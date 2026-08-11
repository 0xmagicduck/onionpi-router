# Contribuer

## Préparer l’environnement

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
(cd frontend && npm ci)
```

## Avant d’ouvrir une pull request

```bash
./scripts/check.sh
```

Ce script enchaîne ce que la CI vérifiera : `ruff`, `pytest`, `tsc`, la
construction de l’interface, `shellcheck`, la cohérence des versions et
l’absence de secrets dans les fichiers suivis.

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
