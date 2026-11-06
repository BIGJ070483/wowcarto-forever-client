<?php
declare(strict_types=1);
require __DIR__.'/../hosting/web/private/community.php';
$dsn=getenv('WCF_MYSQL_TEST_DSN') ?: '';
if (!preg_match('/\Amysql:host=127\.0\.0\.1;port=[0-9]+;dbname=wcf_paging_[a-z0-9_]+;charset=utf8mb4\z/D',$dsn)) throw new RuntimeException('Only a dedicated loopback fixture is accepted');
$db=wcf_db(['dsn'=>$dsn,'username'=>getenv('WCF_MYSQL_TEST_USER'),'password'=>getenv('WCF_MYSQL_TEST_PASSWORD')]);
if ((int)$db->query('SELECT COUNT(*) FROM wcf_community_imports')->fetchColumn()!==0) throw new RuntimeException('Fixture must be empty');
$row=implode('|',['Fixture','','17','37','123','456','0','0','','','','',str_repeat('0',32),'','','comment']);
$header="#v001-id=1-fixture\r\n#1204-ab12\r\n"; $limits=['bytes'=>1048576,'records'=>2000];
$one=$header.str_repeat($row."\r\n",1999); $two=$header.$row."\r\n".$row."\r\n";
if (!wcf_community_import($db,$one,$limits)['inserted'] || !wcf_community_import($db,$two,$limits)['inserted']) throw new RuntimeException('Import failed');
if (wcf_community_import($db,$two,$limits)['inserted']) throw new RuntimeException('Duplicate failed');
$after=0; $gen=null; $count=0;
do { $page=wcf_community_page($db,$after,$gen); $gen=$page['generation']; $count+=count($page['items']); if ($page['items']) $after=end($page['items'])['id']; } while ($page['has_more']);
if ($count!==2001 || $gen!==2) throw new RuntimeException('Pagination incomplete');
$first=wcf_community_page($db,0,null)['items'][0];
if (wcf_community_edit($db,$first['id'],1,str_replace('Fixture','Corrige',$row))!==2) throw new RuntimeException('Correction failed');
try { wcf_community_page($db,0,$gen); throw new RuntimeException('Stale page accepted'); } catch(WcfHttpError $e) { if ($e->status!==409) throw $e; }
echo "PASS MySQL pagination: 2001 records, idempotency, correction and generation conflicts\n";
