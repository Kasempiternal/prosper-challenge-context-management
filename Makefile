# Prosper Challenge — run everything from the repo root (macOS / Linux; on Windows follow the README).

VENV := backend/.venv
PYTHON := $(VENV)/bin/python

.PHONY: help install run studio test clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv, install backend and frontend dependencies
	python3.11 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
	cd frontend && pnpm install

run: ## Run the voice agent backend on :7860
	$(PYTHON) backend/bot.py

studio: ## Run Agent Studio (then open http://localhost:5173)
	cd frontend && pnpm dev

test: ## Backend and frontend tests
	$(PYTHON) -m pytest backend/tests -q
	cd frontend && pnpm test

clean: ## Remove the venv and Python caches
	rm -rf $(VENV)
	find backend -type d -name __pycache__ -prune -exec rm -rf {} +
