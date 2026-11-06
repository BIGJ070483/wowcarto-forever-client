# Contribuer

## Corriger les données depuis le client

1. Sélectionner FR ou EN et synchroniser avant une correction.
2. Activer le mode édition et choisir la même base dans « Base autorisée pour
   l'édition ». Cette sélection est une garde locale, pas un compte ou un rôle
   d'administrateur.
3. Utiliser le menu contextuel de l'annotation. « Modifier l'annotation locale »
   garde la modification sur le PC. « Corriger pour la communauté » propose un
   envoi public après confirmation.
4. Synchroniser les autres clients pour recevoir la nouvelle version. En cas
   de conflit, recharger les données avant de proposer une nouvelle correction.

Une annotation historique FR/EN n'accepte actuellement qu'une correction
publique de nom. Une annotation communautaire accepte nom, catégorie, niveaux
et commentaire ; les coordonnées et les champs non interprétés sont préservés.

Une nouvelle note personnelle FR de référence 1.20 peut être partagée depuis
la carte. Sa copie locale reste indépendante. Un envoi strictement identique
est reconnu ; modifier la note puis la partager à nouveau crée une autre
contribution. Tags, indicateurs et certains caractères ne sont pas pris en
charge par ce partage direct et sont refusés plutôt qu'écartés.

Ne publiez ni données personnelles, ni identifiants, ni fichiers du jeu.
Legacy reste en lecture seule et n'alimente pas le service 1.20.

## Code, traductions et rapports de bugs

- Décrire le comportement attendu et les étapes de reproduction.
- Pour les cartes, préciser mode, base, zone, zoom et provenance des ressources.
- Les tests fictifs doivent rester isolés des bases publiques et personnelles.
- Ne jamais joindre une archive ou un exécutable historique non authentifié.
- Les traductions sont des surcharges liées à l'empreinte de la base EN ;
  les textes manquants restent en anglais. Voir [le format des packs](docs/TRANSLATIONS.md).
- Exécuter les tests applicables et indiquer ce qui a réellement été essayé.

Une contribution au dépôt n'entraîne aucune cession implicite de droits ; la
licence générale est encore à définir, comme indiqué dans [NOTICE](NOTICE.md).
