# Journal des versions

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et la
numérotation [SemVer](https://semver.org/lang/fr/).

## [Non publié]

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
- `install.sh --upgrade` : réinstallation en place qui conserve le point
  d’accès, les mots de passe, la base et la configuration.
- Verbes privilégiés `update`, `update-check` et `update-schedule` dans
  `onionpi-agent-apply`.
- Fichier `VERSION`, exposé par `/api/v1/status` et par la page Paramètres.
- Intégration continue GitHub : ruff, pytest, tsc, build, shellcheck,
  cohérence des versions, contrôle anti-secrets ; workflow de publication.

## [0.1.0]

Première version : point d’accès Wi-Fi routé par Tor, interface web
d’administration, contournement (Snowflake, obfs4, meek), filtrage DNS,
blocage d’appareils, partage de fichiers, chat local et service onion
optionnel.
