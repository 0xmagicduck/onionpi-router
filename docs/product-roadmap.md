# Feuille de route produit et architecture

## Cap produit

OnionPi doit se comporter comme une appliance, pas comme un serveur Linux à
administrer au quotidien : on la branche, elle dit clairement si la protection
est effective, elle refuse de fournir un faux sentiment de sécurité et elle se
répare ou revient à la version précédente sans écran ni clavier.

Trois critères guident chaque évolution :

1. **Pratique** — installation guidée, état compréhensible, diagnostic et
   récupération locale documentés.
2. **Sûr par défaut** — en cas de doute, le Wi-Fi client est coupé ou confiné ;
   aucun trafic ne sort directement et aucun état écrit par l’application ne
   devient implicitement fiable pour root.
3. **Modulaire** — l’interface consomme des capacités stables ; Tor, les
   transports, le filtrage et la plateforme matérielle restent remplaçables
   derrière leurs adaptateurs.

## Socle consolidé dans cette branche

- Un état de protection unique (`demo`, `degraded`, `contained`, `protected`)
  agrège pare-feu, Tor, DNS et point d’accès. L’interface ne déduit plus la
  sécurité du seul voyant Tor.
- Le point d’accès n’est plus lancé directement par NetworkManager : l’unité
  `onionpi-ap.service` dépend du coupe-circuit. Un échec de chargement nftables
  empêche donc le Wi-Fi client de rester exposé.
- Le remplacement des règles nftables est validé puis appliqué dans une seule
  transaction.
- Les résultats root et la zone de préparation des mises à jour vivent dans
  des répertoires root, séparés de l’espace dont l’application peut renommer
  les entrées.
- L’authentification réserve atomiquement son quota avant scrypt et limite le
  nombre de vérifications simultanées. Les corps HTTP, y compris segmentés,
  sont bornés avant parsing ; un import multipart n’est analysé qu’après
  session et CSRF.
- Logout, expiration et changement de mot de passe révoquent aussi les
  WebSockets. Les archives de mise à jour ont une taille maximale et leur
  manifeste signé est vérifié avant le gros téléchargement.
- L’image Raspberry Pi OS téléchargée est contrôlée par l’empreinte publiée ;
  une image personnalisée exige une empreinte explicite.

## Version 0.3.0 — feuille de route réalisée

La version 0.3.0 livre les trois priorités le 11 août 2026. Les validations
automatiques qui matérialisent les critères de sortie font partie du dépôt et
du workflow de publication.

### P0 — mise à jour reproductible

- Les wheelhouses CPython 3.11 et 3.13 pour arm64 sont construits en CI,
  inventoriés dans `wheelhouse/SHA256SUMS` et inclus dans l’archive signée.
  Une mise à niveau installe exclusivement avec `pip --no-index --find-links`.
- Chaque version est installée sous `/opt/onionpi/releases/<version>` puis le
  lien `current` est basculé atomiquement. Un journal root des mutations
  système permet au service de reprise de retirer les nouveaux fichiers et de
  restaurer l’ancienne version après une coupure.
- La construction non privilégiée ne reçoit aucune clé. Seul le job de
  publication, rattaché à l’environnement GitHub protégé
  `stable-release-signing`, peut signer les artefacts déjà testés.

Critère validé par `packaging/tests/update-interruption-matrix.sh`, qui coupe
l’installation et le rollback manuel à douze points durables et exige un lien
`current` cohérent vers l’ancienne ou la nouvelle version.

### P1 — exploitation guidée

- L’assistant de première ouverture impose successivement le changement du mot
  de passe, la confirmation WAN/AP, le test réel du coupe-circuit, le contrôle
  de l’heure et la sauvegarde d’un code de récupération.
- La page « Protection » présente les quatre états et associe chaque contrôle
  défaillant à une action sûre ou au téléchargement du diagnostic.
- `onionpi-maintenance --open` active depuis la console une fenêtre locale de
  1 à 30 minutes. Elle autorise la récupération du compte sans ouvrir SSH ni
  ajouter de secret permanent.
- Les sauvegardes utilisent AES-256-GCM avec une clé dérivée par scrypt. Leur
  restauration affiche d’abord les changements et refuse toute enveloppe ou
  phrase secrète invalide.

Les métriques suivent désormais le temps et la proportion passés en
`protected`, les entrées en `contained` et le taux de récupération réussie.

### P2 — architecture modulaire

- `AppServices` compose les dépendances applicatives. Les routeurs FastAPI et
  leurs contrats vivent dans les modules `auth`, `protection`, `network`,
  `files`, `chat` et `updates` ; `main.py` ne conserve que la composition, le
  cycle de vie et les middlewares.
- `TorBackend`, `FirewallBackend`, `AccessPointBackend` et `UpdateBackend`
  isolent les implémentations Raspberry Pi et démonstration sans élargir les
  verbes privilégiés acceptés par le helper root.
- Les réponses statut/configuration ont des schémas Pydantic versionnés. Le
  document `docs/openapi-v1.json` et les types TypeScript sont régénérés en CI ;
  toute dérive fait échouer la livraison.

Cette frontière permet de remplacer un backend ou d’ajouter un transport Tor
sans changer le modèle de protection ni les contrats HTTP publics.

## Garde-fous de livraison

Chaque version doit conserver les tests actuels et ajouter :

- un test sur Raspberry Pi ou VM qui prouve l’absence de trafic direct quand
  Tor, dnsmasq ou le rechargement nftables échoue ;
- une matrice de coupure/interruption pour installation et rollback ;
- un budget de ressources sur Pi (mémoire au repos, connexions scrypt
  simultanées, taille des historiques et des requêtes) ;
- une vérification de l’image et une nomenclature des composants générée avec
  l’archive de publication.

La métrique produit principale n’est pas « Tor connecté », mais le temps passé
en état `protected`, complété par le nombre d’entrées en `contained` et le taux
de récupération sans intervention SSH.
