<?php
// Disposable loopback-only MySQL fixture. Never use a hosting account here.
declare(strict_types=1);
require __DIR__ . '/../hosting/web/private/service.php';
require __DIR__ . '/../hosting/web/private/community.php';
$dsn = getenv('WCF_MYSQL_TEST_DSN') ?: '';
if (!preg_match('/\Amysql:host=127\.0\.0\.1;port=[0-9]+;dbname=wcf_fixture_[a-z0-9_]+;charset=utf8mb4\z/D', $dsn)) {
    throw new RuntimeException('Only an explicitly named loopback fixture is accepted');
}
$config = ['dsn' => $dsn, 'username' => getenv('WCF_MYSQL_TEST_USER'),
    'password' => getenv('WCF_MYSQL_TEST_PASSWORD'), 'rate_secret' => str_repeat('f', 64)];
$db = wcf_db($config);
if ($db->query('SHOW TABLES')->fetchAll()) {
    throw new RuntimeException('Fixture must be empty; no reset is performed');
}
foreach (['001_initial.sql', '002_rate_limits.sql'] as $file) {
    $db->exec(file_get_contents(__DIR__ . '/../hosting/mysql/' . $file));
}
function check(bool $result, string $message): void {
    if (!$result) { throw new RuntimeException($message); }
}
$input = ['username' => 'fixture_member', 'password' => 'Synthetic-password-42'];
$member = wcf_register($db, $input);
check(wcf_login($db, $input)['id'] === $member['id'], 'Native MySQL authentication failed');
$row = implode('|', ["TEST\xe9", '', '17', '37', '123', '456', '0', '0', '', '', '', '', str_repeat('0',32), '', '', 'fixture', '', 'end']);
$data = "#v001-id=1-fixture\r\n#1204-ab12\r\n$row\r\n$row\r\n";
$limits = ['bytes' => 1048576, 'records' => 2000];
$first = wcf_import($db, $member['id'], $data, $limits);
$second = wcf_import($db, $member['id'], $data, $limits);
check($first['inserted'] && !$second['inserted'] && $first['id'] === $second['id'], 'Idempotency failed');
$saved = wcf_query($db, 'SELECT raw_file,sha256 FROM wcf_imports WHERE id=?', [$first['id']])->fetch();
check($saved['raw_file'] === $data && $saved['sha256'] === hash('sha256',$data,true), 'Raw byte preservation failed');
$rows = wcf_query($db, 'SELECT ordinal,raw_record,review_state FROM wcf_annotations WHERE import_id=? ORDER BY ordinal', [$first['id']])->fetchAll();
check(count($rows) === 2 && (int)$rows[0]['ordinal'] === 1 && (int)$rows[1]['ordinal'] === 2 &&
    $rows[0]['raw_record'] === $row && $rows[1]['raw_record'] === $row && $rows[0]['review_state'] === 'pending', 'Duplicate row preservation failed');
