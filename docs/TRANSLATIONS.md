# Ajouter une traduction à la base historique

Le client dispose des sources historiques FR et EN. Les autres langues ne sont
pas incluses : elles doivent être fournies et vérifiées. Un pack de traduction
réutilise les coordonnées, les catégories et les cartes de la source EN ; il ne
modifie jamais son fichier `master.loc`.

Placer un fichier UTF-8 nommé selon son code, par exemple `de.json`, dans le
dossier `data/translations` du client. Au lancement, seuls les packs valides et
liés à l’empreinte de la source EN installée sont proposés. Exemple de structure
(les identifiants sont fictifs et doivent être remplacés par ceux de la source) :

```json
{
  "format": "wcf-translation-v1",
  "language": "de",
  "source": "US",
  "source_sha256": "EMPREINTE_SHA256_DE_LA_SOURCE_EN",
  "annotations": {
    "123": {"name": "Nom traduit", "comment": "Commentaire traduit"}
  },
  "categories": {"18": "Nom de catégorie traduit"},
  "groups": {"1": "Nom de groupe traduit"}
}
```

Les entrées et champs non traduits restent en anglais. Le client affiche le
nombre d’entrées comportant une traduction ; ce nombre ne garantit pas que tous
leurs champs sont traduits. Les fichiers invalides, les identifiants inconnus et
les empreintes incorrectes sont refusés. Les annotations personnelles restent
séparées par langue. La recherche des packs optionnels parcourt les données en
mémoire ; elle n’utilise pas l’index anglais afin de ne pas masquer les résultats
traduits. Ce mécanisme n’est pas une traduction automatique et ne produit pas de
contenu supplémentaire.

Sur les données locales examinées, la base SQLite indexée FR+EN occupe
110 776 320 octets. Les noms et commentaires FR représentent 4 692 480 octets
avant indexation ; ce chiffre illustre un ordre de grandeur, pas une estimation
garantie pour une nouvelle langue ou ses index.
