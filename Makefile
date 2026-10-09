# Thin aliases — every command is also listed in CLAUDE.md / README.md.
.PHONY: help up down db api web install test test-fast lint fmt migrate fixtures contract calibrate compare samples

BACKEND = cd backend &&

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

up: ## build + start db, api and web (http://localhost:3000)
	docker compose up --build

down: ## stop the stack
	docker compose down

db: ## start only Postgres + pgvector (for native dev / tests)
	docker compose up -d db

install: ## install backend (uv) and frontend (npm) dependencies
	$(BACKEND) uv sync
	cd frontend && npm install

migrate: ## apply database migrations
	$(BACKEND) uv run alembic upgrade head

api: ## run the API natively with auto-reload (uses MPS on Apple Silicon)
	$(BACKEND) uv run uvicorn app.main:app --reload --port 8000

web: ## run the frontend dev server
	cd frontend && npm run dev

test: ## full backend test suite (needs `make db`)
	$(BACKEND) uv run pytest

test-fast: ## tests that need neither Postgres nor the NLP models
	$(BACKEND) uv run pytest -m "not db and not slow"

lint: ## ruff lint + format check, TypeScript check
	$(BACKEND) uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run typecheck

fmt: ## auto-format backend code
	$(BACKEND) uv run ruff check --fix . && uv run ruff format .

fixtures: ## generate the test-fixture resumes + sample JD into samples/fixtures
	$(BACKEND) uv run python scripts/generate_fixtures.py --out ../samples/fixtures

samples: ## generate the labelled calibration set into samples/calibration
	$(BACKEND) uv run python scripts/generate_fixtures.py --calibration --out ../samples/calibration

contract: ## export contracts/openapi.json and regenerate the typed frontend client
	$(BACKEND) uv run python scripts/export_openapi.py
	cd frontend && npm run gen:api

calibrate: ## precision/recall per threshold on the labelled calibration set
	$(BACKEND) uv run python scripts/calibrate.py --resumes ../samples/calibration --labels ../samples/calibration/labels.csv --jd ../samples/calibration/jd.txt

compare: ## SBERT input comparison: natural vs stopword_removed
	$(BACKEND) uv run python scripts/compare_sbert_input.py --resumes ../samples/calibration --labels ../samples/calibration/labels.csv --jd ../samples/calibration/jd.txt
