<?php
declare(strict_types=1);
ini_set('display_errors', '0');
require_once __DIR__ . '/private/service.php';
require_once __DIR__ . '/private/community.php';
require_once __DIR__ . '/private/reference.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; frame-ancestors 'none'");
header('Referrer-Policy: no-referrer');

function wcf_reply(array $body, int $status = 200): never
{
    http_response_code($status);
    echo json_encode($body, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE);
    exit;
}

$stage = 'configuration';
try {
    if (!is_file(__DIR__ . '/private/config.php')) {
        throw new WcfHttpError(503, 'Configuration serveur en attente.');
    }
    $config = require __DIR__ . '/private/config.php';
    if (!is_array($config) || !is_string($config['rate_secret'] ?? null) ||
        strlen($config['rate_secret']) < 64 || !is_bool($config['public_open'] ?? null) ||
        !is_bool($config['registration_open'] ?? null)) {
        throw new WcfHttpError(503, 'Configuration serveur incomplète.');
    }
    $https = !empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off';
    if (($config['require_https'] ?? true) && !$https) {
        throw new WcfHttpError(403, 'HTTPS requis.');
    }
    $stage = 'database';
    $db = wcf_db($config);
    $stage = 'schema';
    $schema = (int)$db->query('SELECT MAX(version) FROM wcf_schema_versions')->fetchColumn();
    if (!in_array($schema, [2, 3, 4, 5], true)) {
        throw new WcfHttpError(503, 'Version de base non prise en charge.');
    }
    $stage = 'limits';
    $limits = wcf_limits($db, $config);
    $action = $_GET['action'] ?? 'bootstrap';
    $method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    if (!is_string($action)) { throw new WcfHttpError(400, 'Action invalide.'); }
    if ($action === 'health' && $method === 'GET') {
        wcf_reply(['ok' => true, 'schema' => $schema, 'open' => (bool)$config['public_open']]);
    }
    if (!in_array($action, ['bootstrap','register','login','logout','upload','imports','review_queue','review_decide','review_export','community_list','community_history','community_snapshot','community_page','community_upload','community_edit','reference_page','reference_edit'], true)) {
        throw new WcfHttpError(404, 'Action inconnue.');
    }
    $expectedMethod = in_array($action, ['bootstrap','imports','review_queue','community_list','community_history','community_snapshot','community_page','reference_page'], true) ? 'GET' : 'POST';
    if ($method !== $expectedMethod) {
        header('Allow: ' . $expectedMethod);
        throw new WcfHttpError(405, 'Méthode non autorisée.');
    }
    // Preview permits only health/bootstrap. No account or upload can be created.
    if (!(bool)$config['public_open'] && $action !== 'bootstrap') {
        throw new WcfHttpError(503, 'Réception des contributions encore fermée.');
    }
    $stage = 'session';
    $sessionDir = __DIR__ . '/private/sessions';
    if (!is_dir($sessionDir) && !mkdir($sessionDir, 0700) && !is_dir($sessionDir)) {
        throw new WcfHttpError(503, 'Session indisponible.');
    }
    ini_set('session.use_strict_mode', '1');
    ini_set('session.use_only_cookies', '1');
    session_save_path($sessionDir);
    session_name('WCFSESSION');
    $cookiePath = rtrim(str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'])), '/') . '/';
    session_set_cookie_params(['lifetime' => 0, 'path' => $cookiePath, 'secure' => $https,
        'httponly' => true, 'samesite' => 'Strict']);
    if (!session_start()) { throw new WcfHttpError(503, 'Session indisponible.'); }
    $now = time();
    if (isset($_SESSION['member_id']) &&
        ($now - ($_SESSION['seen'] ?? 0) > 1800 || $now - ($_SESSION['started'] ?? 0) > 28800)) {
        $_SESSION = [];
        session_regenerate_id(true);
    }
    $_SESSION['seen'] = $now;
    $_SESSION['csrf'] ??= bin2hex(random_bytes(32));
    $member = null;
    if (isset($_SESSION['member_id'])) {
        try { $member = wcf_member($db, (int)$_SESSION['member_id']); }
        catch (WcfHttpError $error) {
            if ($error->status !== 401) { throw $error; }
            $_SESSION = ['csrf' => bin2hex(random_bytes(32)), 'seen' => $now];
            session_regenerate_id(true);
        }
    }
    if ($action === 'bootstrap') {
        wcf_reply(['ok' => true, 'csrf' => $_SESSION['csrf'], 'member' => $member,
            'open' => (bool)$config['public_open'], 'registration_open' => (bool)$config['registration_open'],
            'community_ready' => $schema >= 3, 'reference_ready' => $schema >= 4, 'limits' => $limits]);
    }
    if ($method === 'POST') {
        if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > $limits['bytes'] + 65536) {
            throw new WcfHttpError(413, 'Requête trop volumineuse.');
        }
        $csrf = $_POST['csrf'] ?? null;
        if (!is_string($csrf) || !hash_equals($_SESSION['csrf'], $csrf)) {
            throw new WcfHttpError(403, 'Formulaire expiré. Recharge la page.');
        }
    }
    $stage = 'request';
    $ip = (string)($_SERVER['REMOTE_ADDR'] ?? 'unknown');
    if (str_starts_with($action,'reference_')) {
        if ($schema<4) throw new WcfHttpError(503,'Corrections de référence en préparation.');
        wcf_rate($db,$config,'reference-ip',$ip,$action==='reference_page'?300:20,600);
        $input=$action==='reference_page'?$_GET:$_POST;
        $locale=$input['locale'] ?? null; $sha=$input['source_sha256'] ?? null;
        if (!is_string($locale) || !is_string($sha)) throw new WcfHttpError(400,'Base invalide.');
        if ($action==='reference_page') {
            $after=$input['after'] ?? '0'; $generation=$input['generation'] ?? null;
            if (!is_string($after) || !preg_match('/\A[0-9]{1,7}\z/D',$after) || ($generation!==null && (!is_string($generation) || !preg_match('/\A[0-9]{1,12}\z/D',$generation)))) throw new WcfHttpError(400,'Position invalide.');
            wcf_reply(['ok'=>true,'page'=>wcf_reference_page($db,$locale,$sha,(int)$after,$generation===null?null:(int)$generation)]);
        }
        $id=$input['id'] ?? null; $revision=$input['revision'] ?? null; $name=$input['name'] ?? null;
        if (!is_string($id) || !preg_match('/\A[1-9][0-9]{0,6}\z/D',$id) || !is_string($revision) || !preg_match('/\A(?:0|[1-9][0-9]{0,6})\z/D',$revision) || !is_string($name)) throw new WcfHttpError(400,'Correction invalide.');
        wcf_reply(['ok'=>true,'revision'=>wcf_reference_edit($db,$locale,$sha,(int)$id,(int)$revision,$name)]);
    }
    if (str_starts_with($action, 'community_')) {
        if ($schema < 3) { throw new WcfHttpError(503, 'Communauté en préparation.'); }
        if ($action === 'community_page') {
            if ($schema<5) throw new WcfHttpError(404,'Pagination indisponible.');
            wcf_rate($db,$config,'community-page-ip',$ip,1200,600);
            $after=$_GET['after'] ?? '0'; $generation=$_GET['generation'] ?? null;
            if (!is_string($after) || !preg_match('/\A[0-9]{1,18}\z/D',$after) || ($generation!==null && (!is_string($generation) || !preg_match('/\A[0-9]{1,12}\z/D',$generation)))) throw new WcfHttpError(400,'Position invalide.');
            wcf_reply(['ok'=>true,'page'=>wcf_community_page($db,(int)$after,$generation===null?null:(int)$generation)]);
        }
        if ($action === 'community_snapshot') {
            wcf_rate($db, $config, 'community-read-ip', $ip, 120, 600);
            wcf_rate($db, $config, 'community-snapshot-global', 'all', 60, 600);
            wcf_reply(['ok'=>true,'snapshot'=>wcf_community_snapshot($db)]);
        }
        if ($action === 'community_history') {
            wcf_rate($db, $config, 'community-read-ip', $ip, 120, 600);
            $id = $_GET['id'] ?? null;
            if (!is_string($id) || !preg_match('/\A[1-9][0-9]{0,17}\z/D', $id)) { throw new WcfHttpError(400, 'Annotation invalide.'); }
            $rows = wcf_query($db, 'SELECT revision,raw_record FROM wcf_community_revisions WHERE annotation_id=? ORDER BY revision DESC LIMIT 100', [(int)$id])->fetchAll();
            if (!$rows) { throw new WcfHttpError(404, 'Annotation introuvable.'); }
            wcf_reply(['ok'=>true,'versions'=>array_map(static fn($row) => ['revision'=>(int)$row['revision'],'raw_base64'=>base64_encode($row['raw_record'])], $rows)]);
        }
        if ($action === 'community_list') {
            wcf_rate($db, $config, 'community-read-ip', $ip, 120, 600);
            $after = $_GET['after'] ?? '0';
            if (!is_string($after) || !preg_match('/\A(?:0|[1-9][0-9]{0,17})\z/D', $after)) { throw new WcfHttpError(400, 'Position invalide.'); }
            $rows = wcf_query($db, 'SELECT id,import_id,ordinal,revision,raw_record FROM wcf_community_annotations WHERE id>? ORDER BY id LIMIT 21', [(int)$after])->fetchAll();
            $more = count($rows) > 20; $rows = array_slice($rows, 0, 20);
            $items = array_map(static fn($row) => ['id'=>(int)$row['id'], 'import_id'=>(int)$row['import_id'], 'ordinal'=>(int)$row['ordinal'], 'revision'=>(int)$row['revision'], 'raw_base64'=>base64_encode($row['raw_record'])], $rows);
            wcf_reply(['ok'=>true,'items'=>$items,'has_more'=>$more]);
        }
        wcf_rate($db, $config, 'community-ip', $ip, 20, 600);
        wcf_rate($db, $config, 'community-global', 'all', 300, 600);
        if ($action === 'community_edit') {
            $id = $_POST['id'] ?? null; $revision = $_POST['revision'] ?? null; $encoded = $_POST['record_base64'] ?? null;
            if (!is_string($id) || !preg_match('/\A[1-9][0-9]{0,17}\z/D', $id) || !is_string($revision) || !preg_match('/\A[1-9][0-9]{0,8}\z/D', $revision) || !is_string($encoded) || strlen($encoded) > 90000 || ($raw=base64_decode($encoded, true)) === false) { throw new WcfHttpError(400, 'Modification invalide.'); }
            wcf_reply(['ok'=>true,'revision'=>wcf_community_edit($db,(int)$id,(int)$revision,$raw)]);
        }
        $file = $_FILES['file'] ?? null;
        if (!is_array($file) || ($file['error'] ?? null) !== UPLOAD_ERR_OK || !is_string($file['tmp_name'] ?? null) || !is_uploaded_file($file['tmp_name'])) { throw new WcfHttpError(400, 'Fichier manquant ou transfert refusé.'); }
        $stream = fopen($file['tmp_name'], 'rb');
        if ($stream === false) { throw new WcfHttpError(400, 'Fichier illisible.'); }
        try { $data = stream_get_contents($stream, $limits['bytes'] + 1); } finally { fclose($stream); }
        if ($data === false) { throw new WcfHttpError(400, 'Fichier illisible.'); }
        $result = wcf_community_import($db, $data, $limits);
        wcf_reply(['ok'=>true,'import'=>$result], $result['inserted'] ? 201 : 200);
    }
    if ($action === 'register') {
        if (!(bool)$config['registration_open']) { throw new WcfHttpError(403, 'Inscriptions fermées.'); }
        wcf_rate($db, $config, 'register', $ip, 3, 3600);
        $member = wcf_register($db, $_POST);
    } elseif ($action === 'login') {
        wcf_rate($db, $config, 'login-ip', $ip, 10, 900);
        [$name] = wcf_credentials($_POST);
        wcf_rate($db, $config, 'login-user', strtolower($name), 10, 900);
        $member = wcf_login($db, $_POST);
    }
    if (in_array($action, ['register','login'], true)) {
        session_regenerate_id(true);
        $_SESSION['member_id'] = $member['id'];
        $_SESSION['started'] = $now;
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
        wcf_reply(['ok' => true, 'member' => $member, 'csrf' => $_SESSION['csrf']]);
    }
    if ($action === 'logout') {
        $_SESSION = [];
        session_regenerate_id(true);
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
        wcf_reply(['ok' => true, 'csrf' => $_SESSION['csrf']]);
    }
    if (!$member) { throw new WcfHttpError(401, 'Connexion requise.'); }
    if ($action === 'review_export') {
        $after = $_POST['after'] ?? '0';
        if (!is_string($after) || !preg_match('/\A(?:0|[1-9][0-9]{0,17})\z/D', $after)) {
            throw new WcfHttpError(400, 'Position invalide.');
        }
        wcf_reply(['ok' => true] + wcf_review_export($db, $member['id'], (int)$after));
    }
    if ($action === 'review_queue') {
        $after = $_GET['after'] ?? '0';
        if (!is_string($after) || !preg_match('/\A(?:0|[1-9][0-9]{0,17})\z/D', $after)) {
            throw new WcfHttpError(400, 'Position invalide.');
        }
        wcf_reply(['ok' => true] + wcf_review_queue($db, $member, (int)$after));
    }
    if ($action === 'review_decide') {
        $id = $_POST['id'] ?? null;
        $decision = $_POST['decision'] ?? null;
        $reason = $_POST['reason'] ?? '';
        if (!is_string($id) || !preg_match('/\A[1-9][0-9]{0,17}\z/D', $id) ||
            !is_string($decision) || !is_string($reason)) {
            throw new WcfHttpError(400, 'Décision invalide.');
        }
        wcf_review_decide($db, $member['id'], (int)$id, $decision, $reason);
        wcf_reply(['ok' => true]);
    }
    if ($action === 'imports') {
        $items = wcf_import_history($db, $member['id']);
        wcf_reply(['ok' => true, 'imports' => $items]);
    }
    if ($action === 'upload') {
        wcf_rate($db, $config, 'upload', (string)$member['id'], 10, 600);
        $file = $_FILES['file'] ?? null;
        if (!is_array($file) || !isset($file['error'], $file['tmp_name']) ||
            !is_int($file['error']) || $file['error'] !== UPLOAD_ERR_OK ||
            !is_string($file['tmp_name']) || !is_uploaded_file($file['tmp_name'])) {
            throw new WcfHttpError(400, 'Fichier manquant ou transfert refusé.');
        }
        $stream = fopen($file['tmp_name'], 'rb');
        if ($stream === false) { throw new WcfHttpError(400, 'Fichier illisible.'); }
        try { $data = stream_get_contents($stream, $limits['bytes'] + 1); }
        finally { fclose($stream); }
        if ($data === false) { throw new WcfHttpError(400, 'Fichier illisible.'); }
        $result = wcf_import($db, $member['id'], $data, $limits);
        wcf_reply(['ok' => true, 'import' => $result], $result['inserted'] ? 201 : 200);
    }
    throw new WcfHttpError(404, 'Action inconnue.');
} catch (WcfHttpError $error) {
    if ($error->status === 429) { header('Retry-After: ' . ($error->retryAfter ?? 900)); }
    wcf_reply(['ok' => false, 'error' => $error->getMessage()], $error->status);
} catch (InvalidArgumentException $error) {
    wcf_reply(['ok' => false, 'error' => $error->getMessage()], 400);
} catch (Throwable $error) {
    // SQL messages can contain submitted data. Never echo/log them here.
    error_log('WCF request failed: ' . $stage . ' ' . get_class($error));
    $body = ['ok' => false, 'error' => 'Service temporairement indisponible.'];
    if (($_GET['action'] ?? '') === 'health') {
        $body['stage'] = $stage;
        if ($stage === 'database' && $error instanceof PDOException) {
            $body['driver_code'] = (int)($error->errorInfo[1] ?? 0);
        }
    }
    wcf_reply($body, 503);
}
