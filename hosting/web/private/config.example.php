<?php
// Copy to config.php on the server only. Never commit real credentials.
return [
    'dsn' => 'mysql:host=SQL_HOST;dbname=SQL_DATABASE;charset=utf8mb4',
    'username' => 'SQL_USER',
    'password' => 'SQL_PASSWORD',
    'public_open' => false,
    'registration_open' => false,
    'require_https' => true,
    'rate_secret' => '', // At least 32 random bytes, hex encoded, generated locally.
    'max_upload_bytes' => 8 * 1024 * 1024,
    'max_records' => 2000,
];
