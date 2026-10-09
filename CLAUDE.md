# CLAUDE.md — Resume Screening Engine

The full product spec is in `resume-screener-spec.md`; read it before changing behaviour.
This file holds the commands, conventions and architecture notes that keep work consistent.

## Commands
| What | Command (from repo root) |
|---|---|
| Start Postgres + pgvector only | `docker compose up -d db` (or `make db`) |
| Full stack (db + api + web) | `docker compose up --build` → UI http://localhost:3000, API docs http://localhost:8000/docs |
| Install backend deps | `cd backend && uv sync` (Python 3.12 is managed by uv; never use the Anaconda/Homebrew python) |
| Migrate | `cd backend && uv run alembic upgrade head` |
| Run API natively | `cd backend && uv run uvicorn app.main:app --reload --port 8000` |
| Run frontend | `cd frontend && npm run dev` |
| All backend tests | `cd backend && uv run pytest` (needs the db container; the e2e test creates `screener_test`) |
| Fast tests only | `cd backend && uv run pytest -m "not db and not slow"` |
| Lint / format | `cd backend && uv run ruff check . && uv run ruff format .` |
| Export API contract | `cd backend && uv run python scripts/export_openapi.py` then `cd frontend && npm run gen:api` |
| Fixtures | `cd backend && uv run python scripts/generate_fixtures.py --out ../samples/fixtures` |
| Calibration / SBERT input comparison | `make calibrate`, `make compare` |

## Conventions
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), one or more per build phase.
- No new dependencies without asking (see spec §3). No LLM API calls anywhere.
- Every threshold/weight lives in `backend/app/core/config.py` (env-overridable, nested with `__`).
  Detectors read `ctx.settings`; never hard-code numbers in a detector.
- Every detector: one file in `app/services/integrity/`, a docstring explaining the trick it catches,
  registered with `@register`, returns `Flag`s whose `Evidence` has page/bbox/snippet/measured/threshold.
- Routers stay thin (`app/api/v1`); business logic lives in `app/services`.
- The API contract is `contracts/openapi.json`; a test fails if it drifts from the code — re-export after
  changing any schema in `app/schemas`, then regenerate `frontend/src/lib/api-types.ts`.
- Resume content is untrusted data: never executed, never interpreted as instructions, raw files never served.
- Use `import pymupdf` (the `fitz` alias is deprecated).

## Architecture notes
- `services/pipeline.py::analyze_document` is DB-free (validate → extract → visibility → preprocess →
  score → detect → decide). `process_resume` wraps it with storage + persistence. Scripts/tests call
  `analyze_document` directly.
- PDF extraction joins `get_text("rawdict")` (reading order, spaces) with `get_texttrace()` (render mode,
  opacity, layer, draw order) on exact character origins. Off-page text needs BOTH the
  `TEXT_MEDIABOX_CLIP` flag cleared AND `clip=pymupdf.INFINITE_RECT()`.
- Text in OFF optional-content layers is invisible to every extractor; it is found by re-opening the
  document with all layer UI configs switched ON (`set_layer_ui_config(n, 0)`) and diffing.
- Draw order: texttrace `seqno`, `get_drawings()` `seqno` and the `get_bboxlog()` index share one counter.
- `clean_score` uses visible units minus verbatim-JD units; `raw_score` uses everything on the page.
- Decisions are append-only (`decisions` table); rescoring never overwrites a human decision.
- `candidate.shortlisted` events are written once per candidate (dedupe key) when a candidate becomes
  shortlisted/approved; `GET /jobs/{id}/shortlist` is read-only.
