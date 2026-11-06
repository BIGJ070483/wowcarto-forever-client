<?php
declare(strict_types=1);
require_once __DIR__ . '/service.php';

/** Reject records that the integrated client cannot decode before publishing them. */
function wcf_community_validate_client_rows(array $rows): void
{
    foreach ($rows as $row) {
        $fields=explode('|',$row);
        foreach ([2,3,4,5,6,7] as $index) {
            $value=$fields[$index] ?? '';
            if (!preg_match('/\A[0-9]{1,10}\z/D',$value) || (int)$value>65535) {
                throw new WcfHttpError(400,'Coordonnées ou niveaux de contribution non reconnus.');
            }
        }
    }
}

/** Anonymous, immediately visible imports. Raw files and duplicate rows survive. */
function wcf_community_import(PDO $db, string $data, array $limits): array
{
    $rows = wcf_parse_native($data, $limits['bytes'], $limits['records']);
    wcf_community_validate_client_rows($rows);
    $digest = hash('sha256', $data, true);
    $existing = static fn() => wcf_query($db, 'SELECT id,record_count FROM wcf_community_imports WHERE sha256=?', [$digest])->fetch();
    $db->beginTransaction();
    try {
        $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
        // The schema row serializes the global quota across anonymous uploads.
        $lock = wcf_query($db, 'SELECT version FROM wcf_schema_versions WHERE version=3' . ($mysql ? ' FOR UPDATE' : ''))->fetch();
        if (!$lock) { throw new WcfHttpError(503, 'Communauté en préparation.'); }
        $found = $existing();
        if ($found) { $db->commit(); return ['id'=>(int)$found['id'], 'records'=>(int)$found['record_count'], 'inserted'=>false]; }
        $paged=(int)$db->query('SELECT MAX(version) FROM wcf_schema_versions')->fetchColumn()>=5;
        $usage = $db->query('SELECT COUNT(*) AS files,COALESCE(SUM(LENGTH(raw_file)),0) AS bytes FROM wcf_community_imports')->fetch();
        if ((int)$usage['files'] >= ($paged?10000:2000) || (int)$usage['bytes'] + strlen($data) > ($paged?32:8) * 1024 * 1024) {
            throw new WcfHttpError(413, 'Capacité de synchronisation communautaire atteinte.');
        }
        $revisionBytes = (int)$db->query('SELECT COALESCE(SUM(LENGTH(raw_record)),0) FROM wcf_community_revisions')->fetchColumn();
        $incomingBytes = array_sum(array_map('strlen', $rows));
        $current = $db->query('SELECT COUNT(*) AS records,COALESCE(SUM(LENGTH(raw_record)),0) AS bytes FROM wcf_community_annotations')->fetch();
        $records = (int)$current['records'] + count($rows);
        if ($records > ($paged?100000:2000) || (int)$current['bytes'] + $incomingBytes + 2 * $records + 64 > ($paged?32:4) * 1024 * 1024) {
            throw new WcfHttpError(413, 'Capacité de synchronisation communautaire atteinte.');
        }
        if ($revisionBytes + $incomingBytes > 100 * 1024 * 1024) {
            throw new WcfHttpError(413, 'Historique communautaire temporairement plein.');
        }
        wcf_query($db, 'INSERT INTO wcf_community_imports(sha256,raw_file,record_count) VALUES (?,?,?)', [$digest,$data,count($rows)]);
        $id = (int)$db->lastInsertId();
        foreach ($rows as $i => $row) {
            wcf_query($db, 'INSERT INTO wcf_community_annotations(import_id,ordinal,revision,raw_record) VALUES (?,?,1,?)', [$id,$i+1,$row]);
            $annotation = (int)$db->lastInsertId();
            wcf_query($db, 'INSERT INTO wcf_community_revisions(annotation_id,revision,raw_record) VALUES (?,1,?)', [$annotation,$row]);
        }
        if ($paged) $db->exec('UPDATE wcf_community_generation SET generation=generation+1 WHERE id=1');
        $db->commit();
        return ['id'=>$id,'records'=>count($rows),'inserted'=>true];
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        if ($error instanceof PDOException && $error->getCode() === '23000' && ($found=$existing())) {
            return ['id'=>(int)$found['id'],'records'=>(int)$found['record_count'],'inserted'=>false];
        }
        throw $error;
    }
}

