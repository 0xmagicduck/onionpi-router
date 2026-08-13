# Agent de nœud OnionPi

Ce dossier contient tout ce qui s’installe sur une machine **distante** — un
VPS, un serveur à la maison, une seconde Raspberry Pi — pour qu’elle apparaisse
dans la baie virtuelle d’un OnionPi et que sa sortie réseau passe par Tor.

Rien ici ne s’installe sur la Raspberry Pi elle-même. `packaging/install.sh`
se contente de recopier ce dossier dans la version déployée, pour que
l’interface puisse vous le remettre.

## Ce que ça met en place

| Fichier | Rôle |
| --- | --- |
| `onionpi-node-agent.py` | Agent HTTP sur `127.0.0.1` uniquement. Aucun privilège. Vérifie la signature de chaque appel. |
| `onionpi_mesh.py` | Ed25519, X25519, ChaCha20-Poly1305, Noise IK, cartes signées, verrou de maillage. Bibliothèque standard uniquement. |
| `onionpi_mesh_runtime.py` | Plan de données du maillage : répondeur, redirections locales, choix entre chemin direct et chemin relayé. |
| `render-policy.py` | Traduit la politique reçue en règles nftables, après revalidation. Exécuté sous root. |
| `onionpi-node-apply.sh` | Exécutant privilégié. Revalide le verbe, n’accepte aucun argument depuis le fichier de requête. |
| `systemd/` | Les trois unités: l’agent, l’unité `.path` qui surveille la file, le service root qu’elle déclenche. |
| `bootstrap-node.sh` / `.ps1` | Télécharge la source depuis GitHub et refuse de l’exécuter tant qu’elle ne correspond pas à l’empreinte fournie par la baie. |
| `install-node-agent.sh` | Installation Linux (Debian, Ubuntu, Raspberry Pi OS). |
| `install-node-agent-macos.sh` | Installation macOS avec Homebrew, launchd et PF. |
| `install-node-agent-windows.ps1` | Installation Windows avec tâches système ; sortie directe sûre tant qu’un tunnel TUN manque. |

## Installation

Dans l’interface OnionPi : **Baie virtuelle → Ajouter un nœud → distant**,
ouvrez la fiche, choisissez Linux, macOS ou Windows, puis copiez la commande
proposée. Elle télécharge le bootstrap depuis GitHub, récupère le dépôt et
installe Tor, Python, le service onion et l’agent.

Rien de ce qui vient de GitHub n’est exécuté sur la foi du téléchargement :

1. la commande vérifie l’empreinte du bootstrap avant de le lancer ;
2. le bootstrap vérifie que le `packaging/agent/` extrait de l’archive est
   exactement celui que la baie exécute (`--bundle-digest`), et s’arrête sinon.

Les deux empreintes viennent de l’appliance, installée depuis une publication
signée : elles sont la référence, le téléchargement ne l’est pas. Sans
appliance de référence — développement, installation hors ligne — il faut
passer `--unverified-bundle` explicitement ; sans lui le bootstrap refuse.

Le jeton n’est **pas** dans la commande. L’installateur le demande sur le
terminal (`--token-stdin`), donc il n’apparaît ni dans `ps`, où tout compte de
la machine pourrait le lire, ni dans l’historique du shell. L’interface
l’affiche dans son propre champ, à coller à l’invite.

Pour examiner chaque fichier avant exécution, utilisez **Archive hors ligne**,
puis sous Linux :

```bash
tar xzf onionpi-node-agent.tar.gz
cd onionpi-node-agent
sudo ./install-node-agent.sh --node <id> --token-stdin --client-key <clé>
```

Le script affiche l’adresse `.onion` du nœud à la fin. Recopiez-la dans la
fiche du nœud : la baie ne peut pas la deviner, et c’est voulu.

### Différences selon le système

- **Linux** applique la politique complète avec nftables et systemd.
- **macOS** installe Tor et Python avec Homebrew, lance les services avec
  launchd et applique un routage transparent avec une ancre PF dédiée : TCP et
  DNS entrent dans les ports transparents de Tor, le reste d’Internet est
  bloqué. Le LAN local reste joignable et les ports Tor sont choisis sans gêner
  une autre instance. Une ancienne installation `0.4.1` ne faisait que bloquer
  les applications ; relancez l’installateur pour la remplacer.
- **Windows** installe Python avec winget et le Tor Expert Bundle officiel. Le
  mode direct et l’administration distante fonctionnent. `tor-only` est refusé
  explicitement tant qu’un transport TUN vérifié n’est pas disponible : le
  pare-feu Windows sait bloquer une sortie, mais il ne sait pas convertir le
  TCP arbitraire en SOCKS et ne doit jamais couper la machine en prétendant
  l’avoir routée par Tor.

## Comment la baie le joint

La baie compose l’adresse `.onion` du nœud à travers son propre Tor. Trois
verrous indépendants la protègent :

1. **Autorisation client v3.** Le descripteur du service est chiffré pour la
   clé x25519 de la baie. Sans elle, l’adresse ne se résout même pas.
2. **Signature de l’appel.** Chaque appel porte un HMAC-SHA256 sur la version
   du protocole, l’identifiant du nœud, le verbe, un horodatage, un nonce et
   l’empreinte du corps. Horodatage périmé ou nonce déjà vu : refusé. Le nonce
   n’est retenu qu’après vérification de la signature, sinon un inconnu videra
   la mémoire des nonces avec des appels non signés.
