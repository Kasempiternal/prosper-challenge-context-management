# Prosper Challenge — run everything from the repo root.
#   make setup   install the backend and Agent Studio, create backend/.env, offer to take the API keys
#   make start   run both and open the studio in the browser (runs setup first if needed)
# Without make (e.g. Windows): python scripts/studio.py setup | start

PY ?= python3
VENV_PYTHON := backend/.venv/bin/python

.PHONY: help setup install start dev run studio test clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install everything once: backend venv, frontend deps, backend/.env, keys
	$(PY) scripts/studio.py setup

install: setup ## Same as setup

start: ## Run the backend and Agent Studio together, open the browser (Ctrl+C stops both)
	$(PY) scripts/studio.py start

dev: start ## Same as start

run: ## Backend only, on :7860
	$(VENV_PYTHON) backend/bot.py

studio: ## Agent Studio only (needs the backend running)
	cd frontend && pnpm dev

test: ## Backend and frontend tests
	$(VENV_PYTHON) -m pytest backend/tests -q
	cd frontend && pnpm test

clean: ## Remove the venv, frontend deps and caches
	rm -rf backend/.venv frontend/node_modules .studio
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
