# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et la
numérotation [SemVer](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté

- **Baie virtuelle.** Une page **Baie virtuelle** range les machines dans des
  cadres et des emplacements numérotés en U : clients du Wi-Fi d’un côté,
  machines distantes de l’autre. Créer, déplacer, isoler, régler — la
  topologie est décrite ici, l’application des règles reste chez ceux qui la
  faisaient déjà. Pour un client du Wi-Fi, une règle de baie est déléguée à
  `DeviceGuard` et `DeviceAccessManager` : **aucun verbe privilégié n’a été
  ajouté sur la Pi**. Un index unique partiel garantit côté SQLite qu’un
  emplacement ne porte jamais deux machines ; un déplacement vers un U occupé
  échange les deux au lieu d’en écraser une.
- **Agent de nœud installable** (`packaging/agent/`). Un VPS, un serveur ou une
  seconde Pi rejoignent la baie en installant un agent qui n’écoute que sur
  `127.0.0.1` et se publie comme service onion v3. Aucun port n’est ouvert sur
  Internet. Sur le nœud, la même architecture que sur la Pi : l’agent est sans
  privilège, dépose une requête, une unité `.path` réveille un service root qui
  revalide le verbe et n’extrait aucun argument du fichier.
- **Sortie forcée par Tor sur les nœuds distants.** La politique par défaut
  poussée à un nœud interdit toute sortie qui n’appartient pas au démon Tor,
  laisse le port 22 joignable en entrée, et — quand le nœud est isolé — retire
  aux applications l’accès au port SOCKS local sans couper ni l’agent ni le
  service onion. Le rendu du jeu de règles nftables est fait par un programme
  root qui revalide chaque champ avant d’écrire une ligne.
- **Deux verrous indépendants sur le canal d’administration.** L’autorisation
  client onion v3 (`ONION_CLIENT_AUTH_ADD`, réenregistrée au démarrage comme
  l’est le service onion) rend l’adresse d’un nœud irrésoluble sans la clé de
  la baie ; la signature HMAC-SHA256 de chaque appel couvre le verbe, un
  horodatage, un nonce et l’empreinte du corps, et l’agent refuse un
  horodatage décalé ou un nonce déjà vu, avec une mémoire bornée.
- **Aucune identification de nœud stockée.** Le jeton d’un nœud et sa clé
  x25519 sont dérivés d’un secret maître (`/var/lib/onionpi/rack.key`, 0600),
  de l’identifiant du nœud et d’un compteur de rotation. Une copie de la base,
  un export de configuration ou une sauvegarde n’en contient rien ; renouveler
  un nœud est un incrément qui invalide d’un coup l’ancien jeton et l’ancienne
  clé.
- **Trafic par appareil.** Le pare-feu compte désormais les octets de chaque
  client dans deux ensembles nftables dynamiques, et une minuterie root
  (`onionpi-accounting.timer`, toutes les 15 s) publie ces compteurs dans
  `/var/lib/onionpi-privileged/traffic.json`. L’interface, qui n’a pas le droit
  d’interroger nftables, se contente de lire ce fichier : aucun verbe
  privilégié n’a été ajouté. Les colonnes « Trafic » des pages Tableau de bord,
  Réseau et Protection cessent d’afficher zéro sur matériel réel.
- **Cumul qui survit aux rechargements.** Un ensemble nftables repart vide à
  chaque chargement des règles ; l’application conserve le relevé précédent et
  n’ajoute que la différence, de sorte qu’un redémarrage du pare-feu, de la Pi
  ou du service web ne remet pas les totaux à zéro. Le bouton « Remettre les
  compteurs à zéro » de la page Protection
  (`POST /api/v1/devices/traffic/reset`) est le seul geste qui les efface.

### Sécurité

Audit statique complet du dépôt, consigné dans
[`docs/security-audit-2026-08-13.md`](docs/security-audit-2026-08-13.md). Cinq
constats, tous corrigés ici, chacun accompagné d’un test qui échoue sans son
correctif.

- **En-tête CSRF non ASCII.** Starlette décode les en-têtes en latin-1 et
  `secrets.compare_digest` refuse les chaînes non ASCII : un caractère accentué
  dans `X-CSRF-Token` répondait 500 avec une trace dans le journal, et une 500
  échappe au middleware qui pose les en-têtes de sécurité. La comparaison porte
  désormais sur les formes encodées et le refus reste un 403.
- **Champ de formulaire d’import.** `max_part_size` ne borne que les parties
  texte d’un corps multipart — une partie fichier est écrite dans un fichier
  temporaire sans être mesurée. Lui passer le maximum d’import laissait donc
  accumuler le champ `path` dans un `bytearray` jusqu’à un gigaoctet, soit toute
  la mémoire de l’appliance. Il vaut maintenant 64 Kio.
- **Réserve de stockage.** Elle était consultée après que l’analyseur multipart
  avait déjà écrit tout le corps sur la carte SD. Le budget est calculé avant
  toute lecture, réduit de moitié parce que la copie temporaire et le fichier
  final coexistent, et `BodyLimitMiddleware` le réévalue à chaque import pour
  arrêter aussi un corps qui n’annonce aucune longueur.
- **Sauvegardes chiffrées.** Leur dérivation scrypt, aussi coûteuse en mémoire
  qu’une vérification de mot de passe, ne passait pas par le plafond
  `hashing_slot` du processus. Elle y passe, et les trois routes de sauvegarde
  répondent 429 lorsqu’il est saturé.
- **Récupération de compte.** Elle réinitialise le compte que l’appliance
  possède déjà au lieu d’écrire « admin » en dur — ce qui, sur une installation
  au nom différent, aurait ajouté un second administrateur en laissant valides
  l’ancien mot de passe et les sessions ouvertes. Toutes les sessions et tous
  les WebSockets sont révoqués.
- Une actualisation des listes DNS à la fois : deux reconstructions simultanées
  tenaient chacune jusqu’à 300 000 domaines en mémoire et se disputaient
  l’écriture de `block.hosts`. Les endpoints concernés répondent 409.

## [0.4.0] — 2026-08-12

Version « routeur utile » : chaque appareil du foyer reçoit ses propres règles
d’accès, l’adresse onion cesse d’être un mot de passe partagé, et l’appliance
sait dire elle-même ce que sa configuration expose.

### Ajouté

- **Accès des appareils.** Chaque client du Wi-Fi peut recevoir un nom, une
  pause de 15 minutes à 8 heures, et une plage horaire quotidienne par jour de
  la semaine (les plages qui franchissent minuit appartiennent à la nuit du
  jour choisi). Un ordonnanceur interne recalcule toutes les 20 secondes qui
  doit être coupé et confie le résultat à `DeviceGuard`, seul écrivain de
  `blocked-macs.txt` : aucun nouveau verbe privilégié n’a été ajouté.
- **Audit de sécurité** (`GET /api/v1/security/audit`, page « Audit »). Treize
  contrôles de durcissement — mode démonstration, coupe-circuit, SSH ouvert aux
  clients Wi-Fi, âge du mot de passe, durée des sessions, mises à jour, service
  onion sans autorisation client, filtrage DNS, pays de sortie imposé, rotation
  d’identité, horloge, stockage, certificat local — avec un score, un classement
  par urgence, le geste qui corrige chaque point et, quand c’est possible, le
  bouton qui l’exécute. Le rapport ne contient ni secret, ni domaine visité, ni
  identifiant de client : il est exportable tel quel.
- **Autorisation client du service onion (v3).** L’interface génère une paire de
  clés x25519 par appareil autorisé ; Tor chiffre alors le descripteur pour ces
  clés seules et une adresse qui fuite ne suffit plus à joindre la page de
  connexion. La clé privée est affichée une fois, dans le format exact attendu
  par le navigateur Tor ; OnionPi n’en conserve que la moitié publique.
- Les règles d’accès voyagent dans l’export de configuration et dans les
  sauvegardes chiffrées. Une sauvegarde antérieure à 0.4.0 reste restaurable.
- La colonne « Appareils » affiche le nom donné par le foyer plutôt que celui
  choisi par le fabricant, ainsi que l’état d’accès réel de chaque client.

### Modifié

- `users.password_changed_at` est ajouté au schéma SQLite et renseigné à chaque
  changement de mot de passe. Les bases existantes sont migrées au démarrage.
- `POST /api/v1/onion` republie le service avec la liste d’accès du moment ; la
  publication est retirée puis recréée, seule façon de changer cette liste par
  le port de contrôle.

### Ajouté — interface (travail préparé pour 0.3.2, jamais publié seul)

- Système de design complet pour l’interface web : jetons de couleur,
  d’espacement, de typographie et de mouvement dans `frontend/src/styles/`,
  partagés par tous les écrans.
- Thème clair et thème sombre, avec suivi du réglage du système par défaut et
  choix manuel conservé d’une visite à l’autre.
- Palette de commandes (`Ctrl`/`Cmd` + `K`) pour rejoindre une page ou lancer
  une action, avec recherche insensible aux accents et classement par
  pertinence.
- Raccourcis clavier globaux : `Ctrl K` la palette, `R` l’actualisation, `?`
  leur mémo, `Échap` la fermeture.
- Lien d’évitement vers le contenu, piège de focus et fermeture par `Échap`
  dans les fenêtres modales, libellés ARIA sur les jauges et les tableaux.
- Vue d’ensemble : quatre indicateurs de tête (appareils, débit, temps protégé,
  disponibilité) et jauge circulaire de démarrage de Tor.
- Graphique de trafic avec aires dégradées, échelle lisible et infobulle au
  survol donnant la valeur exacte de chaque échantillon.
- Tableaux triables et recherche des appareils par nom, adresse IP ou MAC.
- Fichiers : importation multiple, sélection groupée avec téléchargement et
  suppression en lot, copie du lien de téléchargement, tri des colonnes et
  confirmation de suppression dans une fenêtre dédiée.
- Journaux : actualisation manuelle, suivi en direct, filtrage des lignes et
  export du service affiché.
- Notifications empilables et menu de compte regroupant thème, raccourcis et
  déconnexion.

### Modifié — interface

- Refonte visuelle de toute l’interface web : barre latérale groupée par
  domaine et repliable de façon persistante, en-têtes de page, panneaux,
  boutons, champs et badges uniformisés.
- États vides et états de chargement explicites sur chaque écran, à la place
  des lignes vides et du message de chargement unique.
- Les tableaux deviennent des cartes empilées sur téléphone au lieu d’un
  défilement horizontal.

### Corrigé

- Les mises à jour automatiques s’installent de nouveau. L’installateur lancé
  avec `--upgrade` réclamait un mot de passe administrateur alors qu’il tourne
  dans un service systemd sans terminal : il s’arrêtait aussitôt, et
  `onionpi-update` revenait à la version précédente en signalant « Retour à
  X.Y.Z : l’installateur a échoué ». Aucune version publiée après 0.3.1 ne
  pouvait donc être installée à distance.
- L’intégration continue exécute désormais réellement la résolution des
  identifiants de `packaging/install.sh --upgrade` sans terminal
  (`packaging/tests/upgrade-noninteractive.sh`), au lieu de ne simuler que le
  journal de mise à jour.
- La déconnexion n’est plus déclenchée par un clic sur le nom d’utilisateur :
  elle est devenue une entrée explicite du menu de compte.
- Le bouton « plus d’actions » des fichiers, qui n’était relié à rien, est
  remplacé par des actions réelles.

## [0.3.1] — 2026-08-12

### Corrigé

- Le répertoire racine d’une version préparée devient traversable avant sa
  publication, afin que le service non privilégié puisse exécuter Python lors
  de la première installation comme après une mise à jour.
- Le service web démarre désormais Uvicorn avec l’interpréteur de la version
  publiée, sans conserver le chemin temporaire utilisé pour préparer le venv.
- Le contrôle d’installation ne signale plus le service générique `nftables`
  comme arrêté lorsque le coupe-circuit OnionPi est actif, et les compteurs
  vides sont correctement interprétés comme zéro.

## [0.3.0] — 2026-08-11

Version appliance : mise à jour hermétique et récupérable après une coupure,
première ouverture guidée, protection actionnable, sauvegardes chiffrées et
contrats internes modulaires.

### Ajouté

- Wheelhouses arm64 CPython 3.11/3.13 inclus dans l’archive signée, installation
  `pip --no-index` et nomenclature SPDX publiée avec chaque version.
- Versions immuables sous `/opt/onionpi/releases`, bascule atomique de `current`,
  journal des mutations root et service de reprise au démarrage.
- Matrice de douze interruptions de mise à jour, test fail-closed en espace réseau
  Linux et budget de ressources contrôlés par la CI.
- Assistant initial en cinq étapes, code de récupération scrypt et fenêtre de
  maintenance physique de 1 à 30 minutes.
- Page Protection à quatre états, actions correctives et métriques de temps
  protégé, confinement et récupération réussie.
- Sauvegardes AES-256-GCM protégées par phrase secrète, avec aperçu des
  changements avant restauration.
- Composition `AppServices`, interfaces de backends, routeurs FastAPI séparés,
  schémas OpenAPI versionnés et types TypeScript générés.

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

### Renforcé

- L’interface expose un état de protection agrégé et ne confond plus « Tor
  connecté » avec « routeur protégé ».
- Le manifeste OpenPGP est contrôlé avant l’archive de mise à jour, limitée à
  256 Mio ; les secrets de signature CI ne sont visibles que par l’étape GPG.
- La signature est isolée dans un job de publication rattaché à l’environnement
  GitHub protégé `stable-release-signing` ; le job de construction ne reçoit
  jamais la clé privée.

## [0.2.1] — 2026-08-11

Version de sécurité. Deux chemins permettaient à l’application web, si elle
était compromise, d’obtenir une écriture root arbitraire, et un client Wi-Fi
sans compte pouvait saturer la mémoire de la Raspberry Pi. Rien à faire côté
utilisateur : les correctifs sont dans le service et dans les scripts
privilégiés, appliqués par la mise à jour elle-même.

### Corrigé

- **Écriture root à travers un lien symbolique choisi par l’application.**
  `onionpi-agent-apply` écrivait sa réponse dans `agent.result.tmp` par une
  redirection ordinaire, dans un répertoire qui appartient à l’utilisateur
  `onionpi` : ce dernier pouvait y placer un lien symbolique entre deux
  commandes et faire écraser par root le fichier de son choix. La redirection
  se fait maintenant sous `noclobber`, c’est-à-dire en `O_CREAT|O_EXCL`, qui
  échoue sur tout nom existant au lieu de le suivre. Même correction pour le
  `chmod` de `update.state`, désormais appliqué au descripteur et non au nom.
- **Extraction root dans un répertoire redirigeable.** `onionpi-update`
  décompressait la publication sous `/var/lib/onionpi/updates` sans vérifier ce
  que ce nom désignait ; un lien symbolique planté là détournait le `rm -rf` et
  l’extraction. Le lien est supprimé et le répertoire doit appartenir
  exclusivement à root, sinon l’installation est refusée.
- **Épuisement mémoire par connexions simultanées.** Chaque vérification de mot
  de passe réserve 16 Mio, le compteur de tentatives ne voit que les essais
  terminés, et FastAPI traite chaque requête dans son propre fil : quelques
  dizaines de requêtes lancées ensemble suffisaient à saturer une Raspberry Pi
  de 1 Gio sans aucun compte. Quatre vérifications au plus s’exécutent
  désormais en parallèle, les autres attendent puis reçoivent un 429.
- **Le service onion court-circuitait nginx.** Il pointait sur uvicorn, démarré
  avec `--forwarded-allow-ips=127.0.0.1` : un visiteur venu de Tor choisissait
  donc l’adresse sur laquelle son quota de connexion était compté, et
  échappait aux limites de taille de corps. nginx écoute maintenant sur
  `127.0.0.1:8081` pour ce trafic, qui traverse le même proxy que les clients
  Wi-Fi.
- **La configuration importée échappait aux limites des points d’entrée.**
  `/api/v1/system/config` lisait le document à la main ; il passe désormais par
  les mêmes schémas que `POST /circumvention`, `/dns-filter`, `/tor/policy` et
  `/devices/block`, et les appareils bloqués sont appliqués en un seul envoi à
  l’agent privilégié au lieu d’un par appareil.
- **Condensat du compte système dans un journal lisible par tous.**
  `firstrun.sh` traçait ses commandes avec `set -x` dans un fichier créé au
  umask par défaut. Le journal est créé en 0600 et la trace est coupée sur le
  bloc qui manipule les identifiants.
- **Chemin contenant un octet nul** : `/api/v1/files` répondait 500 au lieu de
  400.
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
- nginx : `client_max_body_size` ramené à 4 Mio partout sauf sur
  `/api/v1/files/upload`, et `server_tokens off`.
- `/api/v1/health`, le seul point d’entrée accessible sans compte, ne publie
  plus la version installée.
- `RateLimiter` prend un verrou : le compteur du test de débit est partagé par
  tous les fils de travail.

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
[0.2.1]: https://github.com/0xmagicduck/onionpi-router/releases/tag/v0.2.1
[0.3.0]: https://github.com/0xmagicduck/onionpi-router/releases/tag/v0.3.0
[0.3.1]: https://github.com/0xmagicduck/onionpi-router/releases/tag/v0.3.1
