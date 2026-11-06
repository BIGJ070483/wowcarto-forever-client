<?php
// Concurrent fixture worker. Never accepts hosting or a non-local DSN.
declare(strict_types=1);
require __DIR__ . '/../hosting/web/private/service.php';
require __DIR__ . '/../hosting/web/private/community.php';
$dsn = getenv('WCF_MYSQL_TEST_DSN') ?: '';
if (!preg_match('/\Amysql:host=127\.0\.0\.1;port=[0-9]+;dbname=wcf_fixture_[a-z0-9_]+;charset=utf8mb4\z/D', $dsn)) {
    throw new RuntimeException('Only a loopback fixture is accepted');
}
$spec = json_decode($argv[1], true, 16, JSON_THROW_ON_ERROR);
$db = wcf_db(['dsn' => $dsn, 'username' => getenv('WCF_MYSQL_TEST_USER'),
    'password' => getenv('WCF_MYSQL_TEST_PASSWORD')]);
$delay = min(2.0, max(0.0, (float)$spec['start'] - microtime(true)));
usleep((int)($delay * 1000000));
try {
    if ($spec['action'] === 'import') {
        $result = wcf_import($db, (int)$spec['member'], base64_decode($spec['data'], true),
            ['bytes' => 1048576, 'records' => 2000]);
        echo json_encode(['status' => $result['inserted'] ? 201 : 200, 'id' => $result['id']], JSON_THROW_ON_ERROR);
    } elseif ($spec['action'] === 'community_import') {
        $result = wcf_community_import($db, base64_decode($spec['data'], true),
            ['bytes' => 1048576, 'records' => 2000]);
        echo json_encode(['status' => $result['inserted'] ? 201 : 200, 'id' => $result['id']], JSON_THROW_ON_ERROR);
    } elseif ($spec['action'] === 'community_snapshot') {
        $snapshot = wcf_community_snapshot($db);
        echo json_encode(['status' => 200, 'items' => $snapshot['items']], JSON_THROW_ON_ERROR);
    } elseif ($spec['action'] === 'community_edit') {
        $revision = wcf_community_edit($db, (int)$spec['annotation'], (int)$spec['revision'], base64_decode($spec['data'], true));
        echo json_encode(['status' => 200, 'revision' => $revision], JSON_THROW_ON_ERROR);
    } elseif ($spec['action'] === 'review') {
        wcf_review_decide($db, (int)$spec['member'], (int)$spec['annotation'], 'accepted', 'fixture');
        echo '{"status":200}';
    } else { throw new RuntimeException('Unknown fixture operation'); }
} catch (WcfHttpError $error) {
    echo json_encode(['status' => $error->status], JSON_THROW_ON_ERROR);
}
