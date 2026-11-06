<?php
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");
header('Referrer-Policy: no-referrer');
?><!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Communauté · WoWCarto Forever</title><link rel="stylesheet" href="style.css"><script src="app.js" defer></script></head>
<body><main><p class="eyebrow">WoWCarto Forever</p><h1>Partager et corriger des annotations</h1>
<p>Ajoute les observations enregistrées dans WoWCartographe ou corrige une annotation existante. Aucun compte n'est nécessaire.</p>
<p>Les annotations partagées ne sont pas encore synchronisées dans WoWCartographe.</p>
<p id="status" role="status" aria-live="polite">Connexion au service…</p>
<section id="community" hidden><h2>Partager un fichier d'ajouts</h2>
<p>Le fichier complet et ses commentaires seront visibles par la communauté. Vérifie son contenu avant l'envoi.</p>
<form id="upload"><label for="file">Fichier d'ajouts (.txt)</label><input id="file" name="file" type="file" accept=".txt" required>
<p id="limits" class="hint"></p><button type="submit">Partager les annotations</button></form>
<h2>Annotations partagées</h2><p>Tu peux corriger le nom et le premier commentaire. Les autres champs sont conservés.</p>
<div id="items"></div><nav><button id="previous" type="button" hidden>Page précédente</button><button id="next" type="button" hidden>Page suivante</button></nav>
</section></main></body></html>