/** Each correction is an immutable revision; stale changes are refused. */
function wcf_community_edit(PDO $db, int $id, int $revision, string $raw): int
{
    if ($id < 1 || $revision < 1 || $revision >= 100 || str_contains($raw, "\r") || str_contains($raw, "\n")) {
        throw new WcfHttpError(400, 'Annotation invalide.');
    }
    wcf_community_validate_client_rows(wcf_parse_native("#v001-id=1-fixture\r\n#1204-ab12\r\n" . $raw . "\r\n", 65536+100, 1));
    $db->beginTransaction();
    try {
        $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
        $lock = wcf_query($db, 'SELECT version FROM wcf_schema_versions WHERE version=3' . ($mysql ? ' FOR UPDATE' : ''))->fetch();
        if (!$lock) { throw new WcfHttpError(503, 'Communauté en préparation.'); }
        $current = wcf_query($db, 'SELECT revision,raw_record FROM wcf_community_annotations WHERE id=?' . ($mysql ? ' FOR UPDATE' : ''), [$id])->fetch();
        if (!$current) { throw new WcfHttpError(404, 'Annotation introuvable.'); }
        if ((int)$current['revision'] !== $revision) { throw new WcfHttpError(409, 'Annotation modifiée entre-temps.'); }
        $snapshotSize = $db->query('SELECT COUNT(*) AS records,COALESCE(SUM(LENGTH(raw_record)),0) AS bytes FROM wcf_community_annotations')->fetch();
        $paged=(int)$db->query('SELECT MAX(version) FROM wcf_schema_versions')->fetchColumn()>=5;
        if ((int)$snapshotSize['bytes'] - strlen($current['raw_record']) + strlen($raw) + 2 * (int)$snapshotSize['records'] + 64 > ($paged?32:4) * 1024 * 1024) {
            throw new WcfHttpError(413, 'Capacité de synchronisation communautaire atteinte.');
        }
        $revisionBytes = (int)$db->query('SELECT COALESCE(SUM(LENGTH(raw_record)),0) FROM wcf_community_revisions')->fetchColumn();
        if ($revisionBytes + strlen($raw) > 100 * 1024 * 1024) { throw new WcfHttpError(413, 'Historique communautaire temporairement plein.'); }
        $next = $revision + 1;
        wcf_query($db, 'INSERT INTO wcf_community_revisions(annotation_id,revision,raw_record) VALUES (?,?,?)', [$id,$next,$raw]);
        $changed = wcf_query($db, 'UPDATE wcf_community_annotations SET revision=?,raw_record=? WHERE id=? AND revision=?', [$next,$raw,$id,$revision]);
        if ($changed->rowCount() !== 1) { throw new WcfHttpError(409, 'Annotation modifiée entre-temps.'); }
        if ($paged) $db->exec('UPDATE wcf_community_generation SET generation=generation+1 WHERE id=1');
        $db->commit();
        return $next;
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        throw $error;
    }
}

/** Bounded pages with a generation guard; every returned row is checked against its source and history. */
function wcf_community_page(PDO $db, int $after, ?int $expected): array
{
    if ($after<0 || ($expected!==null && $expected<0)) throw new WcfHttpError(400,'Position invalide.');
    $db->beginTransaction();
    try {
        $mysql=$db->getAttribute(PDO::ATTR_DRIVER_NAME)==='mysql';
        $lock=wcf_query($db,'SELECT version FROM wcf_schema_versions WHERE version=3'.($mysql?' FOR UPDATE':''))->fetch();
        if (!$lock) throw new WcfHttpError(503,'Communauté en préparation.');
        $generation=(int)$db->query('SELECT generation FROM wcf_community_generation WHERE id=1')->fetchColumn();
        if ($expected!==null && $expected!==$generation) throw new WcfHttpError(409,'Données modifiées pendant la synchronisation.');
        $counts=$db->query('SELECT (SELECT COUNT(*) FROM wcf_community_annotations) AS records,(SELECT COALESCE(SUM(record_count),0) FROM wcf_community_imports) AS expected')->fetch();
        if ((int)$counts['records']!==(int)$counts['expected']) throw new WcfHttpError(503,'Historique de contribution incohérent.');
        $rows=wcf_query($db,'SELECT a.id,a.import_id,a.ordinal,a.revision,a.raw_record,r.raw_record AS revision_record FROM wcf_community_annotations a LEFT JOIN wcf_community_revisions r ON r.annotation_id=a.id AND r.revision=a.revision WHERE a.id>? ORDER BY a.id LIMIT 101',[$after])->fetchAll();
        $items=[]; $sources=[]; $bytes=0; $sourceBytes=0;
        foreach ($rows as $row) {
            if (count($items)>=100 || ($items && $bytes+strlen($row['raw_record'])>512*1024)) break;
            $id=(int)$row['import_id'];
            if (!isset($sources[$id])) {
                $source=wcf_query($db,'SELECT sha256,raw_file,record_count FROM wcf_community_imports WHERE id=?',[$id])->fetch();
                if (!$source || !hash_equals($source['sha256'],hash('sha256',$source['raw_file'],true))) throw new WcfHttpError(503,'Intégrité de contribution incorrecte.');
                $size=strlen($source['raw_file']);
                if ($items && $sourceBytes+$size>8*1024*1024) break;
                $sourceBytes+=$size;
                try { $parsed=wcf_parse_native($source['raw_file']); }
                catch (InvalidArgumentException $e) { throw new WcfHttpError(503,'Contribution communautaire incohérente.'); }
                if (count($parsed)!==(int)$source['record_count']) throw new WcfHttpError(503,'Contribution communautaire incohérente.');
                $sources[$id]=$parsed;
            }
            $ordinal=(int)$row['ordinal'];
            $initial=(int)$row['revision']===1?$row['revision_record']:wcf_query($db,'SELECT raw_record FROM wcf_community_revisions WHERE annotation_id=? AND revision=1',[(int)$row['id']])->fetchColumn();
            if (!isset($sources[$id][$ordinal-1]) || $initial!==$sources[$id][$ordinal-1] ||
                $row['revision_record']===null || $row['raw_record']!==$row['revision_record']) throw new WcfHttpError(503,'Historique de contribution incohérent.');
            try { wcf_parse_native("#v001-id=1-WCF\r\n#1204-ab12\r\n".$row['raw_record']."\r\n",3*65536+6,1); }
            catch (InvalidArgumentException $e) { throw new WcfHttpError(503,'Annotation communautaire incohérente.'); }
            $bytes+=strlen($row['raw_record']);
            $items[]=['id'=>(int)$row['id'],'import_id'=>$id,'ordinal'=>$ordinal,'revision'=>(int)$row['revision'],'raw_base64'=>base64_encode($row['raw_record'])];
        }
        $more=count($items)<count($rows);
        if ($more && !$items) throw new WcfHttpError(503,'Page communautaire invalide.');
        $db->commit(); return ['generation'=>$generation,'items'=>$items,'has_more'=>$more];
    } catch(Throwable $e) { if ($db->inTransaction()) $db->rollBack(); throw $e; }
}

