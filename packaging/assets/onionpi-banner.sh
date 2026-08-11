#!/usr/bin/env bash
# The one place the OnionPi ASCII identity is drawn. Everything that shows a
# banner — the boot console, the login prompt, the MOTD, onionpi-status —
# sources this file, so the design only ever exists once.
#
# Usage: onionpi-banner [--plain]
#   --plain drops every escape sequence, for /etc/issue and for log files.

onionpi_palette() {
  if [[ "${1:-}" == "--plain" ]] || { [[ ! -t 1 ]] && [[ "${ONIONPI_FORCE_COLOR:-0}" != "1" ]]; }; then
    ONIONPI_BULB="" ONIONPI_WORD="" ONIONPI_DIM="" ONIONPI_ACCENT="" ONIONPI_RESET=""
  else
    # 256-colour violet for the bulb, matching the web interface, a green
    # sprout, and a dim grey for the tagline.
    ONIONPI_BULB=$'\033[38;5;141m'
    ONIONPI_WORD=$'\033[1;38;5;183m'
    ONIONPI_DIM=$'\033[38;5;245m'
    ONIONPI_ACCENT=$'\033[38;5;114m'
    ONIONPI_RESET=$'\033[0m'
  fi
}

onionpi_banner() {
  onionpi_palette "${1:-}"
  local b="$ONIONPI_BULB" w="$ONIONPI_WORD" d="$ONIONPI_DIM" a="$ONIONPI_ACCENT" r="$ONIONPI_RESET"
  printf '%s\n' \
"" \
"         ${a}\\ | /${r}" \
"        ${b}.-'-'-.${r}" \
"      ${b}.'       '.${r}    ${w} ___       _          ___ _${r}" \
"     ${b}/   .---.   \\${r}   ${w}/ _ \\ _ _ (_) ___ _ _ | _ (_)${r}" \
"    ${b};   /     \\   ;${r}  ${w}| (_) | ' \\| |/ _ \\ ' \\|  _/ |${r}" \
"    ${b}|  ;   ${a}o${b}   ;  |${r}  ${w} \\___/|_||_|_|\\___/_||_|_| |_|${r}" \
"    ${b};   \\     /   ;${r}" \
"     ${b}\\   '---'   /${r}   ${d}routeur Tor pour Raspberry Pi${r}" \
"      ${b}'.       .'${r}    ${d}tout le trafic sort par Tor${r}" \
"        ${b}'-._.-'${r}" \
""
}

# Sourced by the other scripts; run directly it simply draws the banner.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  onionpi_banner "${1:-}"
fi
