# Consignes pour les agents

## Objet du dépôt

OnionPi transforme une Raspberry Pi en point d’accès Wi-Fi dont le trafic TCP
et DNS est forcé à travers Tor. Le dépôt contient à la fois l’application et
l’appliance : une modification de `packaging/` peut être installée
automatiquement sur des machines réelles.

## Vérifications

Lancez `./scripts/check.sh` avant de rendre une modification. Les groupes
peuvent être exécutés séparément avec `meta`, `backend`, `frontend`, `shell` ou
`workflows`. Pour une modification ciblée, commencez par le groupe concerné et
terminez par la suite complète lorsque les dépendances locales le permettent.

## Contraintes de sécurité et d’architecture

- Le service web reste non privilégié : aucun appel à `sudo`, `systemctl`,
  `nft` ou `reboot` depuis `backend/onionpi`.
- Toute nouvelle action root passe par un verbe revalidé dans
  `packaging/onionpi-agent-apply.sh`; le fichier de requête est une entrée non
  fiable.
- `packaging/install.sh` est la source de vérité du système déployé. Tout
  script ou unité ajouté sous `packaging/` doit également être installé et
  retiré par `packaging/install.sh` et `packaging/uninstall.sh`.
- Le point d’accès client reste lié au coupe-circuit nftables. Ne créez pas de
  chemin de démarrage qui puisse activer le Wi-Fi après un échec du pare-feu.
- Le profil client doit rester en WPA2 uniquement (`proto rsn`) avec AES-CCMP
  pour les chiffrements pairwise et group. N’introduisez ni WPA1, ni TKIP, ni
  WEP. Le maillage 802.11s séparé reste en WPA3-SAE avec PMF obligatoire.
- Préservez le mode démonstration pour toute fonctionnalité qui interagit avec
  le système hôte.
- Ne suivez aucun secret : clé privée, mot de passe, PSK, condensat scrypt,
  adresse `.onion` ou fichier `*-identifiants.txt`.

## Style

Les textes destinés aux utilisateurs et la documentation sont en français.
Les commentaires de code sont en anglais et expliquent la raison d’un choix.
En shell, utilisez `set -Eeuo pipefail`, citez les variables et validez toute
entrée avant une commande privilégiée. En Python, pas de `shell=True` ni de
chemin construit par concaténation de chaînes.

Consultez `CLAUDE.md` pour l’architecture détaillée et `CONTRIBUTING.md` pour
la procédure de contribution et de publication.
