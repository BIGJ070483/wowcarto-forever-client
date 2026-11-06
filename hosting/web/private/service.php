<?php
declare(strict_types=1);
require_once __DIR__ . '/native.php';

final class WcfHttpError extends RuntimeException
{
    public function __construct(public readonly int $status, string $message, public readonly ?int $retryAfter = null)
    {
        parent::__construct($message);
    }
}

function wcf_db(array $config): PDO
{
    $db = new PDO($config['dsn'], $config['username'], $config['password'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'sqlite') {
        $db->exec('PRAGMA foreign_keys = ON');
    }
    return $db;
}

function wcf_query(PDO $db, string $sql, array $args = []): PDOStatement
{
    $statement = $db->prepare($sql);
    $statement->execute($args);
    return $statement;
}

function wcf_limits(PDO $db, array $config): array
{
    $bytes = min(8388608, (int)$config['max_upload_bytes']);
    if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql') {
        $packet = (int)$db->query('SELECT @@max_allowed_packet')->fetchColumn();
        $bytes = min($bytes, max(0, $packet - 65536));
    }
    foreach (['upload_max_filesize', 'post_max_size'] as $setting) {
        $value = trim((string)ini_get($setting));
        $last = strtolower(substr($value, -1));
        $multiplier = match ($last) { 'g' => 1073741824, 'm' => 1048576, 'k' => 1024, default => 1 };
        $limit = (int)$value * $multiplier;
        if ($limit > 0) {
            $bytes = min($bytes, max(0, $limit - 65536));
        }
    }
    if ($bytes < 1024) {
        throw new WcfHttpError(503, 'Limites serveur insuffisantes.');
    }
    return ['bytes' => $bytes, 'records' => min(2000, max(1, (int)$config['max_records']))];
}

/** A bounded fixed-window counter; only HMACs of IP/user values are stored. */
function wcf_rate(PDO $db, array $config, string $scope, string $identity, int $limit, int $window): void
{
    $key = hash_hmac('sha256', $scope . ':' . $identity, $config['rate_secret']);
    $now = time();
    $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
    if (random_int(1, 100) === 1) {
        wcf_query($db, 'DELETE FROM wcf_rate_limits WHERE expires_at < ?', [$now - 86400]);
    }
    $db->beginTransaction();
    try {
        $insert = $mysql
            ? 'INSERT INTO wcf_rate_limits(bucket,hits,expires_at) VALUES (?,0,?) ON DUPLICATE KEY UPDATE bucket=bucket'
            : 'INSERT INTO wcf_rate_limits(bucket,hits,expires_at) VALUES (?,0,?) ON CONFLICT(bucket) DO NOTHING';
        wcf_query($db, $insert, [$key, $now + $window]);
        $row = wcf_query($db, 'SELECT hits,expires_at FROM wcf_rate_limits WHERE bucket=?' . ($mysql ? ' FOR UPDATE' : ''), [$key])->fetch();
        $hits = (int)$row['expires_at'] <= $now ? 0 : (int)$row['hits'];
        $expires = (int)$row['expires_at'] <= $now ? $now + $window : (int)$row['expires_at'];
        if ($hits >= $limit) {
            $db->commit();
            throw new WcfHttpError(429, 'Trop de tentatives. Réessaie plus tard.', max(1, $expires - $now));
        }
        wcf_query($db, 'UPDATE wcf_rate_limits SET hits=?,expires_at=? WHERE bucket=?', [$hits + 1, $expires, $key]);
        $db->commit();
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        throw $error;
    }
}

function wcf_credentials(array $input): array
{
    $name = $input['username'] ?? null;
    $password = $input['password'] ?? null;
    if (!is_string($name) || !preg_match('/\A[A-Za-z0-9_.-]{3,64}\z/D', $name) ||
        !is_string($password) || strlen($password) < 12 || strlen($password) > 72 || str_contains($password, "\0")) {
        throw new WcfHttpError(400, 'Identifiant : 3 à 64 caractères simples. Mot de passe : 12 à 72 octets.');
    }
    return [$name, $password];
}

function wcf_register(PDO $db, array $input): array
{
    [$name, $password] = wcf_credentials($input);
    $hash = password_hash($password, PASSWORD_BCRYPT, ['cost' => 12]);
    try {
        wcf_query($db, "INSERT INTO wcf_members(username,password_hash,role,enabled) VALUES (?,?,'member',1)", [$name, $hash]);
    } catch (PDOException $error) {
        if ($error->getCode() === '23000') {
            throw new WcfHttpError(409, 'Cet identifiant est indisponible.');
        }
        throw $error;
    }
    return ['id' => (int)$db->lastInsertId(), 'username' => $name, 'role' => 'member'];
}

