#!/usr/bin/env bash
# Installe l'agent OnionPi sur un nœud distant (VPS, serveur, seconde machine).
#
# À lancer sur le nœud, en root, avec les paramètres affichés par l'interface de
# la baie. Le script installe Tor, publie un service onion v3 chiffré pour la
# seule clé de la baie, installe l'agent sur la boucle locale, puis affiche
# l'adresse .onion à recopier dans l'interface.
#
# Aucun port n'est ouvert sur Internet: l'agent n'écoute que sur 127.0.0.1.
set -Eeuo pipefail

NODE_ID=""
TOKEN=""
TOKEN_STDIN=0
PORT=9080
MESH_PORT=9081
CLIENT_KEY=""
CLIENT_NAME="baie"
COORDINATOR_KEY=""
MESH_LOCK=""
ASSUME_YES=0

usage() {
  cat <<'USAGE'
Usage: sudo ./install-node-agent.sh --node <id> --token-stdin [options]

  --node <id>          Identifiant du nœud, 16 caractères hexadécimaux.
  --token-stdin        Lire le jeton sur l'entrée standard, ou le demander sur
                       le terminal. À préférer toujours: un jeton passé en
                       argument est lisible dans « ps » par tout compte de la
                       machine, et reste dans l'historique du shell.
  --token <jeton>      Jeton partagé, 64 caractères hexadécimaux. Déconseillé.
  --port <port>        Port de l'agent sur la boucle locale (défaut: 9080).
  --mesh-port <port>   Port du plan de données du maillage (défaut: 9081).
  --client-key <clé>   Clé publique x25519 de la baie, en base32. Sans elle,
                       l'adresse onion est le seul secret: fortement déconseillé.
  --client-name <nom>  Nom du fichier d'autorisation (défaut: baie).
  --coordinator-key <clé>
                       Clé publique Ed25519 du coordinateur, « ed25519:… ».
                       Elle est épinglée ici: sans elle le maillage reste
                       éteint, et le nœud n'accepte aucune carte de pairs.
  --mesh-lock K:clé,clé,…
                       Verrou de maillage K-sur-N. Une clé de pair nouvelle
                       n'est alors acceptée que contresignée par K garants.
  --yes                Ne pas demander de confirmation.

Les valeurs sont affichées par « Baie virtuelle » dans l'interface OnionPi.
USAGE
}

while (($#)); do
  case "$1" in
    --node) NODE_ID="${2:-}"; shift 2 ;;
    --token) TOKEN="${2:-}"; shift 2 ;;
    --token-stdin) TOKEN_STDIN=1; shift ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --mesh-port) MESH_PORT="${2:-}"; shift 2 ;;
    --client-key) CLIENT_KEY="${2:-}"; shift 2 ;;
    --client-name) CLIENT_NAME="${2:-}"; shift 2 ;;
    --coordinator-key) COORDINATOR_KEY="${2:-}"; shift 2 ;;
    --mesh-lock) MESH_LOCK="${2:-}"; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Option inconnue: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

(( EUID == 0 )) || { printf 'À lancer en root: sudo %s\n' "$0" >&2; exit 1; }

if (( TOKEN_STDIN )); then
  [[ -z "$TOKEN" ]] || { printf 'Choisissez --token ou --token-stdin.\n' >&2; exit 2; }
  if [[ -t 0 ]]; then
    # Lu sur le terminal et non affiché: ni « ps », ni l'historique, ni le
    # défilement de la console ne gardent le jeton.
    printf 'Jeton du nœud (collé depuis « Préparer l’installation »): ' >&2
    read -rs TOKEN
    printf '\n' >&2
  else
    read -r TOKEN
  fi
elif [[ -n "$TOKEN" ]]; then
  printf 'Attention: --token place le jeton dans « ps » et dans\n' >&2
  printf 'l’historique du shell. Préférez --token-stdin.\n' >&2
fi

[[ "$NODE_ID" =~ ^[0-9a-f]{16}$ ]] || { printf '--node invalide.\n' >&2; exit 2; }
[[ "$TOKEN" =~ ^[0-9a-f]{64}$ ]] || { printf 'Jeton invalide.\n' >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( PORT >= 1 && PORT <= 65535 )) \
  || { printf '--port invalide.\n' >&2; exit 2; }
[[ "$MESH_PORT" =~ ^[0-9]{1,5}$ ]] && (( MESH_PORT >= 1 && MESH_PORT <= 65535 )) \
  || { printf '--mesh-port invalide.\n' >&2; exit 2; }
(( MESH_PORT != PORT )) || { printf '--mesh-port doit différer de --port.\n' >&2; exit 2; }
[[ -z "$CLIENT_KEY" || "$CLIENT_KEY" =~ ^[A-Z2-7]{52}$ ]] \
  || { printf '--client-key invalide: 52 caractères base32.\n' >&2; exit 2; }
