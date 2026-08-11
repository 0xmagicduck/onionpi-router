# Mises à jour automatiques

Une OnionPi installée chez quelqu’un doit recevoir les correctifs sans que
personne ne se connecte en SSH. Elle ne doit pas pour autant ouvrir un port,
annoncer sa présence au réseau local, ni installer n’importe quoi.

## La chaîne complète

```
git push origin main            git push origin v0.3.0
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
 SHA256SUMS (+ signature) → journal root → install.sh --upgrade hors ligne
                       │
       lien current atomique → onionpi-verify → retour arrière si échec
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
   `SHA256SUMS`. Le manifeste signé est récupéré avant l’archive, dont la taille
   est limitée à 256 Mio par défaut (`ONIONPI_UPDATE_MAX_ARCHIVE_BYTES`).
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
     un fichier remplacé sur la page de publication, et une modification du
     workflow qui n’a pas obtenu l’approbation de publication ;
   - **non couvert** : la compromission simultanée d’un administrateur du dépôt
     et d’un approbateur de l’environnement de signature, ou celle de la clé
     privée elle-même. Une clé conservée et utilisée entièrement hors ligne
     reste la séparation la plus forte.

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

   Côté dépôt, le job de construction ne reçoit aucun secret. Après les tests,
   ses artefacts sont transmis au job `publish`, rattaché à l’environnement
   protégé `stable-release-signing`. Cet environnement doit imposer des
   approbateurs, des branches de déploiement protégées et contenir
   `ONIONPI_GPG_PRIVATE_KEY` (ainsi que `ONIONPI_GPG_PASSPHRASE` si nécessaire).
   Perdre la clé privée oblige à publier sa remplaçante par une mise à jour
   signée avec l’ancienne.
4. **Le contenu.** L’archive doit contenir `packaging/install.sh`, une
   interface construite, un `VERSION` cohérent, les wheelhouses arm64 avec leur
   propre manifeste SHA-256 et une nomenclature SPDX. Le SBOM externe est lui
   aussi couvert par le `SHA256SUMS` signé.

## Ce qui se passe pendant l’installation

L’application est construite dans un nouveau répertoire immuable
`/opt/onionpi/releases/<version>`. Python est installé depuis le wheelhouse de
l’archive, sans accès réseau. Avant toute mutation, le client enregistre la
cible actuelle et les fichiers système gérés dans
`/var/backups/onionpi-update-<horodatage>/`, puis écrit un journal root. Le lien
`/opt/onionpi/current` n’est basculé qu’une fois la nouvelle arborescence
complète.

`onionpi-verify` juge ensuite le résultat. En cas d’échec, ou si
`onionpi-update-recover.service` trouve le journal après une coupure, les
fichiers introduits sont retirés, les précédents sont restaurés et `current`
revient atomiquement vers l’ancienne version. Tor, nftables, dnsmasq, nginx et
l’application redémarrent alors dans cet ordre. Les trois dernières
sauvegardes et leurs versions référencées sont conservées.

## Diagnostiquer

```bash
onionpi-update --status                 # document JSON, lisible sans root
sudo onionpi-update --check             # cherche, n'installe pas
sudo onionpi-update --apply             # installe maintenant
sudo onionpi-update --rollback          # revient à la sauvegarde la plus récente
sudo onionpi-update --recover           # reprend immédiatement un journal interrompu
journalctl -u onionpi-update -n 100 --no-pager
```

L’interface montre la même chose : version installée, version disponible,
prochaine vérification, dernière tentative et les trois derniers événements.
L’état affiché est lu dans `/var/lib/onionpi-privileged/update.state`, un
répertoire root seulement traversable en lecture par le groupe `onionpi`.

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
