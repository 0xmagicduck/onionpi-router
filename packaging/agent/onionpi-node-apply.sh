#!/usr/bin/env bash
# Exécutant privilégié du nœud OnionPi.
#
# Déclenché par onionpi-node-apply.path quand l'agent écrit une requête. Le
# fichier de requête est une entrée hostile: le verbe est revalidé ici contre
# la liste ci-dessous, aucun argument n'en est extrait, et la politique elle-
# même est relue puis revalidée par render-policy.py avant d'atteindre nft.
#
# C'est ce fichier qui est la frontière de sécurité, pas l'agent.
set -Eeuo pipefail

STATE_DIR=/var/lib/onionpi-node
RESULT_DIR=/var/lib/onionpi-node-privileged
REQUEST="$STATE_DIR/apply.request"
RESULT="$RESULT_DIR/apply.result"
POLICY="$STATE_DIR/policy.json"
APPLIED="$RESULT_DIR/policy.applied"
RULES="$RESULT_DIR/policy.nft"
RENDER=/usr/local/lib/onionpi-node/render-policy.py

install -d -m 0750 "$RESULT_DIR"

answer() {
  # La réponse porte le nonce de la requête: l'agent ne peut pas prendre le
  # résultat d'une action précédente pour celui de la sienne.
  printf '%s %s %s\n' "$1" "$2" "$3" >"$RESULT.tmp"
  chmod 0644 "$RESULT.tmp"
  mv -f "$RESULT.tmp" "$RESULT"
}

[[ -r "$REQUEST" ]] || exit 0
read -r NONCE ACTION _ <"$REQUEST" || exit 0

[[ "$NONCE" =~ ^[0-9a-f]{8,32}$ ]] || exit 0

case "$ACTION" in
  policy|restart-tor|reboot) ;;
  *)
    answer "$NONCE" error "Action refusée"
    exit 0
    ;;
esac

case "$ACTION" in
  policy)
    if [[ ! -s "$POLICY" ]]; then
      answer "$NONCE" error "Aucune politique à appliquer"
      exit 0
    fi
    if ! DIGEST="$(python3 "$RENDER" "$POLICY" "$RULES")"; then
      answer "$NONCE" error "Politique refusée"
      exit 0
    fi
    # nft -c relit le fichier sans rien installer. Une erreur de syntaxe ne doit
    # jamais laisser la machine à moitié filtrée.
    if ! nft -c -f "$RULES"; then
      answer "$NONCE" error "Jeu de règles invalide"
      exit 0
    fi
    nft "delete table inet onionpi_node" 2>/dev/null || true
    if ! nft -f "$RULES"; then
      answer "$NONCE" error "Application du pare-feu refusée"
      exit 0
    fi
    printf '{"digest":"%s","egress":"%s","applied_at":%s}\n' \
      "$DIGEST" \
      "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["egress"])' "$POLICY")" \
      "$(date +%s)" >"$APPLIED.tmp"
    chmod 0644 "$APPLIED.tmp"
    mv -f "$APPLIED.tmp" "$APPLIED"
    answer "$NONCE" ok "Pare-feu appliqué"
    ;;
  restart-tor)
    if systemctl restart tor@default.service 2>/dev/null || systemctl restart tor.service; then
      answer "$NONCE" ok "Tor redémarré"
    else
      answer "$NONCE" error "Redémarrage de Tor refusé"
    fi
    ;;
  reboot)
    answer "$NONCE" ok "Redémarrage en cours"
    systemctl reboot
    ;;
esac
