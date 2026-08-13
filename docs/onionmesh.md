# OnionMesh — le réseau superposé

## Ce qu’on veut, dit simplement

Un réseau privé entre ses propres machines, comme Tailscale : on nomme une
machine, on la joint, où qu’elle soit, sans ouvrir de port ni connaître son
adresse IP. Avec une différence : le transport est Tor.

Cette différence n’est pas cosmétique. Tailscale traverse le NAT avec STUN et
un serveur de coordination qui, par construction, voit l’adresse IP publique de
chaque machine ; quand la traversée échoue, le trafic passe par un relais DERP
qui appartient à Tailscale. Ici, le service onion **est** la traversée : rien
n’écoute sur Internet, il n’y a pas de STUN, pas de DERP, et le plan de
contrôle n’apprend jamais où sont les machines. En échange, on accepte la
latence et le débit d’un circuit Tor, et on le dit franchement (§ *Ce que ce
réseau n’est pas*).

## Où en est le code

Le réseau superposé existe. Il s’appuie sur deux briques plus anciennes, qui
gardent chacune son rôle :

| | Ce que c’est | Portée |
| --- | --- | --- |
| [`docs/mesh.md`](mesh.md) | Dorsale radio 802.11s + `batman-adv`, WPA3-SAE | Le **chemin direct** du maillage |
| [`docs/rack.md`](rack.md) | Plan de contrôle vers des agents distants via Tor | Le **canal d’enrôlement** et la publication des cartes |

Ce qui manquait était le **plan de données** : deux nœuds d’une même baie ne se
parlaient pas, tout passait par le centre et seul le centre initiait. C’est ce
que ce document décrit, et ce que les fichiers suivants mettent en œuvre :

| Fichier | Rôle |
| --- | --- |
| [`packaging/agent/onionpi_mesh.py`](../packaging/agent/onionpi_mesh.py) | Ed25519, X25519, ChaCha20-Poly1305, Noise IK, cartes, verrou. Bibliothèque standard uniquement. |
| [`packaging/agent/onionpi_mesh_runtime.py`](../packaging/agent/onionpi_mesh_runtime.py) | Répondeur, redirections, choix de chemin, sessions. |
| [`backend/onionpi/mesh.py`](../backend/onionpi/mesh.py) | Le coordinateur : une clé, un compteur de série, un verrou. |
| [`backend/tests/test_mesh.py`](../backend/tests/test_mesh.py) | Les vecteurs des RFC, et surtout tous les refus. |

## Le principe : l’identité est une clé, pas une adresse

Aujourd’hui le jeton d’un nœud est **dérivé du secret de la baie** :

    jeton = HMAC-SHA256(maître, "<nœud>:<compteur>:token")

C’est excellent pour l’exploitation — rien à stocker, une rotation est un
incrément — et c’est la mauvaise fondation pour un maillage : la baie peut
**devenir** n’importe lequel de ses nœuds. Tant que la baie est le seul
interlocuteur, cela ne coûte rien. Dès que deux nœuds se parlent entre eux, il
faut qu’un nœud puisse prouver *lui-même* qui il est, y compris à un pair qui
ne fait pas confiance au centre.

Donc, à l’installation, le nœud génère et ne divulgue jamais :

* une clé d’identité **Ed25519** — ce qu’il est, à long terme ;
* une clé statique **X25519** — ce avec quoi il chiffre, signée par la première.

La baie n’en reçoit que les moitiés publiques. Elle continue d’**autoriser** un
nœud ; elle ne peut plus l’**être**. Le jeton dérivé reste, mais rétrogradé au
rôle qu’il tient bien : le canal d’enrôlement, le temps que la clé publique du
nœud remonte.

## L’adresse se déduit de la clé

