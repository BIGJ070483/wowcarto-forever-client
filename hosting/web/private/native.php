<?php
declare(strict_types=1);

/** Observed v001 additions only. Byte strings are never converted to UTF-8. */
function wcf_parse_native(string $data, int $maxBytes = 8388608, int $maxRecords = 2000): array
{
    if (strlen($data) > $maxBytes || str_contains($data, "\0")) {
        throw new InvalidArgumentException('Taille ou contenu du fichier non accepté.');
    }
    $lines = preg_split('/\r\n|\n|\r/', $data);
    if (preg_match('/[\r\n]\z/', $data)) {
        array_pop($lines);
    }
    if (count($lines) < 3 || count($lines) - 2 > $maxRecords) {
        throw new InvalidArgumentException('Nombre d’annotations non accepté.');
    }
    foreach ($lines as $line) {
        if (strlen($line) > 65536) {
            throw new InvalidArgumentException('Ligne trop longue.');
        }
    }
    if (!preg_match('/\A#v001-id=[0-9]+-.+\z/D', $lines[0]) ||
        !preg_match('/\A#[0-9]+-[0-9a-fA-F]+\z/D', $lines[1])) {
        throw new InvalidArgumentException('En-têtes natifs v001 non reconnus.');
    }
    $records = array_slice($lines, 2);
    foreach ($records as $record) {
        $fields = explode('|', $record);
        if (count($fields) < 16 || $fields[0] === '' ||
            !preg_match('/\A[0-9]{1,10}\z/D', $fields[3])) {
            throw new InvalidArgumentException('Annotation native non reconnue.');
        }
    }
    return $records;
}
