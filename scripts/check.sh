#!/usr/bin/env bash
set -Eeuo pipefail

# Everything the CI runs, in one command, in the order that fails fastest.
#
#   ./scripts/check.sh          tout
#   ./scripts/check.sh backend  un seul groupe

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

ONLY="${1:-all}"
# Absolute, so a step that changes directory keeps using the same interpreter.
PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
RUFF="$PROJECT_ROOT/.venv/bin/ruff"
[[ -x "$RUFF" ]] || RUFF="$(command -v ruff || true)"

failed=()
step() {
  local name="$1"; shift
  printf '\n\033[1m▸ %s\033[0m\n' "$name"
  if "$@"; then
    printf '  ok\n'
  else
    printf '  ÉCHEC\n'
    failed+=("$name")
  fi
}

if [[ "$ONLY" == all || "$ONLY" == meta ]]; then
  step "Cohérence des versions" ./scripts/check-version.sh
  step "Secrets" ./scripts/check-secrets.sh
fi

if [[ "$ONLY" == all || "$ONLY" == backend ]]; then
  if [[ -n "$RUFF" ]]; then
    step "ruff" "$RUFF" check backend
  else
    printf '\nruff absent: pip install -r backend/requirements-dev.txt\n' >&2
  fi
  step "pytest" bash -c "cd '$PROJECT_ROOT/backend' && '$PYTHON' -m pytest -q"
fi

if [[ "$ONLY" == all || "$ONLY" == frontend ]]; then
  step "tsc" npm run --prefix frontend check
  step "vite build" npm run --prefix frontend build
fi

if [[ "$ONLY" == all || "$ONLY" == shell ]]; then
  if command -v shellcheck >/dev/null; then
    # shellcheck disable=SC2046
    step "shellcheck" shellcheck --severity=warning --shell=bash $(git ls-files '*.sh')
  else
    printf '\nshellcheck absent: brew install shellcheck\n' >&2
  fi
  for script in $(git ls-files '*.sh'); do
    bash -n "$script" || failed+=("bash -n $script")
  done
fi

printf '\n'
if (( ${#failed[@]} )); then
  printf 'Échecs: %s\n' "${failed[*]}" >&2
  exit 1
fi
printf 'Tout est vert.\n'
