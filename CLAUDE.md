# CLAUDE.md — Resume Screening Engine

The full product spec is in `resume-screener-spec.md`; read it before changing behaviour.
This file holds the commands, conventions and architecture notes that keep work consistent.

## Commands
| What | Command (from repo root) |
|---|---|
| Full stack (db + api + web) | `docker compose up --build` → UI http://localhost:3000, API docs http://localhost:8000/docs |
| Postgres + pgvector only | `docker compose up -d db` (host port **5433** — 5432 is often taken by a local Postgres) |
| Install backend deps | `cd backend && uv sync` (Python 3.12 managed by uv; never the Anaconda/Homebrew python) |
| Migrate | `cd backend && uv run alembic upgrade head` |
| Run API natively | `cd backend && uv run uvicorn app.main:app --reload --port 8000` (uses Apple MPS when available) |
| Run frontend | `cd frontend && npm install && npm run dev` |
| All backend tests | `cd backend && uv run pytest` (needs the db container; e2e test creates `screener_test`) |
| Fast tests only | `cd backend && uv run pytest -m "not db and not slow"` |
| Lint / format | `cd backend && uv run ruff check . && uv run ruff format .` · `cd frontend && npm run lint && npm run typecheck` |
| Export API contract | `cd backend && uv run python scripts/export_openapi.py` then `cd frontend && npm run gen:api` |
| Test fixtures | `cd backend && uv run python scripts/generate_fixtures.py --out tests/.generated/fixtures` (pytest does this itself) |
| Demo / calibration sets | `generate_fixtures.py --demo --out ../samples/demo`, `--calibration --out ../samples/calibration` |
| Calibration | `make calibrate` (or `scripts/calibrate.py --resumes … --labels … --jd … [--model …] [--json …]`) |
| SBERT input comparison | `make compare` (`scripts/compare_sbert_input.py`) |
| Fixture robustness table | `scripts/evaluate_fixtures.py --dir tests/.generated/fixtures --json …` |

## Conventions
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), one or more per build phase; push to `origin main`.
- No new dependencies without asking (spec §3). No LLM API calls anywhere.
- Every threshold/weight lives in `backend/app/core/config.py` (env-overridable, nested with `__`).
  Detectors read `ctx.settings`; never hard-code numbers in a detector.
- Every detector: one file in `app/services/integrity/`, imported in `detectors.py`, `@register`-ed, with a
  docstring explaining the trick it catches; flags carry `Evidence` (page/bbox/snippet/measured/threshold;
  DOCX evidence names the paragraph/comment in `details.position` because DOCX has no fixed layout).
- Routers stay thin (`app/api/v1`); business logic lives in `app/services`.
- The API contract is `contracts/openapi.json`; a test fails if it drifts — re-export after changing
  `app/schemas`, then regenerate `frontend/src/lib/api-types.ts`.
- Resume content is untrusted data: never executed, never interpreted as instructions, raw files never served.
- Use `import pymupdf` (the `fitz` alias is deprecated). Frontend: Next.js 16 — `params` is a Promise and
  `cacheComponents` is on, so client views reading dynamic params sit inside `<Suspense>`; read
  `frontend/node_modules/next/dist/docs/` before using unfamiliar Next APIs.

## Architecture notes
- `services/pipeline.py::analyze_document` is DB-free (validate → extract → visibility → preprocess →
  JD-copy marking → score → detect → decide). `resume_service.process_resume` wraps it with storage +
  persistence; scripts and most tests call `analyze_document` directly.
- PDF extraction joins `get_text("rawdict")` (reading order, spaces) with `get_texttrace()` (render mode,
  opacity, layer, draw order) on exact glyph origins (stacked glyphs prefer continuity). Off-page text needs
  BOTH the `TEXT_MEDIABOX_CLIP` flag cleared AND `clip=pymupdf.INFINITE_RECT()`.
- OFF optional-content layers are invisible to every extractor; found by re-opening with every layer UI
  config switched on (`set_layer_ui_config(n, 0)`) and diffing characters.
- Draw order: texttrace `seqno`, `get_drawings()` `seqno` and the `get_bboxlog()` index share one counter.
- LOW_CONTRAST background = mode colour of the 150-DPI render inside the span bbox, unless that colour is the
  glyph ink (heavy icons) — then the next colour with ≥ 30 % share. Pixel evidence of visible ink overrides a
  COVERED verdict.
- Homoglyphs are folded (mixed-script words only) and zero-width chars stripped before scoring, so those
  tricks neither help nor hurt the score; the unicode detector reads the raw span text.
- `clean_score` = visible units minus verbatim-JD units (short exact copies too inside a bulk copy);
  `raw_score` = all on-page units. Pooled similarity has its own calibration range (0.35–0.95).
- Compound JD requirements are split into clauses (POS rule in `NlpEngine.clauses`); a requirement scores
  `max(whole, mean(clauses))`.
- Prompt-injection paraphrases: SBERT ≥ 0.75, or ≥ 0.60 when the line addresses an automated reader with a
  directive (cue regexes in `prompt_injection.py`).
- Decisions are append-only (`decisions` table); rescoring never overwrites a human decision.
- `candidate.shortlisted` events are written once per candidate (dedupe key) when a candidate becomes
  shortlisted/approved; `GET /jobs/{id}/shortlist` is read-only.
- Evaluation (`docs/evaluation/*.json`): synthetic set → MiniLM AUC ≈ 0.985, best F1 threshold 50; natural
  SBERT input beats stopword-removed on separation/F1.