wcf_query($db, "UPDATE wcf_members SET role='moderator' WHERE id=?", [$member['id']]);
$reviewer = wcf_member($db, $member['id']);
$queue = wcf_review_queue($db, $reviewer, 0)['items'];
check(count($queue) === 2 && base64_decode($queue[0]['record_base64'], true) === $row, 'Review queue binary bytes lost');
wcf_review_decide($db, $member['id'], $queue[0]['id'], 'accepted', '');
try {
    wcf_review_decide($db, $member['id'], $queue[0]['id'], 'rejected', 'stale');
    throw new RuntimeException('Stale review was accepted');
} catch (WcfHttpError $expected) { check($expected->status === 409, 'Wrong stale review response'); }
check((int)$db->query('SELECT COUNT(*) FROM wcf_moderation_events')->fetchColumn() === 1, 'Moderation audit missing');
$history = wcf_import_history($db, $member['id']);
check(count($history) === 1 && $history[0]['review'] === ['pending' => 1, 'accepted' => 1, 'rejected' => 0], 'Review counts incorrect');
$db->exec("CREATE TRIGGER wcf_fixture_failure BEFORE INSERT ON wcf_annotations FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic_failure'");
try {
    wcf_import($db, $member['id'], str_replace('fixture', 'different', $data), $limits);
    throw new RuntimeException('Injected failure was ignored');
} catch (PDOException $expected) {
    check(!$db->inTransaction(), 'Transaction was not rolled back');
}
check((int)$db->query('SELECT COUNT(*) FROM wcf_imports')->fetchColumn() === 1, 'Partial import persisted');
wcf_rate($db, $config, 'fixture', 'identity', 2, 60);
wcf_rate($db, $config, 'fixture', 'identity', 2, 60);
try {
    wcf_rate($db, $config, 'fixture', 'identity', 2, 60);
    throw new RuntimeException('Rate limit was ignored');
} catch (WcfHttpError $expected) { check($expected->status === 429, 'Wrong rate response'); }
// Separate PHP processes exercise actual InnoDB locks, not serialized HTTP requests.
$db->exec('DROP TRIGGER wcf_fixture_failure');
function workers(array $specs): array {
    $running = [];
    $start = microtime(true) + 1;
    foreach ($specs as $spec) {
        $spec['start'] = $start;
        $command = [PHP_BINARY];
        if (getenv('WCF_PHP_EXT_DIR')) {
            array_push($command, '-d', 'extension_dir=' . getenv('WCF_PHP_EXT_DIR'), '-d', 'extension=pdo_mysql');
        }
        array_push($command, __DIR__ . '/hosting_mysql_worker.php', json_encode($spec, JSON_THROW_ON_ERROR));
        $process = proc_open($command,
            [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
        if (!is_resource($process)) { throw new RuntimeException('Worker did not start'); }
        fclose($pipes[0]);
        $running[] = [$process, $pipes];
    }
    $results = [];
    foreach ($running as [$process, $pipes]) {
        stream_set_timeout($pipes[1], 20);
        $output = stream_get_contents($pipes[1]);
        $error = stream_get_contents($pipes[2]);
        fclose($pipes[1]); fclose($pipes[2]);
        check(proc_close($process) === 0 && $error === '', 'Concurrent fixture worker failed');
        $results[] = json_decode($output, true, 16, JSON_THROW_ON_ERROR);
    }
    return $results;
}
function nearQuota(PDO $db, string $name): int {
    $member = wcf_register($db, ['username' => $name, 'password' => 'Synthetic-password-42']);
    for ($n = 0; $n < 99; $n++) {
        wcf_query($db, 'INSERT INTO wcf_imports(member_id,sha256,raw_file,record_count,native_format) VALUES (?,?,?,?,?)',
            [$member['id'], hash('sha256', 'quota-' . $n, true), 'fixture', 1, 'v001']);
    }
    return $member['id'];
}
$quotaMember = nearQuota($db, 'fixture_concurrent_retry');
$spec = ['action' => 'import', 'member' => $quotaMember, 'data' => base64_encode($data)];
$results = workers([$spec, $spec]);
$statuses = array_column($results, 'status'); sort($statuses);
check($statuses === [200, 201] && $results[0]['id'] === $results[1]['id'], 'Concurrent retry at quota was not idempotent');
$quotaMember = nearQuota($db, 'fixture_concurrent_quota');
$spec['member'] = $quotaMember;
$different = $spec; $different['data'] = base64_encode(str_replace('TEST', 'OTHER', $data));
$results = workers([$spec, $different]);
$statuses = array_column($results, 'status'); sort($statuses);
check($statuses === [201, 413], 'Concurrent uploads exceeded quota');
check((int)wcf_query($db, 'SELECT COUNT(*) FROM wcf_imports WHERE member_id=?', [$quotaMember])->fetchColumn() === 100, 'Wrong file quota after concurrent writes');
$reviewer2 = wcf_register($db, ['username' => 'fixture_second_reviewer', 'password' => 'Synthetic-password-42']);
wcf_query($db, "UPDATE wcf_members SET role='moderator' WHERE id=?", [$reviewer2['id']]);
$annotation = (int)$queue[1]['id'];
$results = workers([
    ['action' => 'review', 'member' => $member['id'], 'annotation' => $annotation],
    ['action' => 'review', 'member' => $reviewer2['id'], 'annotation' => $annotation],
]);
$statuses = array_column($results, 'status'); sort($statuses);
check($statuses === [200, 409], 'Concurrent moderation did not reject stale decision');
check((int)wcf_query($db, 'SELECT COUNT(*) FROM wcf_moderation_events WHERE annotation_id=?', [$annotation])->fetchColumn() === 1, 'Concurrent review audit was not atomic');
$db->exec(file_get_contents(__DIR__ . '/../hosting/mysql/003_community.sql'));
$community = wcf_community_import($db, $data, $limits);
check($community['inserted'] && $community['records'] === 2, 'Anonymous import failed');
check(!wcf_community_import($db, $data, $limits)['inserted'], 'Anonymous retry was not idempotent');
$entry = wcf_query($db, 'SELECT id,raw_record FROM wcf_community_annotations WHERE import_id=? ORDER BY ordinal LIMIT 1', [$community['id']])->fetch();
$corrected = str_replace('TEST', 'CORRECTED', $entry['raw_record']);
check(wcf_community_edit($db, (int)$entry['id'], 1, $corrected) === 2, 'Anonymous edit failed');
$candidate = wcf_community_candidate($db, $community['id']);
$expectedCandidate = "#v001-id=1-fixture\r\n#1204-ab12\r\n$corrected\r\n$row\r\n";
check($candidate['native_update'] === false && base64_decode($candidate['raw_base64'], true) === $expectedCandidate && $candidate['sha256'] === hash('sha256', $expectedCandidate), 'Anonymous candidate bytes lost');
try {
    wcf_community_edit($db, (int)$entry['id'], 1, $corrected);
    throw new RuntimeException('Stale anonymous edit accepted');
} catch (WcfHttpError $expected) { check($expected->status === 409, 'Wrong anonymous conflict response'); }
$history = wcf_query($db, 'SELECT raw_record FROM wcf_community_revisions WHERE annotation_id=? ORDER BY revision', [$entry['id']])->fetchAll();
check(count($history) === 2 && $history[0]['raw_record'] === $row && $history[1]['raw_record'] === $corrected, 'Anonymous revision bytes lost');
$snapshot = wcf_community_snapshot($db);
check($snapshot['header_source'] === 'wcf-neutral-v1' && count($snapshot['items']) === 2 &&
    base64_decode($snapshot['items'][0]['raw_base64'], true) === $corrected &&
    $snapshot['items'][0]['revision'] === 2 &&
    base64_decode($snapshot['items'][1]['raw_base64'], true) === $row,
    'Community snapshot lost current revisions or binary data');
$raceData = str_replace('TEST', 'RACE', $data);
$raceImport = ['action' => 'community_import', 'data' => base64_encode($raceData)];
$results = workers([$raceImport, $raceImport]);
$statuses = array_column($results, 'status'); sort($statuses);
check($statuses === [200, 201] && $results[0]['id'] === $results[1]['id'], 'Concurrent anonymous imports were not idempotent');
$raceEntry = wcf_query($db, 'SELECT id FROM wcf_community_annotations WHERE import_id=? ORDER BY ordinal LIMIT 1', [$results[0]['id']])->fetch();
$edit = ['action' => 'community_edit', 'annotation' => (int)$raceEntry['id'], 'revision' => 1, 'data' => base64_encode(str_replace('TEST', 'FIRST', $row))];
$otherEdit = $edit; $otherEdit['data'] = base64_encode(str_replace('TEST', 'SECOND', $row));
$results = workers([$edit, $otherEdit]);
$statuses = array_column($results, 'status'); sort($statuses);
check($statuses === [200, 409], 'Concurrent anonymous edits did not reject a stale revision');
check((int)wcf_query($db, 'SELECT COUNT(*) FROM wcf_community_revisions WHERE annotation_id=?', [$raceEntry['id']])->fetchColumn() === 2, 'Concurrent anonymous edits created inconsistent history');
$current = wcf_query($db, 'SELECT revision,raw_record FROM wcf_community_annotations WHERE id=?', [$raceEntry['id']])->fetch();
$nextRow = str_replace('RACE', 'SNAPSHOT', $current['raw_record']);
if ($nextRow === $current['raw_record']) { $nextRow = str_replace('FIRST', 'SNAPSHOT', $current['raw_record']); }
if ($nextRow === $current['raw_record']) { $nextRow = str_replace('SECOND', 'SNAPSHOT', $current['raw_record']); }
check($nextRow !== $current['raw_record'], 'Unable to make snapshot fixture');
$results = workers([
    ['action' => 'community_snapshot'],
    ['action' => 'community_edit', 'annotation' => (int)$raceEntry['id'], 'revision' => (int)$current['revision'], 'data' => base64_encode($nextRow)],
]);
check($results[0]['status'] === 200 && $results[1]['status'] === 200, 'Concurrent snapshot or edit failed');
$matching = array_values(array_filter($results[0]['items'], static fn($item) => $item['id'] === (int)$raceEntry['id']));
check(count($matching) === 1, 'Concurrent snapshot omitted annotation');
$observed = $matching[0];
check(($observed['revision'] === (int)$current['revision'] && base64_decode($observed['raw_base64'], true) === $current['raw_record']) ||
      ($observed['revision'] === (int)$current['revision'] + 1 && base64_decode($observed['raw_base64'], true) === $nextRow),
      'Concurrent snapshot mixed revision and bytes');
echo "PASS MySQL: migrations, authentication, bytes, limits, concurrent review, imports, edits and snapshots\n";
