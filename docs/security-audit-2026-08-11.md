# Audit sécurité du 11 août 2026

## Résultat exécutif

L’audit statique complet du snapshot `5e23600bcf5175c1bacfda9c742542018a41646f`
a couvert les 125 fichiers du dépôt. Il a relevé 12 problèmes : 2 élevés,
6 moyens et 4 faibles. Cette branche corrige neuf d’entre eux et transforme
les trois chantiers structurels restants en priorités P0 de la feuille de route.

| Sujet | État dans cette branche |
| --- | --- |
| Helpers root dans un espace renommable par l’application | Corrigé : résultats et staging root séparés |
| Cycle du pare-feu susceptible de laisser l’AP sans protection | Corrigé : transaction nftables et AP dépendant |
| Image Raspberry Pi OS non authentifiée | Corrigé : SHA-256 obligatoire |
| Épuisement par corps HTTP avant authentification | Corrigé : limites ASGI et auth avant multipart |
| Course du quota de connexion avant scrypt | Corrigé : réservation atomique et plafond simultané |
| Dépendances pip téléchargées hors de l’artefact signé | **Ouvert — P0** : wheelhouse signé |
| APT/pip de la mise à niveau hors du tunnel de téléchargement | **Ouvert — P0** : installation hermétique hors ligne |
| Rollback incomplet après interruption ou nouveaux fichiers | **Ouvert — P0** : versions immuables et journal de mutations |
| Secrets GPG visibles par toutes les étapes du job | Corrigé : environnement limité à l’étape de signature |
| Archive de publication sans limite de taille | Corrigé : limite streaming et post-contrôle |
| WebSocket survivant à la révocation de session | Corrigé : fermeture par jeton/utilisateur et revalidation |
| Condensat du mot de passe système dans la trace firstboot | Corrigé : xtrace retiré et journal `0600` |

## Lecture produit

Le risque principal n’est pas la compromission du tableau de bord en elle-même,
mais une divergence silencieuse entre « Tor connecté » et « trafic client
réellement confiné ». L’état `protection` et l’unité `onionpi-ap` rendent cette
propriété visible et exécutable au même endroit.

Le deuxième risque est la réparation distante : une mise à jour partiellement
installée peut supprimer l’unique moyen de la corriger. Les trois points encore
ouverts forment donc un seul chantier cohérent — une archive autonome, une
version installée dans un répertoire immuable et une bascule atomique — plutôt
que trois rustines indépendantes.

## Validation requise sur Raspberry Pi

La suite locale vérifie la logique Python, TypeScript, les scripts Bash et les
contrats de packaging. Avant publication, il reste nécessaire de prouver sur
Bookworm et Trixie que :

1. une règle nftables invalide empêche ou confine l’AP sans perte de la table
   active précédente ;
2. arrêter Tor, dnsmasq, NetworkManager ou `onionpi-firewall` produit le bon
   état de protection et aucune sortie WAN directe ;
3. une mise à niveau depuis 0.2.0 migre les fichiers privilégiés, conserve la
   configuration et restaure correctement la version précédente ;
4. une coupure de courant pendant l’installation garde l’appareil récupérable.

Les décisions et critères de sortie correspondants sont suivis dans
[`product-roadmap.md`](product-roadmap.md).
