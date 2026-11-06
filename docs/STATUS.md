# État de validation — 6 novembre 2026

| Contrôle | Résultat observé | Limite |
| --- | --- | --- |
| Suite locale | 232 tests réussis | La CI publique reste distincte |
| Installation Windows | Installation et lancement du dernier paquet local réussis | Essais sur cette machine, pas sur un parc indépendant |
| Fichiers installés | 5 392 fichiers identiques à la construction par SHA-256 | Ne prouve pas le comportement utilisateur |
| Legacy candidat | Féralas, annotations et recherche observés dans le paquet installé | Archive non authentifiée, fonds continentaux 1.20, une image absente |
| Modern | Textures et zones affichées | Raccords et présence réelle des annotations à vérifier en jeu |
| FR/EN et thème sombre | Changements observés dans un paquet installé précédent | Toutes les fenêtres et tous les DPI ne sont pas vérifiés |
| Communauté publique | Deux profils ont reçu le même instantané vide | Publication non vide entre deux installations graphiques non validée |
| Communauté isolée | Écriture, réception, révisions et conflits testés via HTTPS local | Ce n'est pas une recette publique sur deux machines |

Le recalage Modern réutilise 349 correspondances d'image Kalimdor et 237
Azeroth. Les erreurs médianes sont de 3,93 et 4,11 unités de scène, avec des
maxima proches de 35. Elles ne sont pas des pourcentages WoW et ne démontrent
pas indépendamment les positions en jeu.

Les images du paquet local ont été contrôlées par empreinte et décodage :
179 images générées 1.20, 36 plans 1.20, 26 plans Legacy candidats et
1 956 textures Forever. Ces ressources ne sont pas incluses dans le dépôt.

## Avant une version finale

- Vérifier les raccords et plusieurs positions en jeu à différents zooms.
- Obtenir des fonds continentaux Legacy authentifiés et résoudre l'image absente.
- Comparer tous les seuils visuels, icônes et commandes au client 1.20.
- Compléter et relire les traductions supplémentaires.
- Valider une contribution réelle entre installations graphiques indépendantes,
  puis le redémarrage, la persistance et la reprise réseau.
- Fixer la licence du code et le périmètre de redistribution des ressources.
- Achever la recette de l'installateur public.