function wcf_login(PDO $db, array $input): array
{
    [$name, $password] = wcf_credentials($input);
    $row = wcf_query($db, 'SELECT id,username,password_hash,role,enabled FROM wcf_members WHERE username=?', [$name])->fetch();
    // A fixed valid dummy hash also runs bcrypt for nonexistent users.
    $dummy = '$2y$12$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2uheWG/igi.';
    $valid = password_verify($password, $row ? $row['password_hash'] : $dummy);
    if (!$row || !$valid || !(int)$row['enabled']) {
        throw new WcfHttpError(401, 'Connexion refusée.');
    }
    if (password_needs_rehash($row['password_hash'], PASSWORD_BCRYPT, ['cost' => 12])) {
        wcf_query($db, 'UPDATE wcf_members SET password_hash=? WHERE id=?', [password_hash($password, PASSWORD_BCRYPT, ['cost' => 12]), $row['id']]);
    }
    return ['id' => (int)$row['id'], 'username' => $row['username'], 'role' => $row['role']];
}

function wcf_member(PDO $db, int $id): array
{
    $row = wcf_query($db, 'SELECT id,username,role FROM wcf_members WHERE id=? AND enabled=1', [$id])->fetch();
    if (!$row) { throw new WcfHttpError(401, 'Connexion requise.'); }
    $row['id'] = (int)$row['id'];
    return $row;
}

/** Atomic staging; duplicate files per member return the original import. */
function wcf_import(PDO $db, int $member, string $data, array $limits): array
{
    $records = wcf_parse_native($data, $limits['bytes'], $limits['records']);
    $digest = hash('sha256', $data, true);
    $existing = static fn() => wcf_query($db, 'SELECT id,record_count FROM wcf_imports WHERE member_id=? AND sha256=?', [$member, $digest])->fetch();
    $db->beginTransaction();
    try {
        $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
        $owner = wcf_query($db, 'SELECT id FROM wcf_members WHERE id=? AND enabled=1' . ($mysql ? ' FOR UPDATE' : ''), [$member])->fetch();
        if (!$owner) { throw new WcfHttpError(401, 'Connexion requise.'); }
        // Serialize all imports for this owner before checking idempotency and quota.
        // A concurrent retry at the quota boundary must return its original import.
        $found = $existing();
        if ($found) {
            $db->commit();
            return ['id' => (int)$found['id'], 'records' => (int)$found['record_count'], 'inserted' => false];
        }
        $quota = wcf_query($db, 'SELECT COUNT(*) AS files,COALESCE(SUM(LENGTH(raw_file)),0) AS bytes FROM wcf_imports WHERE member_id=?', [$member])->fetch();
        if ((int)$quota['files'] >= 100 || (int)$quota['bytes'] + strlen($data) > 50 * 1024 * 1024) {
            throw new WcfHttpError(413, 'Quota de contributions atteint.');
        }
        wcf_query($db, 'INSERT INTO wcf_imports(member_id,sha256,raw_file,record_count,native_format) VALUES (?,?,?,?,?)', [$member, $digest, $data, count($records), 'v001']);
        $id = (int)$db->lastInsertId();
        $statement = $db->prepare('INSERT INTO wcf_annotations(import_id,ordinal,raw_record) VALUES (?,?,?)');
        foreach ($records as $number => $record) {
            $statement->execute([$id, $number + 1, $record]);
        }
        $db->commit();
        return ['id' => $id, 'records' => count($records), 'inserted' => true];
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        throw $error;
    }
}

/** Summaries only for the authenticated owner's most recent imports. */
function wcf_import_history(PDO $db, int $member): array
{
    $items = wcf_query($db, 'SELECT id,record_count,created_at FROM wcf_imports WHERE member_id=? ORDER BY id DESC LIMIT 100', [$member])->fetchAll();
    if (!$items) { return []; }
    $ids = array_map(static fn(array $row): int => (int)$row['id'], $items);
    $marks = implode(',', array_fill(0, count($ids), '?'));
    $counts = wcf_query($db, "SELECT import_id,review_state,COUNT(*) AS total FROM wcf_annotations WHERE import_id IN ($marks) GROUP BY import_id,review_state", $ids)->fetchAll();
    $byImport = [];
    foreach ($counts as $row) {
        $byImport[(int)$row['import_id']][$row['review_state']] = (int)$row['total'];
    }
    foreach ($items as &$item) {
        $item['review'] = array_merge(['pending' => 0, 'accepted' => 0, 'rejected' => 0], $byImport[(int)$item['id']] ?? []);
    }
    unset($item);
    return $items;
}

function wcf_require_reviewer(array $member): void
{
    if (!in_array($member['role'] ?? '', ['moderator', 'admin'], true)) {
        throw new WcfHttpError(403, 'Accès réservé à la modération.');
    }
}

