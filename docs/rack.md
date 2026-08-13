# Baie virtuelle

La baie virtuelle donne à OnionPi une vue de salle machine : des cadres, des
emplacements numérotés en U, une machine par emplacement, et une feuille de
règles attachée à chaque machine. Elle couvre deux mondes à la fois.

* **Les nœuds locaux** sont les clients du Wi-Fi, déjà connus du pare-feu.
  Les placer dans une baie ne crée aucun chemin d’application nouveau : leurs
  règles sont déléguées à `DeviceGuard` et `DeviceAccessManager`, qui restent
  les seuls à écrire la liste de blocage. La baie nomme et range, elle
  n’applique pas elle-même.
* **Les nœuds distants** sont des machines qu’OnionPi ne route pas : un VPS, un
  serveur à la maison, une seconde Pi. Ils exécutent
  [`packaging/agent/`](../packaging/agent/README.md), n’écoutent que sur leur
  boucle locale, et sont joints à travers Tor. Leurs règles sont poussées à
  l’agent, qui applique son propre coupe-circuit.

## Le chemin d’un appel

```
interface web ──► RackManager ──► NodeClient ──► SOCKS Tor ──► service onion
                                                                    │
                                                        agent (non privilégié)
                                                                    │
                                                     fichier de requête + .path
                                                                    │
                                              onionpi-node-apply.sh (root)
```

La forme est celle qui existe déjà sur la Pi : un agent sans privilège dépose
une requête, une unité `.path` réveille un service root qui **revalide le
verbe contre sa propre liste** et n’extrait aucun argument du fichier. Sur le
nœud comme ici, la frontière de sécurité est le script root, pas l’agent.

## Deux verrous indépendants

1. **Autorisation client onion (v3).** Le nœud chiffre le descripteur de son
   service pour la clé x25519 de cette baie. Sans elle, l’adresse ne se résout
   pas, même pour qui la connaît. La clé est enregistrée auprès de Tor par
   `ONION_CLIENT_AUTH_ADD`, en mémoire : elle est réenregistrée au démarrage de
   l’application, comme le service onion est republié.
2. **Signature de la requête.** Chaque appel porte un HMAC-SHA256 sur la
   version du protocole, le verbe, un horodatage, un nonce et l’empreinte du
   corps. L’agent refuse un horodatage décalé de plus de deux minutes et un
   nonce déjà vu — sa mémoire de nonces est bornée, un rejeu ne la fait pas
   croître.

## Aucun secret stocké

Le jeton d’un nœud et sa clé d’autorisation ne sont écrits nulle part. Ils sont
dérivés à la demande d’un secret maître (`/var/lib/onionpi/rack.key`, 0600), de
l’identifiant du nœud et d’un compteur de rotation :

```
jeton  = HMAC-SHA256(maître, "<nœud>:<compteur>:token")
graine = HMAC-SHA256(maître, "<nœud>:<compteur>:auth")   → clé privée x25519
```

Conséquences directes :

* une copie de la base, un export de configuration ou une sauvegarde ne
  contient aucune identification de nœud ;
* la commande d’installation peut être réaffichée — perdre le presse-papiers
  n’oblige pas à reconstruire la machine ;
* renouveler un nœud est un incrément : l’ancien jeton **et** l’ancienne clé
  cessent de valoir au même instant, et l’agent doit être réinstallé.

Perdre `rack.key` invalide tous les nœuds d’un coup. C’est le même compromis
que la clé du service onion, et c’est pourquoi le fichier vit au même endroit.

## Les règles d’un nœud

| Règle | Nœud local | Nœud distant |
| --- | --- | --- |
| `access` | `blocked` → blocage nftables sur la Pi | `blocked` → isolement du nœud |
| `schedule` | plage horaire appliquée par l’ordonnanceur d’accès | même plage, poussée dans la politique |
| `egress` | sans objet : le coupe-circuit de la Pi s’applique déjà | `tor-only` (défaut) ou `direct` |
| `keep_open_ports` | sans objet | ports laissés joignables en entrée, 22 par défaut |
| `exit_country` | politique Tor globale | transmis au nœud |

Sur le nœud, `tor-only` signifie : sortie interdite sauf le trafic du démon
Tor. Une application qui ignore le proxy échoue au lieu de fuir — `apt`
compris. `direct` est une dérogation assumée, à réserver aux maintenances.

L’isolement va plus loin que `tor-only` : les applications perdent aussi
l’accès au port SOCKS local. La machine reste administrable et son service
onion reste publié, mais elle ne sort plus.

## Comment un nœud est surveillé

Un fil d’arrière-plan interroge les nœuds distants par vagues : une vague par
minute, six nœuds au plus, trois sockets simultanées, le moins récemment vu en
premier. Une baie pleine est donc parcourue en une dizaine de minutes. C’est
délibéré : chaque interrogation ouvre un circuit Tor, et l’appliance a un
budget. Le bouton « Interroger » d’une fiche court-circuite l’attente.

