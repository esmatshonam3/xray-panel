# Xray Panel - developer shortcuts
SHELL := /bin/bash
PY    ?= python

.PHONY: help venv install dev test lint fmt init-db seed run compose-up compose-down clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

venv: ## create virtualenv
	$(PY) -m venv .venv

install: ## install panel + dev dependencies
	.venv/bin/pip install -U pip
	.venv/bin/pip install -r panel/requirements.txt -r panel/requirements-dev.txt

dev: ## run panel with autoreload
	cd panel && ../.venv/bin/uvicorn app.main:app --reload --port 8000

test: ## run the test suite
	cd panel && ../.venv/bin/pytest -q

lint: ## ruff + mypy
	cd panel && ../.venv/bin/ruff check app tests && ../.venv/bin/mypy app || true

fmt: ## ruff format
	cd panel && ../.venv/bin/ruff format app tests

init-db: ## create tables
	cd panel && ../.venv/bin/python -m app.cli init-db --seed

seed: ## seed demo data (plans, node, users)
	cd panel && ../.venv/bin/python -m app.cli seed-demo

run: init-db ## init db then run
	cd panel && ../.venv/bin/uvicorn app.main:app --port 8000

compose-up: ## full local stack (panel + postgres + node agent)
	docker compose up -d --build

compose-down:
	docker compose down

clean:
	rm -rf panel/data .pytest_cache **/__pycache__