/** Consistent bounded snapshot; clients must validate native compatibility. */
function wcf_community_snapshot(PDO $db): array
{
    $db->beginTransaction();
    try {
        $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
        $lock = wcf_query($db, 'SELECT version FROM wcf_schema_versions WHERE version=3' . ($mysql ? ' FOR UPDATE' : ''))->fetch();
        if (!$lock) { throw new WcfHttpError(503, 'Communauté en préparation.'); }
        $sourceSize = $db->query('SELECT COUNT(*) AS files,COALESCE(SUM(LENGTH(raw_file)),0) AS bytes FROM wcf_community_imports')->fetch();
        if ((int)$sourceSize['files'] > 2000 || (int)$sourceSize['bytes'] > 8 * 1024 * 1024) {
            throw new WcfHttpError(413, 'Instantané communautaire trop volumineux.');
        }
        if ((int)$sourceSize['files'] === 0) {
            if ((int)$db->query('SELECT COUNT(*) FROM wcf_community_annotations')->fetchColumn() !== 0) {
                throw new WcfHttpError(503, 'Historique de contribution incohérent.');
            }
            $db->commit();
            return ['header_source'=>null, 'headers_base64'=>null, 'items'=>[]];
        }
        $sources = []; $expectedRows = 0;
        foreach ($db->query('SELECT id,sha256,raw_file,record_count FROM wcf_community_imports ORDER BY id') as $source) {
            if (!hash_equals($source['sha256'], hash('sha256', $source['raw_file'], true))) {
                throw new WcfHttpError(503, 'Intégrité de la contribution incorrecte.');
            }
            try { $originalRows = wcf_parse_native($source['raw_file']); }
            catch (InvalidArgumentException $error) { throw new WcfHttpError(503, 'Contribution communautaire incohérente.'); }
            if (count($originalRows) !== (int)$source['record_count']) {
                throw new WcfHttpError(503, 'Historique de contribution incohérent.');
            }
            $expectedRows += count($originalRows);
            $sources[(int)$source['id']] = $originalRows;
        }
        // The historical header can contain a contributor's pseudonym. This
        // neutral candidate was accepted in a bounded native lab experiment;
        // its full native semantics are not established yet.
        $headers = "#v001-id=1-WCF\r\n#1204-ab12";
        // Check the size before PDO's potentially buffered result allocation.
        $size = $db->query('SELECT COUNT(*) AS records,COALESCE(SUM(LENGTH(raw_record)),0) AS bytes FROM wcf_community_annotations')->fetch();
        if ((int)$size['records'] !== $expectedRows) { throw new WcfHttpError(503, 'Historique de contribution incohérent.'); }
        if ((int)$size['records'] > 2000 || (int)$size['bytes'] + 2 * (int)$size['records'] + strlen($headers) > 4 * 1024 * 1024) {
            throw new WcfHttpError(413, 'Instantané communautaire trop volumineux.');
        }
        $rows = $db->query('SELECT a.id,a.import_id,a.ordinal,a.revision,a.raw_record,r.raw_record AS revision_record,i.raw_record AS initial_record FROM wcf_community_annotations a LEFT JOIN wcf_community_revisions r ON r.annotation_id=a.id AND r.revision=a.revision LEFT JOIN wcf_community_revisions i ON i.annotation_id=a.id AND i.revision=1 ORDER BY a.id LIMIT 2001');
        $items = []; $bytes = strlen($headers);
        while ($row = $rows->fetch()) {
            if (count($items) >= 2000) { throw new WcfHttpError(413, 'Instantané communautaire trop volumineux.'); }
            $sourceId = (int)$row['import_id']; $ordinal = (int)$row['ordinal'];
            if (!isset($sources[$sourceId][$ordinal - 1]) || $row['initial_record'] !== $sources[$sourceId][$ordinal - 1] ||
                $row['revision_record'] === null || $row['raw_record'] !== $row['revision_record']) {
                throw new WcfHttpError(503, 'Historique de contribution incohérent.');
            }
            $bytes += strlen($row['raw_record']) + 2;
            if ($bytes > 4 * 1024 * 1024) { throw new WcfHttpError(413, 'Instantané communautaire trop volumineux.'); }
            try { wcf_parse_native($headers . "\r\n" . $row['raw_record'] . "\r\n", 3 * 65536 + 6, 1); }
            catch (InvalidArgumentException $error) { throw new WcfHttpError(503, 'Annotation communautaire incohérente.'); }
            $items[] = ['id'=>(int)$row['id'], 'import_id'=>(int)$row['import_id'],
                        'ordinal'=>(int)$row['ordinal'], 'revision'=>(int)$row['revision'],
                        'raw_base64'=>base64_encode($row['raw_record'])];
        }
        $db->commit();
        return ['header_source'=>'wcf-neutral-v1', 'headers_base64'=>base64_encode($headers), 'items'=>$items];
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        throw $error;
    }
}