Quand la lecture d’un nœud annonce une empreinte de politique différente de
celle voulue, la politique est repoussée automatiquement. Une règle refusée
n’est donc jamais perdue : elle est stockée, la fiche affiche « Règles en
attente d’application », et la prochaine vague réessaie.

## Profils, actions groupées, import

Une **feuille de règles nommée** — un profil — se pose sur autant de machines
qu’on veut. Elle passe par la validation d’une fiche et par les mêmes managers :
appliquer un profil, c’est écrire exactement ce qu’une fiche aurait écrit, et
un profil ne peut donc rien exprimer de plus. Douze profils au plus.

Les **actions groupées** (isoler, autoriser, interroger, sortir de la baie,
appliquer un profil) sont la même opération répétée, jamais un chemin
d’application différent. Elles ne sont pas transactionnelles, et c’est
délibéré : une machine sur douze qui est hors ligne est le cas normal, ce qui a
réussi tient, ce qui a échoué revient nommé.

Les clients du Wi-Fi qu’aucune fiche ne décrit sont proposés à l’**ajout**, avec
le nom que leur bail annonce — nettoyé comme un nom saisi à la main. L’ajout ne
crée aucun droit : l’appareil était déjà routé par la Pi, il gagne une fiche,
un emplacement et une feuille de règles qui ne bloque rien.

## Disponibilité et alertes

Chaque sondage d’un nœud distant laisse une lecture : horodatage, réponse ou
non, charge, mémoire, disque, amorçage de Tor. La table est bornée à
**288 lectures par nœud** — environ deux jours à raison d’une vague toutes les
dix minutes — et une lecture chasse la plus ancienne.

La disponibilité affichée est la **part des sondages qui ont répondu**, pas une
part de temps. Un nœud est visité toutes les dix minutes environ ; prétendre
savoir ce qu’il a fait entre deux circuits serait une invention.

Les alertes d’une fiche sont dérivées de ce que la fiche contient déjà : nœud
injoignable, règles non appliquées par le nœud, autorisation client onion
absente, Tor non amorcé, unité arrêtée, mémoire ou disque au-delà de 90 %,
sortie directe. Rien n’y est mesuré une seconde fois.

## Installer un nœud

1. **Baie virtuelle → Ajouter un nœud → Machine distante.** Le nœud est créé
   sans adresse : il est « en attente », et rien ne lui est encore demandé.
2. **Télécharger l’agent** depuis sa fiche, et copier l’archive sur la machine.
3. Lancer la commande affichée par « Afficher la commande ». Elle installe Tor,
   publie le service onion avec autorisation client, installe l’agent et les
   unités, puis affiche l’adresse `.onion`.
4. Recopier cette adresse dans la fiche, puis « Interroger ».

L’adresse remonte à la main, et c’est voulu : la baie ne va pas la chercher
elle-même, donc rien ne s’enrôle sans qu’un opérateur l’ait vu.

## Ce que la baie ne fait pas

* **Pas de shell distant.** Les verbes sont énumérés et revalidés des deux
  côtés : état, nouvelle identité, redémarrage de Tor, lecture d’un journal
  d’une unité listée, redémarrage. Rien qui prenne une commande en argument.
  Pour un accès interactif, `torsocks ssh` vers le port laissé ouvert.
* **Pas de trafic à travers la Pi.** Un nœud distant sort par son propre Tor.
  La baie l’administre, elle ne le route pas.
* **Pas d’inventaire automatique.** Un client du Wi-Fi est *proposé*, jamais
  ajouté tout seul, et un nœud distant n’existe que parce qu’un opérateur l’a
  déclaré et lui a recopié son adresse.

## Points d’API

Tous sous `/api/v1/rack`, session obligatoire, jeton CSRF pour chaque mutation.

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| `GET` | `/rack` | Topologie complète : baies, nœuds, profils, appareils proposés, alertes, limites, verbes |
| `POST` | `/rack/racks`, `/racks/update`, `/racks/remove` | Cadres |
| `POST` | `/rack/racks/arrange` | Rangement des U, sans changer l’ordre |
| `POST` | `/rack/nodes`, `/nodes/update`, `/nodes/remove` | Fiches |
| `POST` | `/rack/nodes/move` | Emplacement, avec échange si le U est pris |
| `POST` | `/rack/nodes/rules` | Feuille de règles, poussée dans la foulée |
| `POST` | `/rack/nodes/refresh` | Interrogation immédiate |
| `POST` | `/rack/nodes/bulk` | Une opération de fiche répétée, avec ses échecs nommés |
| `POST` | `/rack/nodes/import` | Clients du Wi-Fi ajoutés comme nœuds locaux |
| `GET` | `/rack/nodes/{id}/history` | Lectures conservées et disponibilité |
| `POST` | `/rack/profiles`, `/profiles/remove` | Feuilles de règles nommées |
| `POST` | `/rack/nodes/action` | Un verbe de la liste publiée |
| `POST` | `/rack/nodes/enrollment`, `/nodes/rotate-token` | Identifiants dérivés |
| `GET` | `/rack/agent-bundle` | `packaging/agent/` en tar.gz |
