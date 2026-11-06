<?php
declare(strict_types=1);
require __DIR__.'/../hosting/web/private/reference.php';
$dsn=getenv('WCF_MYSQL_TEST_DSN') ?: '';
if (!preg_match('/\Amysql:host=127\.0\.0\.1;port=[0-9]+;dbname=wcf_reference_[a-z0-9_]+;charset=utf8mb4\z/D',$dsn)) throw new RuntimeException('Only a dedicated loopback fixture is accepted');
$db=wcf_db(['dsn'=>$dsn,'username'=>getenv('WCF_MYSQL_TEST_USER'),'password'=>getenv('WCF_MYSQL_TEST_PASSWORD')]);
if ((int)$db->query('SELECT COUNT(*) FROM wcf_reference_edits')->fetchColumn()!==0) throw new RuntimeException('Fixture must be empty');
$sha=wcf_reference_sources()['Francais'][0];
if (wcf_reference_edit($db,'Francais',$sha,2,0,'Correction MySQL')!==1) throw new RuntimeException('First revision failed');
try { wcf_reference_edit($db,'Francais',$sha,2,0,'stale'); throw new RuntimeException('Stale revision accepted'); }
catch (WcfHttpError $e) { if ($e->status!==409) throw $e; }
$page=wcf_reference_page($db,'Francais',$sha,0,null);
if ($page['generation']!==1 || count($page['items'])!==1 || $page['items'][0]['name']!=='Correction MySQL') throw new RuntimeException('Page failed');
try { wcf_reference_page($db,'Francais',$sha,0,0); throw new RuntimeException('Stale page accepted'); }
catch (WcfHttpError $e) { if ($e->status!==409) throw $e; }
if (wcf_reference_edit($db,'Francais',$sha,2,1,'Deuxième')!==2) throw new RuntimeException('Second revision failed');
if ((int)$db->query('SELECT COUNT(*) FROM wcf_reference_history')->fetchColumn()!==2) throw new RuntimeException('History failed');
$running=[]; $start=microtime(true)+1;
foreach (['Concurrent A','Concurrent B'] as $name) {
    $cmd=[PHP_BINARY,'-d','extension_dir='.getenv('WCF_PHP_EXT_DIR'),'-d','extension=pdo_mysql',__DIR__.'/hosting_reference_mysql_worker.php',json_encode(['start'=>$start,'name'=>$name])];
    $p=proc_open($cmd,[0=>['pipe','r'],1=>['pipe','w'],2=>['pipe','w']],$pipes);
    if (!is_resource($p)) throw new RuntimeException('Worker failed to start'); fclose($pipes[0]); $running[]=[$p,$pipes];
}
$statuses=[];
foreach ($running as [$p,$pipes]) {
    $out=stream_get_contents($pipes[1]); $err=stream_get_contents($pipes[2]); fclose($pipes[1]); fclose($pipes[2]);
    if (proc_close($p)!==0 || $err!=='') throw new RuntimeException('Worker failed');
    $statuses[]=json_decode($out,true,16,JSON_THROW_ON_ERROR)['status'];
}
sort($statuses); if ($statuses!==[200,409]) throw new RuntimeException('Concurrent edit failed');
echo "PASS MySQL reference: source, revision, conflicts, pagination, concurrent edits and history\n";
