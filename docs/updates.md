# Mises à jour automatiques

Une OnionPi installée chez quelqu’un doit recevoir les correctifs sans que
personne ne se connecte en SSH. Elle ne doit pas pour autant ouvrir un port,
annoncer sa présence au réseau local, ni installer n’importe quoi.

## La chaîne complète

```
git push origin main            git push origin v0.2.0
        │                                │
        ▼                                ▼
   .github/workflows/release.yml (tests, puis publication)
        │                                │
   pré-version « edge »            version « stable »
        └──────────────┬─────────────────┘
                       │  api.github.com, par le port SOCKS de Tor
                       ▼
      onionpi-update.timer  →  onionpi-update --apply
                       │
      SHA256SUMS (+ signature) → sauvegarde → install.sh --upgrade
                       │
              onionpi-verify → retour arrière si échec
```

Rien n’est poussé vers la Pi : c’est elle qui demande, aux heures qu’elle
connaît. Aucun port n’est ouvert côté WAN, et le dépôt n’a besoin d’aucun accès
à l’appareil.

## Choisir les heures

Depuis **Paramètres → Mises à jour**, ou dans `/etc/onionpi/update.conf` :

```bash
ONIONPI_UPDATE_SCHEDULE=03:00,15:00   # jusqu'à six heures par jour
ONIONPI_UPDATE_RANDOM_DELAY=45m       # décalage aléatoire
```

```bash
sudo onionpi-update --write-timer     # applique le changement
systemctl list-timers onionpi-update  # vérifie la prochaine exécution
```

Le délai aléatoire n’est pas là pour ménager GitHub : une requête à 04:30:00
pile, tous les jours, est une signature. `Persistent=true` rattrape l’exécution
manquée d’une Pi éteinte à l’heure prévue.

Pour un calendrier que les heures ne savent pas exprimer :

```bash
ONIONPI_UPDATE_CALENDAR="Mon,Thu *-*-* 04:30:00"
```

Cette variable l’emporte sur `ONIONPI_UPDATE_SCHEDULE`, et n’est modifiable
que depuis le fichier — l’interface web ne peut pas écrire de calendrier
systemd arbitraire.

## Les deux canaux

| Canal | Source | Pour qui |
| --- | --- | --- |
| `stable` | dernière publication étiquetée `v*` | tout le monde |
| `edge` | chaque envoi sur `main`, après les tests | développement |

Une construction `edge` est publiée sous `<VERSION>-edge.<n>`, donc comme une
pré-version de la version en préparation : elle est toujours plus récente que
la précédente construction `edge`, et toujours plus ancienne que la publication
vers laquelle elle mène.

Conséquence à connaître : repasser de `edge` à `stable` n’installe rien tant
que la version stable n’a pas dépassé la construction `edge` déjà installée.
C’est voulu — le client de mise à jour ne rétrograde jamais tout seul. Pour
forcer :

```bash
sudo onionpi-update --apply --force
```

## Ce que l’appareil vérifie avant d’installer

1. **Le transport.** `curl --socks5-hostname 127.0.0.1:9050` : même la
   résolution DNS de `api.github.com` reste dans Tor. Sans Tor, pas de mise à
   jour — plutôt que la même requête en clair.
2. **L’empreinte.** L’archive doit correspondre à la ligne qui la concerne dans
   `SHA256SUMS`.