/** Produce a bounded one-source research candidate, not a published native update. */
function wcf_community_candidate(PDO $db, int $id): array
{
    if ($id < 1) { throw new WcfHttpError(400, 'Contribution invalide.'); }
    $db->beginTransaction();
    try {
        $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
        $lock = wcf_query($db, 'SELECT version FROM wcf_schema_versions WHERE version=3' . ($mysql ? ' FOR UPDATE' : ''))->fetch();
        if (!$lock) { throw new WcfHttpError(503, 'Communauté en préparation.'); }
        $source = wcf_query($db, 'SELECT sha256,raw_file,record_count FROM wcf_community_imports WHERE id=?', [$id])->fetch();
        if (!$source) { throw new WcfHttpError(404, 'Contribution introuvable.'); }
        $original = $source['raw_file'];
        if (!hash_equals($source['sha256'], hash('sha256', $original, true))) { throw new WcfHttpError(503, 'Intégrité de la contribution incorrecte.'); }
        $parsed = wcf_parse_native($original);
        $rows = wcf_query($db, 'SELECT a.ordinal,a.revision,a.raw_record,r.raw_record AS initial_record,c.raw_record AS current_record FROM wcf_community_annotations a JOIN wcf_community_revisions r ON r.annotation_id=a.id AND r.revision=1 JOIN wcf_community_revisions c ON c.annotation_id=a.id AND c.revision=a.revision WHERE a.import_id=? ORDER BY a.ordinal', [$id])->fetchAll();
        if (count($rows) !== count($parsed) || count($rows) !== (int)$source['record_count']) { throw new WcfHttpError(503, 'Historique de contribution incomplet.'); }
        // Captured separators preserve CRLF/LF/CR and a missing final newline.
        $parts = preg_split('/(\r\n|\n|\r)/', $original, -1, PREG_SPLIT_DELIM_CAPTURE);
        $candidateBytes = strlen($original);
        foreach ($rows as $i => $row) {
            if ((int)$row['ordinal'] !== $i + 1 || $row['initial_record'] !== $parsed[$i] || $row['raw_record'] !== $row['current_record']) { throw new WcfHttpError(503, 'Historique de contribution incohérent.'); }
            $candidateBytes += strlen($row['raw_record']) - strlen($parsed[$i]);
            if ($candidateBytes > 8 * 1024 * 1024) { throw new WcfHttpError(413, 'Contribution corrigée trop volumineuse.'); }
            $parts[4 + $i * 2] = $row['raw_record'];
        }
        $candidate = implode('', $parts);
        wcf_parse_native($candidate);
        $db->commit();
        return ['native_update'=>false, 'source_sha256'=>bin2hex($source['sha256']), 'sha256'=>hash('sha256', $candidate), 'records'=>count($rows), 'raw_base64'=>base64_encode($candidate)];
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        throw $error;
    }
}
