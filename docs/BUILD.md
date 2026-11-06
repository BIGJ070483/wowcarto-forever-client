# Développer et compiler

## Environnement

Python 3.11 est la version utilisée pour le paquet Windows local. Tk doit être
disponible. Pillow est la dépendance du client ; PyInstaller sert à construire
l'exécutable. PHP avec PDO SQLite et OpenSSL sont utilisés par les tests réseau.
Les tests du navigateur demandent les dépendances de `tests/requirements-browser.txt`.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Des tests sont ignorés si leurs dépendances optionnelles sont absentes. Pour
exiger les essais PHP, définir `WCF_REQUIRE_PHP_TESTS=1`. `WCF_PHP_BIN` et
`WCF_PHP_EXT_DIR` peuvent désigner une installation PHP de test. Une suite
partiellement ignorée ne doit pas être annoncée comme une recette complète.

## Ressources locales requises

Le dossier `data` contient un catalogue `wcf-clone-catalog-v1`, des bases
référencées avec leurs empreintes, des packs `wcf-map-pack-v1`, ainsi que les
ressources d'interface et aperçus préparés. Les fichiers restent locaux.

Les outils `prepare_clone_data`, `prepare_clone_previews`,
`prepare_clone_minimaps` et `prepare_legacy_data` documentent les préparations
existantes. Certains utilisent encore les chemins et sorties d'un espace de
travail de recherche : ils ne constituent pas un import automatique universel.
Ils doivent être adaptés aux sources locales disponibles et autorisées.
Les scripts et toutes leurs ressources de recherche ne sont pas interchangeables.

```powershell
.\.venv\Scripts\python.exe -m tools.clone_app --data "C:\ressources\data" --state "C:\profil-test\annotations.sqlite3"
```

L'option `--state` permet un profil de test séparé des notes personnelles.
Ne pas utiliser des données de test pour publier sur le service public.

## Construire l'exécutable Windows

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build-windows.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name WoWCartoForever-Client --paths . tools/clone_app.py
.\dist\WoWCartoForever-Client\WoWCartoForever-Client.exe --data "C:\ressources\data"
```

Cette commande produit le client et ses dépendances, sans embarquer les cartes.
Pour un installateur complet, préparer et contrôler les ressources distribuables,
les placer dans le sous-dossier `data` de l'application, puis utiliser un
emballage Windows approprié. Les anciens scripts Inno liés à des chemins locaux
ne sont pas présentés ici comme une chaîne de release portable.

## Héberger le service communautaire

Le service PHP et les migrations MySQL sont dans `hosting`. Copier uniquement
`config.example.php` comme modèle, renseigner les secrets sur le serveur et
protéger le dossier `private`. Sauvegarder et tester les migrations dans l'ordre
avant toute intervention sur une base réelle. La CI utilise des bases jetables.
