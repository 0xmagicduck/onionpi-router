# Les commandes du dépôt, en un endroit.
#
# Rien ici n'est une nouvelle façon de construire OnionPi: chaque cible appelle
# le script qui fait déjà autorité, pour que la CI et un poste de développement
# ne puissent pas diverger. `make` sans argument liste ce qui existe.

SHELL := bash
.SHELLFLAGS := -Eeuo pipefail -c
.DEFAULT_GOAL := help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff

.PHONY: help
help: ## Liste les cibles
	@printf 'OnionPi — cibles disponibles\n\n'
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'
	@printf '\nDémarrage: make setup && make demo\n'

# ------------------------------------------------------------------ mise en place

.PHONY: setup
setup: $(VENV) node_modules ## Installe les dépendances Python et npm

$(VENV): backend/requirements-dev.txt
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r backend/requirements-dev.txt
	@touch $(VENV)

node_modules: frontend/package-lock.json
	cd frontend && npm ci --no-audit --no-fund
	@touch frontend/node_modules

# ------------------------------------------------------------------ vérifications

.PHONY: check
check: ## Tout ce que la CI vérifie, dans l'ordre qui échoue le plus vite
	./scripts/check.sh

.PHONY: backend frontend shell meta workflows
backend: ## ruff + pytest
	./scripts/check.sh backend

frontend: ## tsc + vite build
	./scripts/check.sh frontend

shell: ## shellcheck + tests de paquetage
	./scripts/check.sh shell

meta: ## Versions, secrets, budget, types API, YAML
	./scripts/check.sh meta

workflows: ## actionlint + zizmor sur .github/workflows
	./scripts/check.sh workflows

.PHONY: fix
fix: ## Corrige ce que ruff sait corriger seul
	$(RUFF) check backend --fix

.PHONY: test
test: ## pytest seul, avec les arguments de ARGS
	cd backend && ../$(PYTHON) -m pytest $(ARGS)

# ------------------------------------------------------------------ exécution locale

# Le mode démonstration remplace Tor, nftables et systemd par des réponses
# plausibles: c'est ainsi que tournent les tests et que l'interface se
# développe sans Raspberry Pi.
.PHONY: demo
demo: $(VENV) ## Crée l'administrateur de démonstration et lance l'API sur 8080
	@test -n "$$ONIONPI_SESSION_SECRET" || { \
		printf 'Exportez d’abord un secret de session:\n'; \
		printf '  export ONIONPI_SESSION_SECRET="$$(openssl rand -hex 32)"\n'; \
		exit 1; \
	}
	cd backend && ONIONPI_DEMO_MODE=1 printf '%s\n' 'mot-de-passe-de-demo-solide' \
		| ONIONPI_DEMO_MODE=1 ../$(PYTHON) -m onionpi.cli create-admin --password-stdin
	cd backend && ONIONPI_DEMO_MODE=1 ../$(VENV)/bin/uvicorn onionpi.main:app \
		--host 127.0.0.1 --port 8080 --reload

.PHONY: ui
ui: node_modules ## Sert l'interface sur 5173 et relaie /api vers 8080
	cd frontend && npm run dev

# ------------------------------------------------------------------ publication

.PHONY: version
version: ## Aligne VERSION, package.json et __init__.py (make version V=0.5.0)
	@test -n "$(V)" || { printf 'Usage: make version V=X.Y.Z\n' >&2; exit 2; }
	./scripts/set-version.sh $(V)

.PHONY: api-types
api-types: $(VENV) ## Régénère docs/openapi-v1.json et les types du frontend
	$(PYTHON) scripts/generate-api-types.py

.PHONY: clean
clean: ## Supprime les artefacts de construction, pas les dépendances
	rm -rf frontend/dist dist backend/.pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
