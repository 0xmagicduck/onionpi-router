#!/usr/bin/env bash
set -Eeuo pipefail

# Everything the CI runs, in one command, in the order that fails fastest.
#
#   ./scripts/check.sh            tout
#   ./scripts/check.sh backend    un seul groupe
#
# Groupes: meta | backend | frontend | shell | workflows

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
  step "Budget Raspberry Pi" ./scripts/check-resource-budget.sh
  # Through "$PYTHON", not the shebang: the shebang picks whatever python3 the
  # PATH offers, which on a developer's machine is rarely the one that has
  # FastAPI installed — and the failure then reads as a broken contract.
  step "Types API générés" "$PYTHON" ./scripts/generate-api-types.py
  # A workflow that does not parse fails in zero seconds with "workflow file
  # issue" and no log to read, so it is worth catching here.
  step "YAML des workflows" "$PYTHON" - <<'PY'
import glob
import sys

try:
    import yaml
except ImportError:
    print("pyyaml absent, contrôle ignoré")
    sys.exit(0)

status = 0
for path in sorted(glob.glob(".github/**/*.yml", recursive=True)):
    try:
        yaml.safe_load(open(path, encoding="utf-8"))
    except yaml.YAMLError as error:
        print(f"{path}: {error}")
        status = 1
sys.exit(status)
PY
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
  step "Exécution d’une version publiée" packaging/tests/release-runtime.sh
  step "Mise à niveau non interactive" packaging/tests/upgrade-noninteractive.sh
  step "Matrice d’interruption" packaging/tests/update-interruption-matrix.sh
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

if [[ "$ONLY" == all || "$ONLY" == workflows ]]; then
  # Ces deux-là lisent les workflows comme du code: injection de gabarit dans un
  # `run:`, action non épinglée, jeton laissé dans le dépôt cloné. La chaîne de
  # publication détient la clé de signature, donc une faille y vaut toutes les
  # appliances qui se mettent à jour la nuit.
  if command -v actionlint >/dev/null; then
    step "actionlint" actionlint -color
  else
    printf '\nactionlint absent: brew install actionlint\n' >&2
  fi
  ZIZMOR="$PROJECT_ROOT/.venv/bin/zizmor"
  [[ -x "$ZIZMOR" ]] || ZIZMOR="$(command -v zizmor || true)"
  if [[ -n "$ZIZMOR" ]]; then
    step "zizmor" "$ZIZMOR" --config .github/zizmor.yml --persona=regular \
      --no-progress .github/workflows
  else
    printf '\nzizmor absent: .venv/bin/pip install zizmor\n' >&2
  fi
fi

printf '\n'
if (( ${#failed[@]} )); then
  printf 'Échecs: %s\n' "${failed[*]}" >&2
  exit 1
fi
printf 'Tout est vert.\n'
