# Budget de ressources Raspberry Pi

La référence minimale est une Raspberry Pi arm64 avec 1 Gio de RAM. Ces limites
sont des garde-fous de livraison : une version qui les relève doit expliquer la
mesure faite sur Pi et adapter `scripts/check-resource-budget.sh`.

| Ressource | Budget v0.4 | Mécanisme |
|---|---:|---|
| Mémoire du service web | alerte à 320 Mio, arrêt à 384 Mio | `MemoryHigh` et `MemoryMax` dans `onionpi.service` |
| Calculs scrypt simultanés | 4, soit environ 64 Mio | sémaphore global `HASHING_SLOTS` |
| Tâches du service | 64 | `TasksMax` |
| Descripteurs ouverts | 1 024 | `LimitNOFILE` |
| Corps API ordinaire | 1 Mio | `BodyLimitMiddleware` avant parsing |
| Fichier importé | 1 Gio par défaut, écrit en flux | limite dédiée et réserve disque de 512 Mio |
| Archive de mise à jour | 256 Mio | limite `curl` et contrôle après téléchargement |
| Historique trafic | 180 points | `deque(maxlen=180)` |
| Messages / événements | 2 000 / 4 000 | purge SQLite à chaque maintenance |
| Règles d’accès par appareil | 128 | `MAX_RULES`, pauses échues effacées à chaque tic |
| Accès onion autorisés | 8 | `MAX_CLIENTS` |
| Vérifications de connexion | quotas par adresse et global | réservation atomique avant scrypt |

Le budget n’est pas une promesse que le noyau ne tuera jamais un processus :
Tor, nginx et dnsmasq ont leurs propres consommations. Il garantit que
l’interface non privilégiée ne peut pas, à elle seule, absorber la mémoire de
la Pi par des connexions ou historiques sans borne.