3. **La signature.** `SHA256SUMS.asc` doit être une signature OpenPGP valide de
   `SHA256SUMS`, faite par la clé de publication OnionPi :

   ```
   FD4D C3B7 A6C9 4E1F 3B2F  130A 99EF BC5B 082A 1AB8
   ```

   Cette empreinte est celle de `packaging/keys/onionpi-release.asc`, que
   `install.sh` convertit en trousseau `/etc/onionpi/update-signing-key.gpg`.
   La clé publique voyage donc **avec le code déjà installé** : une archive ne
   peut être acceptée que si elle est signée par une clé qui se trouvait sur
   l’appareil avant même que cette archive existe.

   Ce que cela couvre, et ce que cela ne couvre pas :

   - couvert : une archive modifiée entre GitHub et la Pi, un miroir hostile,
     un fichier remplacé sur la page de publication, et une publication faite
     par quelqu’un qui n’a pas accès aux secrets de ce dépôt ;
   - **non couvert** : un attaquant capable d’écrire dans ce dépôt. Un
     workflow qu’il ajoute peut demander `ONIONPI_GPG_PRIVATE_KEY` au coffre
     d’Actions et signer ce qu’il veut. La clé de signature vit dans le même
     système que le code qu’elle authentifie, et c’est la limite de ce montage.

   Pour cette séparation-là, il faut sortir la clé du CI : la garder hors
   ligne, signer `SHA256SUMS` à la main et téléverser le `.asc` sur la
   publication. Le client de mise à jour ne voit aucune différence.

   Vérifier à la main ce que la Pi vérifie toute seule :

   ```bash
   gpg --import packaging/keys/onionpi-release.asc
   gpg --verify SHA256SUMS.asc SHA256SUMS
   sha256sum -c SHA256SUMS
   ```

   `ONIONPI_UPDATE_REQUIRE_SIGNATURE=1` est la valeur par défaut : une
   publication sans signature valide est refusée, elle n’est pas simplement
   signalée. Passez à `0` uniquement si vous construisez vos propres archives
   non signées.

   Côté dépôt, la signature est faite en CI avec le secret
   `ONIONPI_GPG_PRIVATE_KEY` (et `ONIONPI_GPG_PASSPHRASE` si la clé en a une).
   Perdre la clé privée n’empêche rien de fonctionner, mais oblige à publier
   une nouvelle clé publique par une mise à jour signée avec l’ancienne — donc
   à ne pas la perdre en premier.
4. **Le contenu.** L’archive doit contenir `packaging/install.sh`, une
   interface construite, et un fichier `VERSION` égal à la version annoncée par
   le nom du fichier.

## Ce qui se passe pendant l’installation

`/opt/onionpi` est copié dans `/var/backups/onionpi-update-<horodatage>/`, les
unités systemd avec. Puis `packaging/install.sh --upgrade` réinstalle par‑dessus :
il relit `/etc/onionpi/install.conf` au lieu de poser des questions, ne touche
ni au profil NetworkManager (qui contient le PSK), ni au compte administrateur,
ni à la base, ni au proxy Snowflake s’il tourne.

`onionpi-verify` juge le résultat. En cas d’échec, la copie est restaurée et le
service redémarré : la version précédente revient sans intervention. Les trois
dernières sauvegardes sont conservées.

## Diagnostiquer

```bash
onionpi-update --status                 # document JSON, lisible sans root
sudo onionpi-update --check             # cherche, n'installe pas
sudo onionpi-update --apply             # installe maintenant
sudo onionpi-update --rollback          # revient à la sauvegarde la plus récente
journalctl -u onionpi-update -n 100 --no-pager
```

L’interface montre la même chose : version installée, version disponible,
prochaine vérification, dernière tentative et les trois derniers événements.

## Frontières de sécurité

L’application web ne peut pas lancer une mise à jour : elle écrit un verbe dans
`/var/lib/onionpi/agent.request`, et `onionpi-agent-apply` — qui tourne en root
— le compare à sa propre liste (`update`, `update-check`, `update-schedule`)
avant d’agir. Aucun argument n’est jamais lu depuis ce fichier.

Les préférences choisies dans l’interface atterrissent dans
`/var/lib/onionpi/update.settings.json`, que l’application possède. Ce fichier
est traité comme hostile : `onionpi-update` n’en retient que quatre champs, le
canal doit valoir `stable` ou `edge`, chaque horaire doit correspondre à
`HH:MM`, et tout le reste est ignoré. Le dépôt, l’usage de Tor et l’exigence de
signature ne sont réglables que dans `/etc/onionpi/update.conf`, appartenant à
root — et `ONIONPI_UPDATE_ALLOW_OVERRIDES=0` retire même à l’interface le droit
de proposer quoi que ce soit.
