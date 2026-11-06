# Service communautaire

Service PHP avec stockage MySQL, contrôle des révisions, instantanés paginés
et conservation des fichiers de contribution. Le dossier web est la racine à
servir ; les fichiers dans private doivent être inaccessibles par HTTP.

Utiliser private/config.example.php comme modèle de configuration et exécuter
les migrations MySQL dans l'ordre sur une base sauvegardée. Ne jamais versionner
config.php, sql.php, les sessions ni les sauvegardes SQL réelles.

Les tests PHP et Python utilisent des bases jetables et des annotations
fictives. Ils ne doivent pas viser le service public. Le mode public_open et
l'état du schéma déterminent l'ouverture des contributions ; l'inscription
reste distincte. Le client n'a pas besoin de recevoir les identifiants SQL.

Voir ../docs/BUILD.md, ../docs/STATUS.md et ../CONTRIBUTING.md pour l'état du
client et les limites de validation.
