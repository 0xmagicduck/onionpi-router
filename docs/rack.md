# Baie virtuelle

## Centre de données virtuel

L’interface présente désormais la baie comme un petit plan de contrôle de
centre de données. Les quatre indicateurs restent entièrement dérivés de l’état
déjà connu par `RackManager` : nœuds joignables, sorties forcées par Tor,
empreintes de politique synchronisées et alertes. Le panneau **Fabric Tor**
rend explicite le canal utilisé pour administrer une machine distante :

```
OnionPi → circuit Tor → service onion autorisé → agent local
```

« Tester le fabric » exécute l’opération groupée `refresh` sur les seuls nœuds
distants qui possèdent une adresse onion. Cela ouvre de vrais circuits et
actualise la dernière lecture de chaque agent ; aucune latence ou disponibilité
n’est fabriquée côté navigateur. La table **Nœuds et services** reprend de la
même façon les unités déclarées actives dans `state.services`.

Cette vue ressemble à un réseau privé maillé dans son expérience de gestion,
mais la frontière technique reste volontairement nette : Tor transporte le
plan de contrôle et les flux explicitement proxifiés, pas un sous-réseau IP
transparent. Le câblage visuel documente donc la topologie ; il ne crée jamais
de route L2/L3 entre deux appareils.

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
                                          fichier de requête + service natif
                                                                    │
                                           exécutant privilégié revalidateur