3. **Signature de la réponse.** Le nœud signe ce qu’il répond, avec une clé
   distincte et le nonce de l’appel. Tor authentifie le *service*, pas
   l’agent : sans cette signature, ce qui répond à l’adresse — un squatteur du
   port local, une adresse onion recopiée de travers dans une fiche — pourrait
   inventer une charge, un amorçage Tor ou un journal, et la baie les
   classerait comme des faits.

Le jeton et la clé sont dérivés du secret de baie ; ils ne sont stockés nulle
part côté OnionPi. Renouveler un nœud incrémente un compteur, ce qui invalide
d’un coup l’ancien jeton et l’ancienne clé.

### Protocole v2

La signature des réponses et l’identifiant du nœud dans le canonique arrivent
avec la **version 2** du protocole, et un agent v1 ne sait pas les produire.
Une appliance à jour affiche alors « réponse non authentifiée » sur les nœuds
restés en v1 : réinstallez leur agent depuis **Préparer l’installation**, la
commande est réaffichable. La baie ne se rabat jamais sur la v1 — un repli
déclenché par une réponse est un repli offert à qui la fabrique.

## Le maillage

L’agent 0.6 engendre au premier démarrage une identité **Ed25519** et une clé
statique **X25519**, dans `identity.json` (0600), et n’en transmet que les
moitiés publiques. C’est ce qui permet à deux nœuds de se parler sans faire
confiance à la baie : elle les **autorise**, elle ne peut plus les **être**.

Deux options d’installation le pilotent, toutes deux affichées par la baie dans
la commande d’enrôlement :

* `--coordinator-key ed25519:…` épingle la clé qui signe les cartes du réseau.
  Sans elle, l’agent tourne mais le maillage reste éteint : un nœud qui
  apprendrait cette clé d’une carte accepterait la première carte venue.
* `--mesh-lock K:clé,clé,…` écrit `mesh.lock`, propriété de root. Une clé de
  pair **nouvelle** n’est alors acceptée que contresignée par K garants sur N.

L’installateur ouvre aussi un second port virtuel sur le service onion du nœud
(9081 par défaut, `--mesh-port`) : c’est le plan de données. L’agent n’écoute
jamais sur `0.0.0.0` — la boucle locale pour le chemin relayé, l’adresse de
`bat0` pour le chemin direct quand la machine a une radio maillée.

Détails et modèle de menace : [`docs/onionmesh.md`](../../docs/onionmesh.md).

## Le pare-feu du nœud

Sous Linux et macOS, la politique par défaut appliquée à la première
synchronisation :

* **sortie** directe interdite ; Linux exige le proxy Tor, macOS redirige TCP
  et DNS dans Tor ;
* **entrée** interdite sauf les ports gardés ouverts — le 22 par défaut, pour
  qu’un VPS reste administrable ;
* **isolement** (règle « Accès : bloqué ») : les applications perdent en plus
  l’accès au port SOCKS. La machine reste joignable et son service onion reste
  publié, mais elle ne sort plus.

Quand le maillage est activé, Linux ajoute exactement deux règles : le port du
plan de données, en entrée et en sortie, uniquement vers `10.43.0.0/16`. Sans
elles le coupe-circuit couperait le chemin direct. Isoler un nœud retire la
règle de sortie : un pair du maillage reste une sortie.

Windows refuse cette politique et conserve sa sortie directe tant qu’un tunnel
TUN vérifié n’est pas installé.

Conséquence à connaître : sous Linux, `apt` et les mises à jour n’aboutissent
plus sans proxy SOCKS. Sous macOS, TCP et DNS sont transparents mais UDP/QUIC
restent bloqués, puisque Tor ne les transporte pas. Réglez la sortie sur
**directe** le temps d’une maintenance qui exige un protocole incompatible.

### Diagnostic macOS

Les chemins contiennent des espaces et doivent rester entre guillemets :

```bash
tail -n 80 "/Library/Application Support/OnionPi Node/log/tor.stdout.log"
tail -n 80 "/Library/Application Support/OnionPi Node/log/tor.stderr.log"
launchctl print system/com.onionpi.node.tor
```

## Désinstallation

La commande ci-dessous concerne Linux. Sur macOS, retirez les trois services
`com.onionpi.node.*`, l’ancre PF et `/Library/Application Support/OnionPi Node`.
Sur Windows, retirez les trois tâches `OnionPi Node *`, le groupe de règles de
pare-feu `OnionPi Node` et `%ProgramData%\OnionPi Node`.

```bash
sudo systemctl disable --now onionpi-node-agent.service onionpi-node-apply.path
sudo nft delete table inet onionpi_node
sudo rm -rf /usr/local/lib/onionpi-node /usr/local/sbin/onionpi-node-apply.sh \
  /etc/onionpi-node /var/lib/onionpi-node /var/lib/onionpi-node-privileged \
  /etc/systemd/system/onionpi-node-*
sudo sed -i '/^# >>> OnionPi node agent$/,/^# <<< OnionPi node agent$/d' /etc/tor/torrc
sudo systemctl daemon-reload && sudo systemctl restart tor
```