[[ "$CLIENT_NAME" =~ ^[A-Za-z0-9_-]{1,32}$ ]] \
  || { printf '--client-name invalide.\n' >&2; exit 2; }
[[ -z "$COORDINATOR_KEY" || "$COORDINATOR_KEY" =~ ^ed25519:[0-9a-f]{64}$ ]] \
  || { printf '--coordinator-key invalide: « ed25519: » et 64 caractères hexadécimaux.\n' >&2; exit 2; }
if [[ -n "$MESH_LOCK" ]]; then
  [[ -n "$COORDINATOR_KEY" ]] \
    || { printf '--mesh-lock sans --coordinator-key n’a rien à verrouiller.\n' >&2; exit 2; }
  [[ "$MESH_LOCK" =~ ^([1-8]):(ed25519:[0-9a-f]{64}(,ed25519:[0-9a-f]{64})*)$ ]] \
    || { printf '--mesh-lock invalide: attendu « K:clé,clé,… ».\n' >&2; exit 2; }
  LOCK_THRESHOLD="${BASH_REMATCH[1]}"
  IFS=',' read -r -a LOCK_TRUSTEES <<<"${BASH_REMATCH[2]}"
  (( LOCK_THRESHOLD <= ${#LOCK_TRUSTEES[@]} )) \
    || { printf 'Seuil du verrou supérieur au nombre de garants.\n' >&2; exit 2; }
fi

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HS_DIR=/var/lib/tor/onionpi-node
LIB_DIR=/usr/local/lib/onionpi-node
STATE_DIR=/var/lib/onionpi-node
RESULT_DIR=/var/lib/onionpi-node-privileged
TORRC=/etc/tor/torrc
MARK_START='# >>> OnionPi node agent'
MARK_END='# <<< OnionPi node agent'

if [[ -z "$CLIENT_KEY" ]]; then
  printf '\nAttention: sans --client-key, quiconque apprend l’adresse .onion\n'
  printf 'peut atteindre l’agent. Seule la signature le protège alors.\n\n'
fi

if (( ! ASSUME_YES )); then
  printf 'Installer l’agent OnionPi sur cette machine ? [o/N] '
  read -r reply
  [[ "$reply" =~ ^[oOyY]$ ]] || { printf 'Abandon.\n'; exit 0; }
fi

command -v apt-get >/dev/null || {
  printf 'Distribution non gérée: installez tor, nftables et python3 à la main,\n' >&2
  printf 'puis relancez ce script.\n' >&2
  exit 1
}

printf '▸ Paquets\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq tor nftables python3 >/dev/null

printf '▸ Compte de service\n'
if ! id -u onionpi-node >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin onionpi-node
fi

install -d -m 0750 -o onionpi-node -g onionpi-node "$STATE_DIR"
install -d -m 0750 -o root -g onionpi-node "$RESULT_DIR"
install -d -m 0755 "$LIB_DIR" /etc/onionpi-node

printf '▸ Programmes\n'
install -m 0755 "$SOURCE_DIR/onionpi-node-agent.py" "$LIB_DIR/onionpi-node-agent.py"
install -m 0755 "$SOURCE_DIR/render-policy.py" "$LIB_DIR/render-policy.py"
# À côté de l'agent, donc importés par le répertoire du programme. Aucune
# dépendance externe: tout le maillage tient dans la bibliothèque standard.
install -m 0644 "$SOURCE_DIR/onionpi_mesh.py" "$LIB_DIR/onionpi_mesh.py"
install -m 0644 "$SOURCE_DIR/onionpi_mesh_runtime.py" "$LIB_DIR/onionpi_mesh_runtime.py"
install -m 0755 "$SOURCE_DIR/onionpi-node-apply.sh" /usr/local/sbin/onionpi-node-apply.sh
install -m 0644 "$SOURCE_DIR/systemd/onionpi-node-agent.service" \
  /etc/systemd/system/onionpi-node-agent.service
install -m 0644 "$SOURCE_DIR/systemd/onionpi-node-apply.service" \
  /etc/systemd/system/onionpi-node-apply.service
install -m 0644 "$SOURCE_DIR/systemd/onionpi-node-apply.path" \
  /etc/systemd/system/onionpi-node-apply.path

printf '▸ Identité du nœud\n'
# Le jeton est un secret partagé: root l'écrit, le compte de service le lit.
umask 027
cat >/etc/onionpi-node/agent.env <<EOF
NODE_ID=$NODE_ID
TOKEN=$TOKEN
PORT=$PORT
ONIONPI_NODE_PORT=$PORT
MESH_PORT=$MESH_PORT
COORDINATOR_KEY=$COORDINATOR_KEY
EOF
chown root:onionpi-node /etc/onionpi-node/agent.env
chmod 0640 /etc/onionpi-node/agent.env
umask 022

# Le verrou de maillage appartient à root et l'agent ne fait que le lire: un
# verrou que le coordinateur pourrait remplacer ne verrouillerait rien.
if [[ -n "$MESH_LOCK" ]]; then
  {
    printf '{"version":1,"threshold":%s,"trustees":[' "$LOCK_THRESHOLD"
    for index in "${!LOCK_TRUSTEES[@]}"; do
      (( index == 0 )) || printf ','
      printf '"%s"' "${LOCK_TRUSTEES[index]}"
    done
    printf ']}\n'
  } >/etc/onionpi-node/mesh.lock
  chown root:onionpi-node /etc/onionpi-node/mesh.lock
  chmod 0644 /etc/onionpi-node/mesh.lock
else
  rm -f /etc/onionpi-node/mesh.lock
fi

# La file de requêtes privilégiées, sur le modèle de la Raspberry Pi: l'agent
# écrit, root répond à côté, dans un répertoire que l'agent ne peut pas modifier.
[[ -f "$STATE_DIR/apply.request" ]] || : >"$STATE_DIR/apply.request"
chown onionpi-node:onionpi-node "$STATE_DIR/apply.request"
chmod 0640 "$STATE_DIR/apply.request"
[[ -f "$RESULT_DIR/apply.result" ]] || : >"$RESULT_DIR/apply.result"
chmod 0644 "$RESULT_DIR/apply.result"

printf '▸ Service onion\n'
install -d -m 0700 -o debian-tor -g debian-tor "$HS_DIR"
if [[ -n "$CLIENT_KEY" ]]; then
  install -d -m 0700 -o debian-tor -g debian-tor "$HS_DIR/authorized_clients"
  printf 'descriptor:x25519:%s\n' "$CLIENT_KEY" \
    >"$HS_DIR/authorized_clients/$CLIENT_NAME.auth"
  chown debian-tor:debian-tor "$HS_DIR/authorized_clients/$CLIENT_NAME.auth"
  chmod 0600 "$HS_DIR/authorized_clients/$CLIENT_NAME.auth"
else
  rm -rf -- "$HS_DIR/authorized_clients"
fi

# Bloc délimité: une réinstallation le remplace, elle ne l'empile pas.
if grep -Fq "$MARK_START" "$TORRC" 2>/dev/null; then
  sed -i "/^${MARK_START}$/,/^${MARK_END}$/d" "$TORRC"
fi
cat >>"$TORRC" <<EOF
$MARK_START
# Généré par install-node-agent.sh. Ne pas modifier à la main.
ControlPort 9051
CookieAuthentication 1
CookieAuthFileGroupReadable 1
HiddenServiceDir $HS_DIR/
HiddenServiceVersion 3
HiddenServicePort $PORT 127.0.0.1:$PORT
HiddenServicePort $MESH_PORT 127.0.0.1:$MESH_PORT
$MARK_END
EOF

systemctl daemon-reload
systemctl restart tor
systemctl enable --now onionpi-node-apply.path >/dev/null
systemctl enable --now onionpi-node-agent.service >/dev/null
systemctl restart onionpi-node-agent.service

printf '▸ Publication\n'
ADDRESS=""
for _ in $(seq 1 30); do
  if [[ -s "$HS_DIR/hostname" ]]; then
    ADDRESS="$(tr -d '[:space:]' <"$HS_DIR/hostname")"
    break
  fi
  sleep 1
done

if [[ -z "$ADDRESS" ]]; then
  printf '\nTor n’a pas encore publié l’adresse. Consultez « journalctl -u tor -n 50 »\n' >&2
  printf 'puis relisez %s/hostname.\n' "$HS_DIR" >&2
  exit 1
fi

cat <<EOF

Agent installé.

  Adresse du nœud : $ADDRESS
  Port de l’agent : $PORT
  Port du maillage : $MESH_PORT${COORDINATOR_KEY:+ (coordinateur épinglé)}${MESH_LOCK:+, verrou $LOCK_THRESHOLD-sur-${#LOCK_TRUSTEES[@]}}

Recopiez cette adresse dans « Baie virtuelle » → le nœud « $NODE_ID », puis
actualisez-le. Tant que la baie ne l’a pas, le nœud reste en attente.

Le pare-feu du nœud n’est appliqué qu’à la première politique reçue depuis la
baie. Par défaut, cette politique interdit toute sortie qui ne passe pas par
Tor et laisse le port 22 joignable.
EOF
