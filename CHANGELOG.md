# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et la
numérotation [SemVer](https://semver.org/lang/fr/).

## [Non publié]

### Corrigé

- Le point d’accès dépend maintenant du coupe-circuit et le remplacement de la
  table nftables est transactionnel : une règle invalide ne laisse plus le
  Wi-Fi client actif sans protection.
- Les fichiers écrits par root et la préparation des mises à jour sont sortis
  de l’espace dont l’application peut renommer les entrées.
- Les tentatives de connexion concurrentes sont comptées avant scrypt, les
  corps HTTP sont bornés avant parsing et les WebSockets suivent la révocation
  des sessions.
- L’image Raspberry Pi OS exige une empreinte SHA-256 et la trace de premier
  démarrage ne journalise plus le condensat du mot de passe système.

- **Fuite DNS lors du contrôle de l’adresse de sortie.** `TorController`
  interrogeait `check.torproject.org` par un proxy `socks5://`, ce qui fait
  résoudre le nom par le résolveur du système — celui du fournisseur d’accès —
  avant d’entrer dans Tor. Une requête en clair toutes les cinq minutes
  annonçait un utilisateur de Tor à cette adresse. Le proxy est désormais
  `socks5h://`, comme partout ailleurs dans le code.
- **Limitation des tentatives de connexion contournable.** nginx ajoutait
  l’adresse du client à l’en-tête `X-Forwarded-For` qu’il recevait au lieu de la
  remplacer : un client Wi-Fi pouvait donc choisir l’adresse sur laquelle son
  quota était compté, ou épuiser celui d’un autre appareil. nginx réécrit
  maintenant l’en-tête depuis `$remote_addr`, et un plafond global s’ajoute au
  quota par adresse pour les requêtes qui n’empruntent pas nginx (service
  onion).
- **Suppression de répertoire par un nom d’archive.** `onionpi-update` acceptait
  `..` comme numéro de version, qui devient un composant de chemin sous
  `/var/lib/onionpi/updates`. Le motif exige désormais un premier caractère
  alphanumérique, et l’adresse de téléchargement doit être servie par
  `github.com` ou `githubusercontent.com`.
- **Énumération des comptes par le temps de réponse** de `/api/v1/auth/login` :
  un nom inconnu coûte maintenant le même scrypt qu’un nom connu.

### Renforcé

- L’interface expose un état de protection agrégé et ne confond plus « Tor
  connecté » avec « routeur protégé ».
- Le manifeste OpenPGP est contrôlé avant l’archive de mise à jour, limitée à
  256 Mio ; les secrets de signature CI ne sont visibles que par l’étape GPG.

- `ONIONPI_DEMO_MODE` sur une installation réelle est signalé en `ERROR` dans le
  journal : ce mode rend chaque commande inopérante et chaque mesure fictive.
- Les réglages numériques (`ONIONPI_SESSION_TTL`, `ONIONPI_MAX_UPLOAD_BYTES`,
  `ONIONPI_STORAGE_RESERVE_BYTES`, ports) sont bornés, et une valeur booléenne
  non reconnue est journalisée au lieu de valoir silencieusement « désactivé ».
- Les origines du serveur de développement Vite (`:5173`) ne sont plus
  acceptées hors mode démonstration.
- `verify_password` refuse un condensat dont le coût scrypt est inférieur à
  celui écrit par cette version.
- `onionpi.service` : `ProtectProc`, `ProtectHostname`, `ProtectClock`,
  `ProtectKernelLogs`, `RestrictNamespaces`, `SystemCallArchitectures`,
  `CapabilityBoundingSet=` vide.

## [0.2.0] — 2026-08-11

Version de résilience et de diagnostic : l’interface reste exploitable quand
une métrique hôte est refusée, les états persistants résistent aux coupures et
une mise à jour défectueuse restaure désormais toute la surface installée.

### Ajouté

- Diagnostic authentifié dans **Paramètres** : intégrité SQLite, stockage,
  services critiques, bootstrap Tor, file d’actions privilégiées, fragments de
  configuration et synchronisation de l’horloge, avec remèdes en français et
  export JSON local.
- Maintenance SQLite réelle : suppression des sessions expirées, historique
  borné à 2 000 messages et 4 000 événements, index temporels, `PRAGMA
  optimize` et contrôle `quick_check`.
- Tests d’intégration des fichiers atomiques, de la file privilégiée, des
  écritures SQLite concurrentes, de la rétention et des métriques sur un hôte
  restreint. La suite compte maintenant 85 tests backend.

### Modifié

- Tous les petits états sensibles (requêtes root, ponts, politique Tor,
  blocages, DNS, clé onion, proxy Snowflake et préférences de mise à jour) sont
  écrits par fichier temporaire, `fsync` puis renommage atomique.
- Le polling React ne chevauche plus deux requêtes, ne met plus à jour un
  composant démonté et se suspend lorsque l’onglet est masqué.
- La publication CI refuse de créer une archive que les appareils rejetteraient
  faute de clé de signature.
- La sauvegarde du client de mise à jour couvre maintenant `/opt/onionpi`, les
  unités systemd, les helpers root et les configurations installées ; le retour
  arrière redémarre l’ensemble cohérent des services.

### Corrigé

- Le mode démonstration ne consulte plus `psutil` avant de substituer ses
  valeurs, ce qui supprimait un `500` sur les hôtes où `sysctl` est interdit.
- Une erreur isolée de CPU, mémoire, disque, température, réseau ou temps de
  démarrage ne fait plus tomber `/api/v1/status` ni le thread de métriques.
- Une politique de sortie ou un fichier de ponts refusé par Tor restaure
  toujours le fragment précédent, y compris lors de la première écriture.
- Les adresses IP de pont impossibles, les réponses Moat non HTTP 2xx et les
  documents inattendus sont refusés proprement.
- Le proxy Snowflake de démonstration reflète maintenant l’état demandé.
- Les répertoires temporaires de téléchargement sont nettoyés après un échec et
  `ONIONPI_UPDATE_KEEP_BACKUPS=0` ne conserve plus une quantité illimitée de
  sauvegardes.

## [0.1.0] — 2026-08-11

Point d’accès Wi-Fi routé par Tor, interface web d’administration,
contournement (Snowflake, obfs4, meek), filtrage DNS, blocage d’appareils,
partage de fichiers, chat local et service onion optionnel.

### Ajouté

- Mise à jour automatique : `onionpi-update` récupère les publications GitHub
  **par le port SOCKS de Tor**, vérifie l’empreinte SHA-256 (et la signature
  OpenPGP si un trousseau est configuré), réinstalle en place et revient
  automatiquement à la version précédente si le contrôle post-installation
  échoue.
- Horaires précis configurables : `onionpi-update.timer` est régénéré depuis
  `/etc/onionpi/update.conf` ou depuis la page **Paramètres** (jusqu’à six
  heures par jour, avec un délai aléatoire pour ne pas signer l’appareil par
  la minute exacte de sa requête).
- Deux canaux : `stable` (versions étiquetées) et `edge` (chaque envoi sur
  `main`, publié après les tests).
- Publications signées OpenPGP (`FD4DC3B7A6C94E1F3B2F130A99EFBC5B082A1AB8`).
  La clé publique est installée avec le code et la signature est **exigée** par
  défaut : une archive doit être signée par une clé déjà présente sur
  l’appareil. La clé de signature vivant dans le coffre d’Actions du même
  dépôt, cela ne protège pas d’un attaquant capable d’y écrire — voir
  `docs/updates.md`.
- `install.sh --upgrade` : réinstallation en place qui conserve le point
  d’accès, les mots de passe, la base et la configuration.
- Verbes privilégiés `update`, `update-check` et `update-schedule` dans
  `onionpi-agent-apply`.
- Fichier `VERSION`, exposé par `/api/v1/status` et par la page Paramètres.
- Intégration continue GitHub : ruff, pytest, tsc, build, shellcheck,
  cohérence des versions, contrôle anti-secrets ; workflow de publication.

[0.1.0]: https://github.com/0xmagicduck/onionpi-router/releases/tag/v0.1.0
[0.2.0]: https://github.com/0xmagicduck/onionpi-router/releases/tag/v0.2.0
