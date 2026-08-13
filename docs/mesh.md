# Maillage OnionPi réel

OnionPi peut relier plusieurs routeurs par un backhaul Wi-Fi **802.11s** chiffré
avec WPA3-SAE. `batman-adv` choisit les chemins de couche 2 et permet les vrais
relais multi-sauts : un nœud C peut atteindre A en passant par B, même sans lien
radio direct entre A et C.

Ce maillage relie les **nœuds OnionPi**, pas les téléphones ou ordinateurs
ordinaires. Chaque appareil client rejoint le point d’accès OnionPi le plus
proche. Chaque nœud conserve sa propre instance de Tor, son DNS Tor et son
coupe-circuit ; le mesh ne publie jamais de route Internet directe.

## Matériel requis

Chaque OnionPi doit avoir trois liens distincts :

1. une interface WAN, généralement `eth0` ;
2. une radio pour le point d’accès client, généralement `wlan0` ;
3. une seconde radio Wi-Fi compatible 802.11s, par exemple `wlan1`.

Une radio unique ne doit pas porter simultanément le point d’accès et le
backhaul. Les contraintes de canal et les modes concurrents varient selon les
pilotes et donnent une topologie fragile. Vérifiez la seconde radio :

```bash
iw phy "$(basename "$(readlink -f /sys/class/net/wlan1/phy80211)")" info
```

La liste « Supported interface modes » doit contenir `mesh point`. Choisissez
des adaptateurs dont le pilote Linux prend aussi en charge WPA3-SAE.

## Installer deux nœuds

Tous les nœuds partagent les quatre mêmes valeurs : identifiant, mot de passe,
bande et canal. Leur adresse `10.43.X.Y/16` doit être unique. Sans
`--mesh-address`, elle est dérivée des deux derniers octets de la MAC ; une
collision reste possible et doit être corrigée explicitement.

Nœud A :

```bash
sudo env \
  ONIONPI_WIFI_PASSWORD='phrase-wifi-longue' \
  ONIONPI_ADMIN_PASSWORD='phrase-admin-longue' \
  ONIONPI_MESH_PASSWORD='phrase-mesh-commune-longue' \
  ./packaging/install.sh --yes --wan eth0 --wifi wlan0 \
  --mesh wlan1 --mesh-id Maison-Onion --mesh-address 10.43.0.1/16 \
  --mesh-band a --mesh-channel 36
```

Nœud B : utilisez la même commande, avec `--mesh-address 10.43.0.2/16`.
Ajoutez C avec `10.43.0.3/16`, etc. La bande et le canal doivent être autorisés
par le code pays configuré sur chaque Pi.

Après installation :

```bash
sudo onionpi-verify
sudo batctl -m bat0 neighbors
sudo batctl -m bat0 originators
ping 10.43.0.2
```

L’interface **Réseau → Maillage OnionPi** affiche les originators vus, y compris
ceux atteints par plusieurs sauts. L’administration HTTPS d’un nœud est
joignable sur sa propre adresse `10.43.X.Y` ; son certificat local inclut cette
adresse lors de l’installation.

## Modèle de sécurité et limites

- WPA3-SAE chiffre et authentifie le lien radio. Une phrase commune compromise
  impose la réinstallation du profil sur chaque nœud.
- `bat0` n’a ni DHCP ni route par défaut. nftables bloque tout transit entrant
  ou sortant par `bat0`; un pair ne peut pas emprunter l’Ethernet d’un autre
  nœud pour contourner Tor.
- Seuls ICMP, mDNS et l’administration HTTP(S) — plus SSH si autorisé à
  l’installation — atteignent un nœud par le mesh. SOCKS, ControlPort et
  TransPort de Tor restent fermés aux pairs.
- Tor transporte TCP et DNS vers Internet. Tor ne devient pas le protocole de
  routage du mesh : 802.11s et batman-adv assurent le multi-saut local, Tor
  assure l’anonymisation de chaque sortie.
- La sélection automatique d’une passerelle Internet batman n’est pas activée.
  Elle créerait un chemin de fuite et mélangerait les identités de plusieurs
  nœuds derrière le même Tor.

Le noyau Linux documente `batman-adv` comme un commutateur virtuel de couche 2
auquel on attache les interfaces physiques ; `bat0` est donc la seule interface
qui reçoit l’adresse IP du plan mesh.

