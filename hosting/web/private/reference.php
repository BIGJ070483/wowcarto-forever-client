<?php
declare(strict_types=1);
require_once __DIR__ . '/service.php';

function wcf_reference_sources(): array
{
    return [
        'Francais' => ['f1838d4946221d610fb7f54241bb574670dadb3da4b1bf1398821b640b7bb6b5', '77410e6a3bbcf354ff8e90b4a483d68de85de7360102989c08d87cd4bd18eb07'],
        'US' => ['58ae7d6ffd4f3e6a16ef27dc696b2f9c754a1752a1ae774f30ccf7d02637761e', 'd242bb5ff3342a4b1d7b86dcc7010d0dd9712f3ec13c639b746c6f69289aaeec'],
    ];
}

function wcf_reference_validate(string $locale, string $sha, ?int $nativeId=null): void
{
    $sources=wcf_reference_sources();
    if (!isset($sources[$locale]) || !hash_equals($sources[$locale][0],$sha)) throw new WcfHttpError(400,'Base de référence incompatible.');
    if ($nativeId===null) return;
    if ($nativeId<1 || $nativeId>1000000) throw new WcfHttpError(400,'Annotation de référence invalide.');
    $bits=@file_get_contents(__DIR__.'/reference-'.$locale.'.bits');
    if ($bits===false || !hash_equals($sources[$locale][1],hash('sha256',$bits))) throw new WcfHttpError(503,'Index de référence indisponible.');
    $byte=intdiv($nativeId,8);
    if ($byte>=strlen($bits) || !(ord($bits[$byte]) & (1 << ($nativeId % 8)))) throw new WcfHttpError(404,'Annotation de référence introuvable.');
}

function wcf_reference_page(PDO $db, string $locale, string $sha, int $after, ?int $expected): array
{
    wcf_reference_validate($locale,$sha);
    if ($after<0 || ($expected!==null && $expected<0)) throw new WcfHttpError(400,'Position invalide.');
    $db->beginTransaction();
    try {
        $generation=(int)$db->query('SELECT generation FROM wcf_reference_generation WHERE id=1')->fetchColumn();
        if ($expected!==null && $generation!==$expected) throw new WcfHttpError(409,'Données modifiées pendant la synchronisation.');
        $rows=wcf_query($db,'SELECT native_id,revision,name FROM wcf_reference_edits WHERE locale=? AND native_id>? ORDER BY native_id LIMIT 501',[$locale,$after])->fetchAll();
        $more=count($rows)>500; if ($more) array_pop($rows);
        $items=array_map(static fn($r)=>['id'=>(int)$r['native_id'],'revision'=>(int)$r['revision'],'name'=>$r['name']],$rows);
        $db->commit();
        return ['source_sha256'=>$sha,'generation'=>$generation,'items'=>$items,'has_more'=>$more];
    } catch(Throwable $e) { if ($db->inTransaction()) $db->rollBack(); throw $e; }
}

function wcf_reference_edit(PDO $db, string $locale, string $sha, int $id, int $revision, string $name): int
{
    wcf_reference_validate($locale,$sha,$id);
    if ($revision<0 || $revision>=100 || strlen($name)>800 || trim($name)==='' ||
        preg_match('//u',$name)!==1 || preg_match('/[\x00-\x1f\x7f]/',$name) || preg_match_all('/./us',$name)>200) {
        throw new WcfHttpError(400,'Correction invalide.');
    }
    $db->beginTransaction();
    try {
        $mysql=$db->getAttribute(PDO::ATTR_DRIVER_NAME)==='mysql';
        wcf_query($db,'SELECT generation FROM wcf_reference_generation WHERE id=1'.($mysql?' FOR UPDATE':''))->fetch();
        $current=wcf_query($db,'SELECT revision FROM wcf_reference_edits WHERE locale=? AND native_id=?'.($mysql?' FOR UPDATE':''),[$locale,$id])->fetch();
        $actual=$current?(int)$current['revision']:0;
        if ($actual!==$revision) throw new WcfHttpError(409,'Annotation modifiée entre-temps.');
        $next=$actual+1;
        if ($current) wcf_query($db,'UPDATE wcf_reference_edits SET revision=?,name=? WHERE locale=? AND native_id=?',[$next,$name,$locale,$id]);
        else wcf_query($db,'INSERT INTO wcf_reference_edits(locale,native_id,revision,name) VALUES (?,?,?,?)',[$locale,$id,$next,$name]);
        wcf_query($db,'INSERT INTO wcf_reference_history(locale,native_id,revision,name) VALUES (?,?,?,?)',[$locale,$id,$next,$name]);
        wcf_query($db,'UPDATE wcf_reference_generation SET generation=generation+1 WHERE id=1');
        $db->commit(); return $next;
    } catch(Throwable $e) { if ($db->inTransaction()) $db->rollBack(); throw $e; }
}
