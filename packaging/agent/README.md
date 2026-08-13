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
| `render-policy.py` | Traduit la politique reçue en règles nftables, après revalidation. Exécuté sous root. |
| `onionpi-node-apply.sh` | Exécutant privilégié. Revalide le verbe, n’accepte aucun argument depuis le fichier de requête. |
| `systemd/` | Les trois unités: l’agent, l’unité `.path` qui surveille la file, le service root qu’elle déclenche. |
| `install-node-agent.sh` | Installe l’ensemble et publie le service onion. |

## Installation

Dans l’interface OnionPi : **Baie virtuelle → Ajouter un nœud → distant**.
Téléchargez l’archive proposée, copiez-la sur la machine, puis :

```bash
tar xzf onionpi-node-agent.tar.gz
cd onionpi-node-agent
sudo ./install-node-agent.sh --node <id> --token <jeton> --client-key <clé>
```

Le script affiche l’adresse `.onion` du nœud à la fin. Recopiez-la dans la
fiche du nœud : la baie ne peut pas la deviner, et c’est voulu.

## Comment la baie le joint

La baie compose l’adresse `.onion` du nœud à travers son propre Tor. Deux
verrous indépendants la protègent :

1. **Autorisation client v3.** Le descripteur du service est chiffré pour la
   clé x25519 de la baie. Sans elle, l’adresse ne se résout même pas.
2. **Signature.** Chaque appel porte un HMAC-SHA256 sur le verbe, un
   horodatage, un nonce et l’empreinte du corps. Horodatage périmé ou nonce
   déjà vu : refusé.

Le jeton et la clé sont dérivés du secret de baie ; ils ne sont stockés nulle
part côté OnionPi. Renouveler un nœud incrémente un compteur, ce qui invalide
d’un coup l’ancien jeton et l’ancienne clé.

## Le pare-feu du nœud

La politique par défaut, appliquée dès la première synchronisation :

* **sortie** interdite sauf le trafic du démon Tor. Une application qui ignore
  le proxy n’atteint rien ;
* **entrée** interdite sauf les ports gardés ouverts — le 22 par défaut, pour
  qu’un VPS reste administrable ;
* **isolement** (règle « Accès : bloqué ») : les applications perdent en plus
  l’accès au port SOCKS. La machine reste joignable et son service onion reste
  publié, mais elle ne sort plus.

Conséquence à connaître : en mode `tor-only`, `apt` et les mises à jour
n’aboutissent plus sans passer par Tor. Réglez la sortie sur **directe** le
temps d’une maintenance, ou configurez `apt` sur le proxy SOCKS local.

## Désinstallation

```bash
sudo systemctl disable --now onionpi-node-agent.service onionpi-node-apply.path
sudo nft delete table inet onionpi_node
sudo rm -rf /usr/local/lib/onionpi-node /usr/local/sbin/onionpi-node-apply.sh \
  /etc/onionpi-node /var/lib/onionpi-node /var/lib/onionpi-node-privileged \
  /etc/systemd/system/onionpi-node-*
sudo sed -i '/^# >>> OnionPi node agent$/,/^# <<< OnionPi node agent$/d' /etc/tor/torrc
sudo systemctl daemon-reload && sudo systemctl restart tor
```
