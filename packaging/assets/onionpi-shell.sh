# Sourced, never executed: no shebang. Raspberry Pi OS logs in with bash,
# which is what the hyphenated helper names below need.
# shellcheck shell=bash
# Installed as /etc/profile.d/onionpi.sh. Gives every interactive shell on the
# Pi the same identity as the boot console: violet prompt, an onion glyph, and
# the shortcuts an operator actually reaches for.
#
# Nothing here is required for OnionPi to work; a user who prefers their own
# prompt only has to set PS1 after this file has run.

[ -n "${BASH_VERSION:-}" ] || return 0
case "$-" in *i*) ;; *) return 0 ;; esac

onionpi-banner() { /usr/local/lib/onionpi/onionpi-banner.sh "$@"; }

alias onionpi-log='journalctl -u onionpi -u tor -n 80 --no-pager'
alias onionpi-tor='journalctl -fu tor'
alias onionpi-clients='cat /var/lib/misc/dnsmasq.leases'

if [ -z "${ONIONPI_KEEP_PROMPT:-}" ] && [ -t 1 ]; then
  if [ "$(id -u)" -eq 0 ]; then
    __onionpi_mark='\[\033[38;5;203m\]#'
  else
    __onionpi_mark='\[\033[38;5;114m\]@'
  fi
  # user at host, then the working directory, then the onion glyph as the
  # prompt character so a stray screenshot still says which machine this is.
  PS1="\[\033[38;5;183m\]\u${__onionpi_mark}\[\033[38;5;141m\]\h\[\033[0m\] \[\033[38;5;245m\]\w\[\033[0m\]\n\[\033[38;5;141m\](@)\[\033[0m\] "
  unset __onionpi_mark
fi