```

La forme est celle qui existe déjà sur la Pi : un agent sans privilège dépose
une requête, puis systemd, launchd ou une tâche Windows réveille un service
privilégié qui **revalide le verbe contre sa propre liste** et n’extrait aucun
argument du fichier. Sur le nœud comme ici, la frontière de sécurité est
l’exécutant privilégié, pas l’agent.

## Trois verrous indépendants

1. **Autorisation client onion (v3).** Le nœud chiffre le descripteur de son
   service pour la clé x25519 de cette baie. Sans elle, l’adresse ne se résout
   pas, même pour qui la connaît. La clé est enregistrée auprès de Tor par
   `ONION_CLIENT_AUTH_ADD`, en mémoire : elle est réenregistrée au démarrage de
   l’application, comme le service onion est republié.
2. **Signature de la requête.** Chaque appel porte un HMAC-SHA256 sur la
   version du protocole, l’identifiant du nœud, le verbe, un horodatage, un
   nonce et l’empreinte du corps. L’agent refuse un horodatage décalé de plus
   de deux minutes et un nonce déjà vu — sa mémoire de nonces est bornée, un
   rejeu ne la fait pas croître, et un appel non signé n’y écrit rien.
3. **Signature de la réponse.** Le nœud signe ce qu’il répond, avec une clé
   dérivée séparément et le nonce de l’appel auquel il répond. Le circuit
   authentifie le *service onion*, pas l’agent : sans cette signature, tout ce
   qui parvient à répondre à cette adresse dicterait à la baie l’état du nœud,
   et ce sont ces lectures qui déclenchent alertes, historique et repoussée
   automatique des règles.

La baie ne se rabat jamais sur le protocole v1, qui laissait les réponses non
signées : un repli déclenché par une réponse est un repli offert à qui la
fabrique. Un nœud resté en v1 affiche « réponse non authentifiée » et se
répare en réinstallant son agent — la commande est réaffichable.

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
| `egress` | sans objet : le coupe-circuit de la Pi s’applique déjà | `tor-only` (Linux/macOS) ou `direct` |
| `keep_open_ports` | sans objet | ports laissés joignables en entrée, 22 par défaut |
| `exit_country` | politique Tor globale | transmis au nœud |

Sous Linux, `tor-only` signifie : sortie interdite sauf le trafic du démon Tor.
Une application qui ignore le proxy échoue au lieu de fuir — `apt` compris.
Sous macOS, PF redirige TCP et DNS dans Tor et bloque les protocoles restants.
Sous Windows, le mode est refusé jusqu’à la présence d’un transport TUN
vérifié : le pare-feu seul pourrait couper Internet, pas transformer une
connexion arbitraire en SOCKS. `direct` reste disponible sur chaque plateforme.

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

## Ports et câbles

Chaque façade expose des ports logiques : un client local en a un, une machine
distante en a quatre. Le mode **Câbler** relie deux ports libres appartenant à
des nœuds installés dans la même baie. La liaison, sa couleur et sa vitesse
sont persistées dans SQLite et réapparaissent après un redémarrage.

Une prise ne peut porter qu’un câble. Déplacer un de ses deux appareils hors
de la baie supprime automatiquement la liaison : l’interface ne laisse donc
jamais un câble fantôme pointer vers une autre baie. L’état affiché est dérivé
de celui des deux nœuds (`online`, `warning` ou `offline`).

Ce câblage est un inventaire visuel, pas une nouvelle voie d’application : il
ne touche ni nftables, ni NetworkManager, ni la politique de l’agent. Les
règles de chaque fiche restent l’unique source de vérité de l’accès réseau.

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
2. Ouvrir **Préparer l’installation**, choisir Linux, macOS ou Windows et
   copier la commande GitHub. L’archive hors ligne reste disponible.
3. Lancer la commande sur la machine. Elle vérifie l’empreinte du bootstrap,
   qui vérifie à son tour que l’agent téléchargé est exactement celui que
   l’appliance exécute. Les deux téléchargements ciblent le SHA Git immuable
   inscrit dans la publication, et non `main` ou un tag supposé. Elle installe
   ensuite Tor et Python, publie le service onion avec autorisation client,
   crée les services natifs (systemd, launchd ou tâches Windows) et affiche
   l’adresse `.onion`.
4. Coller le jeton à l’invite. Il n’est pas dans la commande : un argument est
   lisible dans `ps` par tout compte de la machine et reste dans l’historique
   du shell, et l’installation est justement le moment où une machine neuve
   est la moins connue.
5. Recopier l’adresse onion dans la fiche, puis « Interroger ».

Les deux empreintes viennent de l’appliance, qui a été installée depuis une
publication signée : elles sont la référence, GitHub ne l’est pas. Une
installation sans copie de référence — développement, archive hors ligne —
doit passer `--unverified-bundle` explicitement ; sans lui, rien ne s’exécute.

L’adresse remonte à la main, et c’est voulu : la baie ne va pas la chercher
elle-même, donc rien ne s’enrôle sans qu’un opérateur l’ait vu.

## Ce que la baie ne fait pas

* **Pas de shell distant.** Les verbes sont énumérés et revalidés des deux
  côtés : état, nouvelle identité, redémarrage de Tor, lecture d’un journal
  d’une unité listée, redémarrage, publication de la carte du maillage, rotation
  de la clé de maillage. Rien qui prenne une commande en argument. Pour un accès
  interactif, `torsocks ssh` vers le port laissé ouvert — ou une redirection
  OnionMesh, qui évite d’exposer ce port.
* **Pas de trafic à travers la Pi.** Un nœud distant sort par son propre Tor.
  La baie l’administre, elle ne le route pas. Deux nœuds qui se parlent le font
  directement, par [OnionMesh](onionmesh.md) : la baie signe la carte qui les
  autorise, elle n’est pas sur le chemin.
* **Pas d’inventaire automatique.** Un client du Wi-Fi est *proposé*, jamais
  ajouté tout seul, et un nœud distant n’existe que parce qu’un opérateur l’a
  déclaré et lui a recopié son adresse.

## Points d’API

Tous sous `/api/v1/rack`, session obligatoire, jeton CSRF pour chaque mutation.

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| `GET` | `/rack` | Topologie complète : baies, nœuds, câbles, profils, appareils proposés, alertes, limites, verbes |
| `POST` | `/rack/racks`, `/racks/update`, `/racks/remove` | Cadres |
| `POST` | `/rack/racks/arrange` | Rangement des U, sans changer l’ordre |
| `POST` | `/rack/cables`, `/cables/remove` | Création et retrait d’une liaison entre deux ports libres |
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
| `GET` | `/rack/mesh` | État du réseau superposé : coordinateur, verrou, membres, révocations |
| `POST` | `/rack/mesh/lock` | Verrou de maillage K-sur-N |
| `POST` | `/rack/mesh/netmap` | Publication immédiate de la carte d’un nœud |
| `POST` | `/rack/mesh/endorsement`, `/mesh/endorsements` | Ce qu’un garant signe, et les contre-signatures reçues |
| `GET` | `/rack/agent-bundle` | `packaging/agent/` en tar.gz |