function wcf_review_queue(PDO $db, array $member, int $after): array
{
    wcf_require_reviewer($member);
    $items = wcf_query($db, "SELECT id,import_id,ordinal,raw_record FROM wcf_annotations WHERE review_state='pending' AND id>? ORDER BY id ASC LIMIT 6", [$after])->fetchAll();
    $hasMore = count($items) > 5;
    $items = array_slice($items, 0, 5);
    return ['has_more' => $hasMore, 'items' => array_map(static fn(array $row): array => [
        'id' => (int)$row['id'], 'import_id' => (int)$row['import_id'],
        'ordinal' => (int)$row['ordinal'], 'record_base64' => base64_encode($row['raw_record']),
    ], $items)];
}

/** Commit decision and audit together, refusing stale decisions. */
function wcf_review_decide(PDO $db, int $moderatorId, int $annotationId, string $decision, string $reason): void
{
    if ($annotationId < 1 || !in_array($decision, ['accepted', 'rejected'], true) ||
        strlen($reason) > 500 || str_contains($reason, "\0") || !preg_match('//u', $reason) ||
        ($decision === 'rejected' && trim($reason) === '')) {
        throw new WcfHttpError(400, 'Décision ou motif invalide.');
    }
    $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
    $db->beginTransaction();
    try {
        $member = wcf_query($db, 'SELECT role FROM wcf_members WHERE id=? AND enabled=1' . ($mysql ? ' FOR UPDATE' : ''), [$moderatorId])->fetch();
        if (!$member) { throw new WcfHttpError(401, 'Connexion requise.'); }
        wcf_require_reviewer($member);
        $row = wcf_query($db, 'SELECT review_state FROM wcf_annotations WHERE id=?' . ($mysql ? ' FOR UPDATE' : ''), [$annotationId])->fetch();
        if (!$row) { throw new WcfHttpError(404, 'Annotation introuvable.'); }
        if ($row['review_state'] !== 'pending') { throw new WcfHttpError(409, 'Annotation déjà traitée.'); }
        $changed = wcf_query($db, "UPDATE wcf_annotations SET review_state=? WHERE id=? AND review_state='pending'", [$decision, $annotationId]);
        if ($changed->rowCount() !== 1) { throw new WcfHttpError(409, 'Annotation déjà traitée.'); }
        wcf_query($db, 'INSERT INTO wcf_moderation_events(annotation_id,moderator_id,decision,reason) VALUES (?,?,?,?)', [$annotationId, $moderatorId, $decision, $reason]);
        $db->commit();
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        throw $error;
    }
}

/** Private, bounded review selection. Never a native update or public endpoint. */
function wcf_review_export(PDO $db, int $reviewerId, int $after): array
{
    $db->beginTransaction();
    try {
        $mysql = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql';
        $member = wcf_query($db, 'SELECT role FROM wcf_members WHERE id=? AND enabled=1' . ($mysql ? ' FOR UPDATE' : ''), [$reviewerId])->fetch();
        if (!$member) { throw new WcfHttpError(401, 'Connexion requise.'); }
        wcf_require_reviewer($member);
        $rows = wcf_query($db, "SELECT id,import_id,ordinal,raw_record FROM wcf_annotations WHERE review_state='accepted' AND id>? ORDER BY id LIMIT 21", [$after])->fetchAll();
        $hasMore = count($rows) > 20;
        $rows = array_slice($rows, 0, 20);
        $records = [];
        // Read each original once; cap resident data by not caching entire native files.
        $sourceId = null; $source = null; $originalRecords = [];
        foreach ($rows as $row) {
            if ($sourceId !== (int)$row['import_id']) {
                $sourceId = (int)$row['import_id'];
                $source = wcf_query($db, 'SELECT raw_file,sha256 FROM wcf_imports WHERE id=?', [$sourceId])->fetch();
                if (!$source || !hash_equals($source['sha256'], hash('sha256', $source['raw_file'], true))) {
                    throw new RuntimeException('Review source integrity failed');
                }
                $originalRecords = wcf_parse_native($source['raw_file']);
            }
            $event = wcf_query($db, 'SELECT id,decision FROM wcf_moderation_events WHERE annotation_id=? ORDER BY id DESC LIMIT 1', [$row['id']])->fetch();
            $ordinal = (int)$row['ordinal'];
            if (!$event || $event['decision'] !== 'accepted' || $ordinal < 1 ||
                !isset($originalRecords[$ordinal - 1]) || $originalRecords[$ordinal - 1] !== $row['raw_record']) {
                throw new RuntimeException('Review selection integrity failed');
            }
            $records[] = ['annotation_id' => (int)$row['id'], 'event_id' => (int)$event['id'],
                'source_sha256' => bin2hex($source['sha256']), 'ordinal' => $ordinal,
                'sha256' => hash('sha256', $row['raw_record']), 'raw_base64' => base64_encode($row['raw_record'])];
        }
        $db->commit();
        return ['format' => 'wcf-server-review-v1', 'native_update' => false,
            'records' => $records, 'has_more' => $hasMore,
            'next_after' => $records ? $records[count($records) - 1]['annotation_id'] : $after];
    } catch (Throwable $error) {
        if ($db->inTransaction()) { $db->rollBack(); }
        throw $error;
    }
}
