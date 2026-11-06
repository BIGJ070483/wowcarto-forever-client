<?php
declare(strict_types=1);
require __DIR__.'/../hosting/web/private/reference.php';
$dsn=getenv('WCF_MYSQL_TEST_DSN') ?: '';
if (!preg_match('/\Amysql:host=127\.0\.0\.1;port=[0-9]+;dbname=wcf_reference_[a-z0-9_]+;charset=utf8mb4\z/D',$dsn)) throw new RuntimeException('Only a dedicated loopback fixture is accepted');
$spec=json_decode($argv[1],true,16,JSON_THROW_ON_ERROR);
$db=wcf_db(['dsn'=>$dsn,'username'=>getenv('WCF_MYSQL_TEST_USER'),'password'=>getenv('WCF_MYSQL_TEST_PASSWORD')]);
usleep((int)(1000000 * min(2,max(0,(float)$spec['start']-microtime(true)))));
try { $revision=wcf_reference_edit($db,'Francais',wcf_reference_sources()['Francais'][0],2,2,$spec['name']); echo json_encode(['status'=>200,'revision'=>$revision]); }
catch (WcfHttpError $e) { echo json_encode(['status'=>$e->status]); }