L’adresse d’un nœud sur le maillage est un ULA IPv6 (RFC 4193), donc jamais
routé sur Internet :

    fd7a:0000: <96 bits de poids faible de SHA-256(clé d'identité publique)>

Le préfixe fait exactement 32 bits et les 96 restants viennent de la clé, ce qui
donne une adresse complète sans reste à combler. C’est ce que font Yggdrasil et
cjdns, et c’est ce qui rend le plan d’adressage **auto-certifiant** :

* aucune attribution, aucun DHCP, aucune collision à négocier — le plan
  d’adresse actuel `10.43.X.Y` est dérivé de deux octets de MAC et
  [`docs/mesh.md`](mesh.md) admet qu’une collision reste possible et se corrige
  à la main ; ici il n’y a rien à corriger ;
* router vers une adresse, c’est authentifier la clé qui la produit. Personne
  ne « prend » l’adresse d’un autre : il faudrait une préimage de SHA-256 ;
* vérifiable hors ligne, sans annuaire : un opérateur qui lit une adresse dans
  un journal sait de quelle clé elle parle.

Le plan `10.43.0.0/16` de `bat0` ne bouge pas : il reste l’adressage du lien
radio. Le maillage, lui, est en IPv6 uniquement, et ses adresses ne dépendent
d’aucun lien.

## Le plan de contrôle : une carte signée

La Pi est le serveur de coordination. Elle publie à chaque nœud une **carte du
réseau** :

```json
{
  "version": 1,
  "serial": 412,
  "issued_at": 1786000000,
  "not_after": 1786086400,
  "node": "8e02…",
  "coordinator": "ed25519:…",
  "grants": [22, 9080],
  "peers": [
    {
      "node": "3f1c…",
      "identity": "ed25519:…",
      "static": "x25519:…",
      "static_signature": "…",
      "address": "fd7a:0:…",
      "onion": "…",
      "mesh_port": 9081,
      "mesh_v4": "10.43.0.2",
      "grants": [22],
      "endorsements": { "ed25519:…": "…" }
    }
  ],
  "forwards": [{ "listen": 2222, "node": "3f1c…", "port": 22 }],
  "revoked": ["ed25519:…"],
  "signature": "…"
}
```

`grants` à la racine est ce que **ce** nœud accepte de présenter ; le `grants`
d’un pair est ce que **lui** présente. Un nœud applique le premier et se sert du
second pour savoir quoi demander : les deux extrémités appliquent la carte, donc
ce qu’on aurait raconté à l’initiateur n’entre pas dans la décision.
`static_signature` est produite par le nœud lui-même — sans elle, le
coordinateur choisirait la clé avec laquelle chacun chiffre pour un pair, et sa
propre signature de la carte le couvrirait.

Signée par la clé Ed25519 du coordinateur, et un nœud refuse :

* une carte dont le `serial` est **inférieur ou égal** au dernier vu — sinon un
  rejeu réinstalle un pair révoqué, et une révocation qui se rejoue n’est pas
  une révocation ;
* une carte périmée (`not_after`), ce qui borne dans le temps ce qu’un
  coordinateur muet ou saisi peut laisser tourner ;
* une signature qu’il ne reconnaît pas.

Elle voyage sur le canal qui existe — un verbe `netmap` de plus dans le
vocabulaire de [`nodeclient.py`](../backend/onionpi/nodeclient.py), soumis aux
mêmes signatures d’appel et de réponse. Un second verbe, `mesh-rotate`, demande
au nœud de renouveler sa clé ; c’est lui qui l’engendre et l’ancienne qui signe
le changement.

La clé publique du coordinateur est **épinglée à l’installation**, par
`--coordinator-key` dans la commande d’enrôlement. Elle ne voyage jamais sur le
canal qu’elle authentifie : un nœud qui l’apprendrait d’une carte accepterait la
première carte qu’on lui tend, ce qui est exactement ce qu’elle empêche. Sans
elle, l’agent tourne mais le maillage reste éteint, et il le dit.

Une carte n’est republiée que quand son contenu change, ou quand elle a dépassé
la moitié de sa vie. Le balayage de la baie passe sinon son temps à re-signer
les mêmes pairs, et à user des circuits pour ne rien dire.

### Le verrou de maillage (facultatif)

Un coordinateur compromis peut inscrire un pair de son choix dans la carte. La
réponse de Tailscale s’appelle *tailnet lock* ; la même ici : quand le verrou
est activé, un nœud n’accepte une **nouvelle** clé de pair que si elle est
contresignée par K clés de confiance sur N, listées dans `mesh.lock`.

Ce fichier appartient à root sur le nœud et est posé par l’installateur
(`--mesh-lock K:clé,clé,…`). La baie ne l’écrit pas : un verrou que le
coordinateur pourrait remplacer ne verrouillerait rien. Elle **transporte** les
contre-signatures, elle ne peut pas les fabriquer. Un garant crée sa clé et
signe depuis sa propre machine :

```bash
onionpi-admin mesh-trustee --out garant.key
onionpi-admin mesh-endorse --key garant.key --node <id> --identity ed25519:<…>
```

Le coût est réel — ajouter une machine demande K opérateurs — donc c’est un
choix, pas un défaut. Ce qu’il achète l’est aussi : la Pi cesse d’être un point
unique dont la compromission ouvre tout le maillage.

## Le plan de données : Noise IK sur un flux onion

```
A ──► SOCKS Tor local ──► circuit ──► service onion de B
                                            │
                              handshake Noise_IK (clés de la carte)
                                            │
                                    flux applicatif chiffré
```

Le handshake est **Noise_IK_25519_ChaChaPoly_BLAKE2s** — la construction de
WireGuard : A connaît la clé statique de B (elle est dans la carte, c’est la
précondition de IK), la sienne part chiffrée dans le premier message. On
obtient l’authentification mutuelle, la confidentialité persistante, et
l’initiateur reste anonyme pour un observateur du flux.

Le premier message porte déjà le port demandé, donc une session s’ouvre en un
aller-retour. B refuse alors pour l’une de deux raisons, et elles ne se
répondent pas de la même façon : une clé statique absente de sa carte ne reçoit
**rien du tout** — un inconnu n’apprend pas qu’il existe une carte —, tandis
qu’un port non habilité reçoit un refus chiffré, parce qu’à ce stade le pair est
authentifié et mérite de savoir pourquoi.

Les primitives sont écrites en Python pur : le nœud n’a que la bibliothèque
standard, et une signature de carte décide quels pairs existent. Le débit d’un
tel chiffrement plafonne à quelques mégaoctets par seconde — au-dessus de ce
qu’un circuit Tor transporte, en dessous de ce que `bat0` pourrait porter. Quand
`cryptography` est installée sur le nœud, elle prend le relais pour le seul
chiffrement de flux ; les vecteurs de la RFC 8439 valident les deux chemins.

### Pourquoi chiffrer par-dessus Tor, qui chiffre déjà

Ce n’est pas de la redondance, les deux couches n’authentifient pas la même
chose.

* Un service onion authentifie **le service** : « ce flux va bien à la clé
  qu’annonce cette adresse ». L’adresse, elle, est une donnée d’annuaire — dans
  la baie d’aujourd’hui, un humain la recopie à la main dans une fiche. Une
  adresse fausse, périmée ou soufflée reste techniquement authentique.
* Noise authentifie **l’identité** : « ce flux va bien au nœud dont la carte
  signée porte cette clé ». Une adresse recopiée de travers ne donne alors pas
  un mauvais pair, elle ne donne aucun pair.

S’ajoutent trois bénéfices qui ne coûtent rien : la confidentialité persistante
ne dépend plus du circuit ; la session survit à un changement de transport (§
suivant) ; et les **réponses** sont authentifiées, ce que le plan de contrôle
n’avait pas avant le protocole v2.

### Chemin direct, chemin relayé

C’est la distinction Tailscale entre lien direct et relais DERP, et elle
réconcilie les deux réseaux du dépôt :

| Chemin | Transport | Latence | Quand |
| --- | --- | --- | --- |
| direct | `bat0` (802.11s, WPA3-SAE) | ~1 ms | pair à portée du maillage radio |
| relayé | flux onion | 300 ms – 2 s | tous les autres |

Le direct est tenté en premier avec un délai court (1,5 s) : un pair hors de
portée doit échouer vite et laisser la place au relais. Le chemin qui a marché
est réessayé en tête pendant une minute — assez long pour ne pas resonder à
chaque connexion, assez court pour qu’un pair qui revient à portée radio
retrouve le direct dans la minute.

Côté écoute, l’agent ne se lie jamais à `0.0.0.0` : la boucle locale porte le
chemin relayé (Tor s’y connecte quand un circuit arrive sur le second port
virtuel du service onion), et l’adresse de `bat0` porte le chemin direct. Sur un
VPS sans radio, il n’y a donc rien d’ouvert sur Internet, comme avant.

**La même session Noise** dans les deux cas : le transport change, pas la
sécurité. La dorsale 802.11s cesse d’être une fonction séparée pour devenir le
chemin rapide d’un seul réseau, et un pair proche n’a plus à payer six sauts
Tor pour être joint.

Conséquence agréable : `bat0` redevient un tuyau bête. Aujourd’hui,
[`docs/mesh.md`](mesh.md) laisse l’administration HTTPS d’un nœud joignable sur
son adresse `10.43.X.Y`, et une phrase SAE partagée entre tous les nœuds veut
dire qu’un seul nœud compromis atteint l’administration de tous les autres.
Avec le maillage, `bat0` ne porte plus que du Noise, et la même compromission
ne donne qu’un lien radio.

### Ce qui a le droit de passer

Défaut : tout est refusé, et le maillage lui-même est éteint sur une fiche
neuve. La carte porte des **habilitations**, et **les deux extrémités les
appliquent**. B refuse un handshake dont la clé statique n’est pas dans sa
propre carte, quoi qu’on ait raconté à A. C’est ce qui fait qu’un coordinateur
compromis ne suffit pas : sous verrou de maillage, il faudrait aussi K
signatures.

La fiche d’un nœud, dans **Baie virtuelle → un nœud → Maillage**, en dit deux
choses :

* les **ports présentés** — ce que ce nœud accepte d’ouvrir à ses pairs ;
* les **redirections** — un port local qui présente le port d’un pair, comme
  `ssh -L`. Une redirection déclarée ne suffit pas : le pair visé doit avoir
  ouvert ce port de son côté.

Sur un nœud Linux, `render-policy.py` traduit l’activation en exactement deux
règles nftables — entrée et sortie, un seul port, uniquement vers
`10.43.0.0/16` — parce que sans elles le coupe-circuit « Tor uniquement »
couperait le chemin direct. Le chemin relayé ne demande aucune exception : il
passe par la boucle locale et par Tor. Isoler un nœud retire la règle de sortie :
isoler veut dire « il ne sort plus », et un pair du maillage reste une sortie.

## Rotation et révocation

* **Clé de nœud** : le nœud génère la nouvelle et signe le changement avec
  l’ancienne. Le coordinateur publie une carte de `serial` supérieur ; l’ancienne
  clé passe dans `revoked` jusqu’à son `not_after`.
* **Délai de révocation** : borné par le rafraîchissement de la carte, soit le
  balayage actuel — une vague par minute, six nœuds, une baie pleine en une
  dizaine de minutes. Un nœud révoqué peut donc être joignable jusqu’à dix
  minutes. C’est une propriété du système, pas un détail : à qui a besoin de
  mieux, il faut réduire l’intervalle et payer les circuits.
* **Perte de la clé du coordinateur** : il faut re-signer les cartes, pas
  ré-enrôler les machines — les identités appartiennent aux nœuds. C’est
  strictement mieux qu’aujourd’hui, où perdre `rack.key` invalide tout d’un
  coup.

## Ce que ce réseau n’est pas

Ces limites ne se corrigent pas par du réglage. Elles se choisissent.

* **Ce n’est pas un VPN à haut débit.** Un circuit Tor plafonne à quelques
  Mb/s, partagés, avec 300 ms – 2 s d’aller-retour. Administration, `ssh`,
  `git`, API internes, sauvegardes patientes : oui. Vidéo, sauvegarde de
  plusieurs Tio, jeu : non.
* **Il n’y a pas de mode TUN, et c’est un choix.** Un flux onion est du TCP.
  Faire passer de l’IP dedans empile deux contrôles de congestion, et sous
  perte les deux réagissent au même événement : c’est le *TCP meltdown*.
  Tailscale utilise WireGuard sur UDP précisément pour l’éviter. Le seul mode
  implémenté est donc le **transfert de flux** — un port distant présenté
  localement, comme `ssh -L`. Un TUN reste techniquement possible : il
  demanderait un verbe privilégié de plus sur chaque nœud, une interface par
  plateforme, et il livrerait un tunnel dont les à-coups sous perte seraient
  reprochés au maillage plutôt qu’à la pile. L’adresse `fd7a:` reste un nom
  d’identité, pas une destination qu’on route.
* **Tor n’est pas le protocole de routage.** Il n’y a pas de routage multi-saut
  applicatif : chaque paire de pairs ouvre son propre circuit. Le multi-saut
  local reste l’affaire de `batman-adv`.
* **Pas d’UDP, pas d’ICMP** de bout en bout : Tor ne les transporte pas. Un
  `ping` sur une adresse du maillage ne veut rien dire, et les outils qui en
  dépendent ne fonctionneront pas.
* **Un nœud reste sa propre sortie.** Le maillage relie des machines entre
  elles ; il ne route pas l’Internet de l’une par l’autre. Une passerelle
  mélangerait les identités de plusieurs nœuds derrière un même Tor, ce que
  [`docs/mesh.md`](mesh.md) refuse déjà côté `batman-adv`.

## Ce qui reste vrai du maillage radio

Le 802.11s d’aujourd’hui garde ses propriétés et ses angles morts, et le
maillage les rend supportables plutôt qu’il ne les supprime :

* **Une phrase SAE unique pour tous les nœuds.** Un nœud compromis, c’est la
  dorsale entière. WPA3-SAE authentifie le *lien*, pas le pair. Avec Noise
  par-dessus, ce qu’on y gagne se limite à un tuyau.
* **`batman-adv` n’authentifie pas ses annonces d’originator** au-delà du lien.
  Qui détient la phrase peut usurper une MAC et se placer au milieu. Donc :
  `bat0` est un transport non fiable, jamais une frontière de confiance.
* **Ce qui protège vraiment reste le pare-feu.** nftables coupe tout transit
  entrant ou sortant par `bat0`, IPv6 est désactivé sur les deux interfaces, et
  aucune route hors `10.43.0.0/16` n’est installée. Ces règles ne changent pas.
* **`bat0` ne porte plus que du Noise, quand on le veut.** Le port du plan de
  données s’ajoute à ce que la dorsale transporte ; l’administration HTTPS d’un
  nœud y reste joignable comme le décrit [`docs/mesh.md`](mesh.md), et c’est
  toujours une phrase SAE partagée qui la garde. Qui veut que le lien radio ne
  soit qu’un tuyau ferme cette écoute et passe par une redirection du maillage.

## Par où on est passé

Chaque étape était utile seule et laissait le dépôt cohérent.

| Étape | Contenu | État |
| --- | --- | --- |
| 0 | Appels **et réponses** signés, nonce consommé après vérification, téléchargement de l’agent épinglé | fait |
| 1 | Clés générées sur le nœud, adresses dérivées des clés, verbes `netmap` et `mesh-rotate` signés | fait |
| 2 | Plan de données Noise IK sur flux onion, transfert de flux, habilitations aux deux bouts | fait |
| 3 | Chemin direct sur `bat0`, sélection de chemin, mémoire du dernier chemin | fait |
| 4 | Verrou de maillage K-sur-N, outils de garant | fait |
| — | Mode TUN | écarté, voir *Ce que ce réseau n’est pas* |

L’étape 0 est celle qui rendait les autres possibles : tant que la baie croyait
n’importe quelle réponse arrivant à une adresse onion, il n’y avait pas de
socle sur lequel poser une carte signée.

## Mettre en route

1. Enrôlez ou réenrôlez chaque nœud avec la commande affichée par **Baie
   virtuelle → un nœud → Préparer l’installation**. Elle porte désormais
   `--coordinator-key`, et `--mesh-lock` quand le verrou est actif. Un agent
   antérieur à 0.6 n’a pas de maillage : il faut le réinstaller.
2. Actualisez le nœud. Il annonce son identité, la baie l’enregistre et son
   adresse `fd7a:` apparaît sur sa fiche.
3. Dans **Maillage**, activez le nœud et déclarez les ports qu’il présente.
4. Sur le nœud qui doit joindre, ajoutez une redirection : port local, pair,
   port distant. La carte part à l’enregistrement ; les pairs apprennent le
   changement à leur passage suivant du balayage.
5. Vérifiez depuis le nœud initiateur :

```bash
ssh -p 2222 utilisateur@127.0.0.1
```

Le journal de l’agent indique par quel chemin la session est passée
(`direct` ou `relayé`) et en combien de millisecondes.
