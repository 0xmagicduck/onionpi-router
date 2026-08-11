## Ce que change cette PR

<!-- Une ou deux phrases. Le « pourquoi » avant le « quoi ». -->

## Vérifications

- [ ] `./scripts/check.sh` passe
- [ ] Testé sur une Raspberry Pi, ou expliqué pourquoi ce n’était pas nécessaire
- [ ] `CHANGELOG.md` mis à jour si le comportement visible change

## Frontières de sécurité

- [ ] Aucune nouvelle action privilégiée, ou : nouveau verbe validé dans
      `onionpi-agent-apply` (jamais d’argument lu depuis la file d’attente)
- [ ] Aucun nouveau chemin en écriture pour le service web
- [ ] Aucun trafic client ne sort de Tor
- [ ] Si `packaging/` gagne un fichier : ajouté à `install.sh` **et** à
      `uninstall.sh`
