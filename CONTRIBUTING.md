# Contribuer

## En trois commandes

```bash
make setup                                            # venv + npm ci
export ONIONPI_SESSION_SECRET="$(openssl rand -hex 32)"
make demo                                             # API sur 127.0.0.1:8080
```

`make demo` lance l’application en **mode démonstration** : Tor, nftables et
systemd sont remplacés par des réponses plausibles, donc aucune Raspberry Pi
n’est nécessaire. Le compte créé est `admin` / `mot-de-passe-de-demo-solide`.
Dans un second terminal, `make ui` sert l’interface sur 5173 et relaie `/api`
(WebSocket compris) vers 8080.

`make` seul liste toutes les cibles. Chacune appelle le script qui fait déjà
autorité : il n’y a pas de seconde façon de construire OnionPi.

## Où est quoi

| Chemin | Ce qu’on y trouve |
| --- | --- |
| `backend/onionpi/` | L’application. `main.py` monte les routes, `services.py` assemble les gestionnaires. |
| `frontend/src/` | Vite + React, sans routeur ni bibliothèque d’état. Les types de `types.ts` sont écrits à la main. |
| `packaging/` | Ce qui s’installe sur la Pi. `install.sh` fait autorité sur la disposition déployée. |
| `packaging/agent/` | Ce qui s’installe sur une machine **distante** de la baie. |
| `docs/` | [`rack.md`](docs/rack.md) la baie, [`mesh.md`](docs/mesh.md) la dorsale radio, [`onionmesh.md`](docs/onionmesh.md) le réseau superposé visé, [`updates.md`](docs/updates.md) la chaîne de publication. |

Le modèle de privilèges est le cœur du projet : le service web n’appelle jamais
`systemctl`, `nft` ni `sudo`. Il écrit un verbe dans une file, et un script
root le **revalide contre sa propre liste**. `CLAUDE.md` en donne le détail.

## Avant d’ouvrir une pull request

```bash
make check      # équivalent de ./scripts/check.sh
```

Ce script enchaîne ce que la CI vérifiera : `ruff`, `pytest`, `tsc`, la
construction de l’interface, `shellcheck`, la cohérence des versions,
l’absence de secrets dans les fichiers suivis, et `actionlint` + `zizmor` sur
les workflows. Chaque groupe se lance seul : `make backend`, `make frontend`,
`make shell`, `make meta`, `make workflows`.

Deux outils facultatifs, installés à part, activent les derniers contrôles :

```bash
brew install shellcheck actionlint     # ou l'équivalent de votre distribution
.venv/bin/pip install zizmor
```

## Ce que la CI refuse

- une version incohérente entre `VERSION`, `frontend/package.json` et
  `backend/onionpi/__init__.py` ;
- un script ou une unité systemd ajouté dans `packaging/` sans ligne
  correspondante dans `install.sh` **et** `uninstall.sh` — une désinstallation
  qui laisse des fichiers derrière elle est un bug ;
- un fichier d’identifiants, une clé privée ou un condensat scrypt suivi par
  git.

## Style

Le code existant est la référence. En particulier :

- l’interface, les messages et les commentaires destinés aux utilisateurs sont
  en français ; les commentaires de code sont en anglais ;
- un commentaire explique **pourquoi**, pas quoi. Les commentaires qui
  paraphrasent la ligne suivante sont supprimés en revue ;
- côté shell : `set -Eeuo pipefail`, variables toujours entre guillemets,
  aucune entrée non validée passée à `systemctl`, `nft` ou `rm` ;
- côté Python : pas de `subprocess` avec `shell=True`, pas de chemin construit
  par concaténation de chaînes ;
- toute action privilégiée passe par un verbe de `onionpi-agent-apply`. Si un
  correctif a besoin de root depuis l’application, la bonne réponse est un
  nouveau verbe validé côté root, jamais un `sudo` dans le service web.

## Réglages à faire dans GitHub

Ces protections ne peuvent pas vivre dans le dépôt : elles se cochent dans les
réglages, et sans elles une partie de ce qui précède n’est qu’une convention.
Ce qui est publié ici s’installe tout seul, à 4 h du matin, sur des machines
qui font passer le trafic de vraies personnes.

**Branche `main`** — *Settings → Rules → Rulesets* :

- exiger une pull request, avec revue, et **revue des propriétaires**
  (`.github/CODEOWNERS` ne fait rien sans cette case) ;
- exiger les vérifications `Backend`, `Frontend`, `Shell`, `Version and
  packaging consistency`, `Workflow security`, `No credentials in the tree`,
  `Analyse python`, `Analyse javascript-typescript` ;
- exiger que la branche soit à jour avant fusion ;
- bloquer les poussées forcées et la suppression de la branche ;
- appliquer les règles **aussi aux administrateurs** : une exception permanente
  pour soi-même est l’exception que quelqu’un d’autre finira par utiliser.

**Environnement `stable-release-signing`** — *Settings → Environments* : c’est
lui qui détient la clé GPG. Relecteurs obligatoires, et branches de déploiement
limitées à `main` et aux étiquettes `v*`. Le travail de construction ne reçoit
jamais la clé privée ; sans cet environnement, une modification de workflow
suffirait à signer une publication.

**Sécurité** — *Settings → Code security* : signalement privé de vulnérabilité
(référencé par `SECURITY.md`), alertes et mises à jour Dependabot, analyse de
secrets avec **blocage à la poussée**. `scripts/check-secrets.sh` attrape ce
qui est déjà écrit ; le blocage à la poussée attrape ce qui est en train de
l’être.

**Actions** — *Settings → Actions* : jeton `GITHUB_TOKEN` en lecture seule par
défaut, et n’autoriser que les actions du dépôt plus celles vérifiées par
GitHub. Les workflows épinglent déjà chaque action à un commit — une étiquette
se déplace, et qui la déplace exécute du code ici.

## Publier une version

```bash
./scripts/set-version.sh 0.2.0
# … relire CHANGELOG.md, committer, ouvrir la PR, la fusionner …
git tag v0.2.0 && git push origin v0.2.0
./scripts/set-version.sh 0.3.0   # les constructions edge visent désormais 0.3.0
```

Un envoi sur `main` publie une pré-version `edge`. Une étiquette `v*` publie
une version `stable`. Les deux passent par les tests avant d’être publiées,
parce qu’une Raspberry Pi installera l’archive toute seule à 4 h du matin.

Voir [`docs/updates.md`](docs/updates.md) pour la mécanique complète.
