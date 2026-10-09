# Resume Screening Engine — Build Spec for Claude Code

> **How to use:** Put this file in your repo root, open Claude Code there and say:
> *"Read resume-screener-spec.md completely, reply with your plan, then build it phase by phase."*
> (Or paste the whole file into Claude Code.)

---

## 0. How you (Claude Code) should work
1. Read the entire spec first. Reply with a 5-line summary of your understanding, the folder tree, and open questions. **Wait for my go-ahead before writing code.**
2. Build in the phases in §11. After each phase: run the tests, fix failures, give me a short summary, and commit (conventional commits).
3. Create and maintain `CLAUDE.md` (run / test / migrate commands, conventions, architecture notes).
4. Don't add dependencies beyond §3 without asking.
5. If the repo already contains an earlier LLM-based screening implementation, replace it with this pipeline.
6. Dev machines: MacBook Air (Apple Silicon) and Windows with WSL2 + Docker Desktop — everything must run on both (multi-arch images, no x86-only dependencies).

## 1. What we're building
The resume-screening module of a recruitment agent — the first use case of a larger "business automation OS" (final-year B.E. project). A recruiter creates a job (JD) and uploads many resumes (PDF/DOCX). The system:
1. extracts text **with styling and position** (font size, color, opacity, page, bounding box),
2. separates **visible** text from **hidden / unreadable** text,
3. scores each resume against the JD with **SBERT embeddings + cosine similarity**, on top of an NLP preprocessing pipeline with **stopword removal + lemmatization**,
4. runs **integrity checks** that flag resume gaming — hidden text, tiny fonts, JD copy-paste, keyword stuffing, prompt injection and more,
5. returns an **explainable report** and a decision, with human override.

**Frontend: deliberately basic and minimal. Backend: production quality — clean architecture, configurable, well tested.**

## 2. Hard rules
- **No LLM API calls** (OpenAI, Grok, Anthropic, Gemini, …) anywhere in scoring or detection. All NLP runs locally.
- **The official score uses visible text only.** Hidden text and verbatim JD-copied lines never count.
- **Gaming-resistant scoring:** skill matching is set-based (repeating a keyword adds nothing), requirement coverage uses max similarity, identical units are deduplicated before pooling.
- **Every flag carries evidence** (page, bbox, snippet, measured value, threshold). No unexplained flags.
- **Flags never auto-reject.** Suspicious resumes go to human review; a human makes the final call.
- **All thresholds and weights live in config** (overridable via `.env`) — none hard-coded in detectors.
- **Resume content is untrusted input** — never executed, never treated as instructions, raw files never served to the browser.
- Never use name, gender, age, photo, address or marital status in scoring.

## 3. Tech stack
- **Backend:** Python 3.11+ · FastAPI · Pydantic v2 + pydantic-settings · SQLAlchemy 2.x + Alembic · PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`) · sentence-transformers (default `all-MiniLM-L6-v2`, 384-dim; configurable to `all-mpnet-base-v2`) · spaCy `en_core_web_sm` · PyMuPDF · python-docx + lxml · rapidfuzz · numpy · Pillow · ReportLab (test fixtures) · pytest · ruff
- **Optional, feature-flagged, off by default:** pytesseract (OCR), LibreOffice headless (DOCX → PDF so DOCX also gets the visual checks)
- **Frontend:** Next.js (App Router) · TypeScript · Tailwind CSS · `openapi-typescript` for a typed API client. No UI component library.
- **Infra:** `docker-compose.yml` (db, api, web) · `.env.example` · CPU-only torch in the API image · SBERT model + spaCy pipeline downloaded at image build · device auto-select: CUDA → Apple MPS (when running natively) → CPU.

## 4. Architecture
```
backend/app/
  main.py               # app factory; lifespan loads SBERT + spaCy once
  core/config.py        # every threshold and weight (pydantic-settings)
  core/logging.py       # structured JSON logs with request_id / resume_id
  api/v1/               # jobs.py, resumes.py — thin routers, no business logic
  schemas/              # Pydantic models = the API contract
  db/                   # models.py, session.py, migrations/
  services/
    validation.py       # file type, size, security checks
    extraction/         # pdf.py, docx.py -> ParsedDocument
    visibility/         # rules.py, visual_check.py -> visible / hidden per span
    preprocessing/      # normalize.py, sections.py, nlp.py, jd_parser.py
    scoring/            # embedder.py, requirements.py, skills.py, scorer.py
    integrity/          # base.py (Detector protocol + registry) + one file per detector
    decision.py
    pipeline.py         # validate -> extract -> visibility -> preprocess -> score -> detect -> decide -> persist
    previews.py         # page renders with flagged regions highlighted
  data/skills.json      # canonical skills + aliases
backend/tests/          # unit tests per detector, scoring tests, end-to-end test
backend/scripts/        # generate_fixtures.py, calibrate.py, compare_sbert_input.py
frontend/
contracts/openapi.json  # exported API contract
```
Every detector implements one interface — `detect(doc: ParsedDocument, ctx: JobContext) -> list[Flag]` — and is registered in one place, so adding a new check = adding one file.

**Internal models**
- `TextSpan`: text, page, bbox, font, declared_size, effective_size, color_rgb, opacity, render_mode, background_rgb, source (`body | header | footer | textbox | annotation | comment | alt_text | metadata | hidden_layer`), visible, hidden_reasons[]
- `ParsedDocument`: file info, page sizes, spans, image bboxes, draw-order log, metadata, raw_text, visible_text, hidden_text

## 5. Pipeline

### 5.1 Validation & security
- PDF and DOCX only — verify magic bytes, not the extension. Max 5 MB, max 10 pages.
- Store uploads outside any served folder with UUID names; compute SHA-256.
- Flag / quarantine: password-protected PDFs (`ENCRYPTED_FILE`); PDFs with JavaScript, `/Launch` or `/OpenAction` actions, or embedded files (`MALICIOUS_PDF_FEATURES`, critical); DOCX with macros (`MACRO_DOCUMENT`); zip bombs (check total uncompressed size).
- Image-only / scanned PDF (almost no extractable text) → status `UNPARSEABLE` → needs review (OCR only if enabled). Never auto-reject.
- One bad file must never break a batch — catch errors per resume, store `FAILED` + reason.

### 5.2 Extraction (with styling)
- **PDF (PyMuPDF):** per-span text, size, color, opacity, render mode, bbox and draw order (e.g. `page.get_text("rawdict")`, `page.get_texttrace()`, `page.get_bboxlog()`); filled shapes (`page.get_drawings()`); image bboxes; metadata; annotations; optional-content layers that are OFF.
  **Gotcha:** PyMuPDF drops text outside the page box by default (`TEXT_MEDIABOX_CLIP`). Disable that flag during extraction, or off-page text silently disappears and can't be flagged.
- **DOCX:** walk the body XML in order — paragraphs, tables, text boxes, headers/footers, comments, image alt text. Resolve each run's **effective** formatting (run → paragraph style → document defaults): hidden/vanish, color, size (half-points), highlight/shading.
- **Contact info:** name (largest-font line near the top of page 1), email, phone (incl. +91 formats) — the scheduling agent needs these later.

### 5.3 Visibility engine
Mark every span visible or hidden and record all reasons. Render each PDF page once (~150 DPI) and reuse the image.

| Reason | Rule (thresholds in config) |
|---|---|
| `LOW_CONTRAST` | WCAG contrast ratio between the text color and the **actual background under the span** < 1.5. Background = most frequent color of the rendered pixels inside the span bbox (works for vector shapes *and* image backgrounds like Canva templates); fallback: topmost filled shape under the span, else white. **Never just check "is the text white"** — white text on a dark sidebar is a legit design. |
| `TINY_FONT` | Effective size < 4 pt → hidden. 4–6 pt with ≥ 20 characters → suspicious. Effective size comes from the rendered bbox height / character width, not only the declared size (catches scaled or squeezed text). |
| `LOW_OPACITY` | Opacity < 0.1 |
| `INVISIBLE_RENDER_MODE` | Invisible text render mode — except an OCR text layer on a scanned page, which is normal |
| `OFF_PAGE` | Bbox mostly outside the page box |
| `COVERED` | An opaque image or shape drawn **after** the text covers ≥ 90% of its bbox |
| `HIDDEN_LAYER` | Text inside an optional-content layer that is OFF |
| `STACKED_TEXT` | Spans drawn on top of each other at the same position |
| `DOCX_HIDDEN` | Vanish/hidden run, color same as page or shading, size < 4 pt, or text only in comments / alt text |
| `NOT_RENDERED_VISIBLY` | **Independent pixel check:** crop each non-whitespace span's bbox from the render; (near-)uniform pixels = the text isn't actually visible. Catches tricks the rules miss (clipping, blend modes, …). Rule + pixel agreement = high confidence. |

Optional (OCR enabled): OCR the render; extracted words with no OCR match near their bbox → hidden.

### 5.4 Preprocessing (NLP)
1. Unicode NFKC; count, then strip zero-width / invisible characters (U+200B–U+200D, U+2060, U+FEFF, soft hyphen).
2. Section detection (Summary, Skills, Experience, Projects, Education, Certifications) via heading heuristics (larger/bold font, common heading words).
3. Sentence / bullet segmentation (spaCy; each bullet line is a unit).
4. Lowercase → **stopword removal** (spaCy list + small custom list) → **lemmatization** = the *lexical text*, used for skill matching, keyword density and stuffing checks.
5. Mask PII (emails, phones, URLs, person names via NER) before scoring.
6. JD parsing: split into requirement units; weight "Requirements / Must have / Responsibilities" = 1.0 and "Nice to have / Preferred" = 0.5; drop boilerplate (about us, benefits, equal-opportunity text).

**SBERT input:** SBERT is trained on natural sentences, so feed it normalized, PII-masked **natural** sentences (stopwords kept). Add config flag `SBERT_INPUT=natural|stopword_removed` and `scripts/compare_sbert_input.py`, which compares both on the sample set so the effect can be reported.

### 5.5 Scoring (SBERT + cosine similarity)
- Load the model once; batch-encode with `normalize_embeddings=True` (cosine = dot product).
- **Never embed the whole resume as one string** — MiniLM truncates input at ~256 tokens. Embed units, then aggregate.
- Components (weights in config):
  1. **Requirement coverage — 0.5:** for each JD requirement, max cosine over all resume units; keep the best-matching resume line as evidence; "met" if raw cosine ≥ 0.55. Score = weighted mean of calibrated best similarities.
  2. **Skill match — 0.3:** skills from `skills.json` (aliases: js → javascript, postgres → postgresql, ml → machine learning, …) matched on the lexical text with spaCy PhraseMatcher; SBERT fallback (≥ 0.80) for multi-word skills. Score = weighted matched / weighted required. Return matched + missing lists.
  3. **Overall similarity — 0.2:** cosine between mean-pooled JD and resume embeddings.
- Calibrate raw cosine to 0–100 with a configurable floor/ceiling (default 0.25 → 0, 0.75 → 100); keep raw values in the breakdown.
- Two scores:
  - `clean_score` — visible text minus verbatim JD-copied lines (**the official score**)
  - `raw_score` — everything extracted (what a naive ATS would compute)
  - `gaming_delta = raw_score − clean_score`; ≥ 10 points → `SCORE_INFLATION_ATTEMPT` (high)
- Store unit embeddings in pgvector (dimension = model dimension) with an HNSW cosine index.

### 5.6 Integrity detectors
Flag shape: `{code, severity: low|medium|high|critical, detector, message, evidence: {page, bbox, snippet, measured_value, threshold}}`

1. **Hidden text** — one flag per reason group from §5.3; severity high (suspicious-but-visible tiny font = medium). Escalate to **critical** when the hidden text contains JD sentences or JD keywords (classic "white-fonting").
2. **JD copy-paste** — runs on all text (evidence says whether the copy was visible or hidden); compare normalized text (lowercase, punctuation stripped, stopwords **kept**):
   - `VERBATIM_JD_LINE` (high): resume line ≥ 8 words with rapidfuzz ratio ≥ 90 against any JD sentence.
   - `JD_BULK_COPY` (high; critical ≥ 40%): ≥ 15% of the JD's 8-word shingles appear in the resume.
   - `NEAR_VERBATIM_JD` (medium): SBERT cosine ≥ 0.92 **and** token overlap ≥ 0.7 with a JD sentence (lightly reworded copy).
   - `JD_VOICE_PHRASES` (medium): text that only makes sense in a JD — "the ideal candidate", "you will", "we are looking for", "responsibilities include", "join our team".
   - Never flag skill lists, headings, degree names or lines < 8 words. Normal tailoring (similar wording) must not be flagged as verbatim.
3. **Keyword stuffing** (lexical text):
   - `KEYWORD_STUFFING`: any JD keyword repeated > max(8, 3% of tokens).
   - `KEYWORD_DUMP`: outside the Skills section, a block ≥ 15 tokens where ≥ 70% of content words are JD keywords with no verbs; or a skills list > 50 items.
   - `REPEATED_CONTENT`: the same line ≥ 3 times.
4. **Prompt injection** — scan visible + hidden + metadata text. Regex hit → **critical**: "ignore (all|previous|prior) instructions", "you are an AI / assistant / language model", "system prompt", "rate / rank / score this candidate", "shortlist / select / hire this candidate", "this candidate is the perfect / ideal fit", "note to AI / screener", chat-template tokens like `[INST]`, `<|im_start|>`. Semantic-only hit (SBERT ≥ 0.75 against ~20 canonical injection phrases, catches paraphrases) → high.
5. **Unicode tricks:** `INVISIBLE_UNICODE` (zero-width count above threshold), `HOMOGLYPHS` (words mixing Latin with Cyrillic/Greek letters), `GARBLED_TEXT` (mostly non-words from broken font encoding → low, review).
6. **Metadata stuffing:** title / subject / keywords / XMP holding long keyword lists or JD text → `METADATA_STUFFING` (low/medium). Metadata never feeds scoring.
7. **Duplicates (pgvector):** `DUPLICATE_FILE` (same SHA-256 in the job), `NEAR_DUPLICATE_RESUME` (resume embedding cosine ≥ 0.97 with another candidate), `IDENTITY_CONFLICT` (same email/phone, different names).
8. **Timeline sanity** (nice-to-have, low): end date before start date; future dates not marked "expected" / "present".
9. **File security** flags from §5.1.

**Integrity risk score** (0–100) = sum over flags (critical 40, high 25, medium 10, low 3), capped at 100. Risk level = highest severity present.

### 5.7 Decision
- Any critical flag → `FLAGGED` (never auto-shortlisted; a human must review)
- `UNPARSEABLE` → `NEEDS_REVIEW`
- `clean_score` ≥ 70 and no high flags → `SHORTLISTED`
- `clean_score` ≥ 60 (borderline, or ≥ 70 with a high flag) → `NEEDS_REVIEW`
- Otherwise → `NOT_SHORTLISTED` (flags still shown in the report)
- Human override (approve for interview / reject / keep in review + note) is stored with a timestamp (audit trail).

## 6. Database (PostgreSQL + pgvector, Alembic migrations)
- `jobs` — id (uuid), title, jd_text, requirements jsonb, required_skills jsonb, jd_embedding vector, created_at
- `resumes` — id, job_id, file_name, sha256, mime, storage_path, status (`PENDING | PROCESSING | DONE | FAILED | UNPARSEABLE`), candidate_name, email, phone, page_count, visible_text, hidden_text, created_at, processed_at
- `resume_units` — id, resume_id, section, text, is_visible, is_jd_copy, embedding vector
- `scores` — resume_id, clean_score, raw_score, gaming_delta, breakdown jsonb (incl. per-stage timings)
- `integrity_flags` — id, resume_id, code, severity, detector, message, evidence jsonb
- `decisions` — resume_id, system_decision, human_decision, reviewer_note, decided_at
- `events` — id, type, payload jsonb, created_at (outbox for the future agents)

## 7. API contract (v1) — schemas first, export `contracts/openapi.json`
| Endpoint | Purpose |
|---|---|
| `POST /api/v1/jobs` | Create job (title, jd_text) → parsed requirements + skills |
| `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}` | List / detail |
| `POST /api/v1/jobs/{id}/resumes` | Multipart, many files → `202` + resume ids; processed in background |
| `GET /api/v1/jobs/{id}/candidates?status=&sort=score\|risk` | Candidate table rows |
| `GET /api/v1/resumes/{id}` | Full report (example below) |
| `GET /api/v1/resumes/{id}/pages/{n}/preview.png` | Page render — hidden text boxed red, JD-copied lines boxed orange |
| `POST /api/v1/resumes/{id}/rescore` | Recompute with current config, no re-upload |
| `PATCH /api/v1/resumes/{id}/decision` | Human override |
| `GET /api/v1/jobs/{id}/shortlist` | Approved/shortlisted candidates with email, for the future interview-scheduling agent; also writes a `candidate.shortlisted` event |
| `GET /health` | Health check |

Background work: FastAPI BackgroundTasks behind a small `TaskQueue` interface (swappable for Redis/RQ later). Error format: `{"error": {"code": "...", "message": "...", "details": {}}}`.

Report example:
```json
{
  "resume_id": "b1c2…",
  "candidate": { "name": "…", "email": "…" },
  "status": "DONE",
  "decision": { "system": "FLAGGED", "human": null },
  "scores": {
    "clean_score": 54.2,
    "raw_score": 88.9,
    "gaming_delta": 34.7,
    "breakdown": {
      "requirement_coverage": 58.0,
      "skill_match": 50.0,
      "overall_similarity": 52.1,
      "requirements": [
        {
          "jd_requirement": "2+ years building REST APIs with FastAPI",
          "best_resume_line": "Built REST APIs in FastAPI for a payments dashboard",
          "similarity": 0.71, "met": true, "weight": 1.0
        }
      ],
      "skills": { "matched": ["python", "fastapi"], "missing": ["docker", "aws"] }
    }
  },
  "integrity": {
    "risk_score": 65,
    "risk_level": "critical",
    "flags": [
      {
        "code": "LOW_CONTRAST", "severity": "critical", "detector": "hidden_text",
        "message": "412 characters of white-on-white text containing JD sentences",
        "evidence": { "page": 1, "bbox": [36, 700, 560, 790], "snippet": "Python Django AWS Kubernetes…", "measured_value": 1.0, "threshold": 1.5 }
      },
      {
        "code": "SCORE_INFLATION_ATTEMPT", "severity": "high", "detector": "scoring",
        "message": "Hidden/copied content raises the score by 34.7 points",
        "evidence": { "measured_value": 34.7, "threshold": 10 }
      }
    ]
  }
}
```

## 8. Frontend (basic & minimal)
Next.js + Tailwind; grayscale + one accent color; system font; no animations, no chart library, no auth. Proper loading / empty / error states. Typed client generated from `contracts/openapi.json`. Three screens only:
1. **`/`** — job list + "New job" form (title, JD textarea).
2. **`/jobs/[id]`** — extracted requirements and skills (chips); drag-and-drop multi-upload (PDF/DOCX); candidate table: Name · Clean score · Risk badge · Status · # Flags; sort and filter; polls every 2 s while anything is processing.
3. **`/candidates/[id]`** — big clean score (plus raw score and gaming delta when > 0); requirement table (JD requirement → best resume line → similarity → met); matched / missing skills; flag list with evidence; page previews with highlighted regions; decision buttons + note.

## 9. Testing & evaluation (must pass before moving on)
`scripts/generate_fixtures.py` builds test resumes programmatically plus a sample JD — use **ReportLab** for PDFs (independent from the PyMuPDF parser), PyMuPDF only where ReportLab can't (e.g. OFF layers, JavaScript), python-docx for DOCX:
- **Must produce zero integrity flags:** `clean.pdf`, `clean.docx`, `dark_sidebar_legit.pdf` (white text on a dark sidebar), `tailored_legit.pdf` (wording similar to the JD, nothing verbatim)
- **Hidden text:** `white_text_jd.pdf` (JD pasted in white), `same_color_box.pdf`, `tiny_font.pdf` (1–2 pt), `low_opacity.pdf`, `render_mode_invisible.pdf`, `off_page.pdf`, `behind_image.pdf`, `hidden_layer.pdf`, `hidden_vanish.docx`, `white_text.docx`, `tiny_font.docx`
- **Content tricks:** `verbatim_jd_lines.pdf`, `jd_bulk_copy.pdf`, `keyword_stuffing.pdf`, `prompt_injection_hidden.pdf`, `zero_width.pdf`, `homoglyph.pdf`, `metadata_stuffing.pdf`
- **Files:** `encrypted.pdf`, `javascript.pdf`, `scanned_image_only.pdf`

Tests:
- each fixture raises its expected flag codes; the legit fixtures raise none (false-positive guard);
- `clean_score` of `white_text_jd.pdf` ≈ the same resume without the hidden text;
- a matching resume scores clearly above an unrelated one for the same JD;
- API end-to-end: create job → upload → poll → report.

`scripts/calibrate.py`: given a resume folder + labels CSV (fit / not fit), print the score distribution, precision/recall for thresholds 50–85 and the best threshold — for the project report's evaluation section.

## 10. Non-functional
- SBERT + spaCy loaded once at startup; JD embeddings cached; batch encoding; CPU-heavy work in a thread pool, never on the event loop. Target < 3 s per 2-page resume on CPU.
- Structured logs with request_id / resume_id; per-stage timings stored in the breakdown.
- Type hints everywhere, ruff, a docstring on every detector explaining the trick it catches.
- Raw uploads are never served to the browser — only rendered PNG previews.
- README: one-command setup, Mermaid architecture diagram, pipeline walkthrough, detector table (what each catches + thresholds), how to run tests and calibration.

## 11. Build phases (stop and report after each)
0. **Scaffold & contract:** folders, docker-compose, config, DB models + migration, Pydantic schemas, stub routes, export `contracts/openapi.json`, health check. *(Contract first — the frontend can be built in parallel from here.)*
1. Validation + extraction (PDF / DOCX → `ParsedDocument`) + fixture generator.
2. Visibility engine (rules + pixel check) + hidden-text tests, including the dark-sidebar false-positive test.
3. Preprocessing + SBERT scoring (clean vs raw) + scoring tests.
4. Remaining detectors (JD copy, stuffing, injection, unicode, metadata, duplicates, timeline) + tests.
5. Pipeline, background processing, decision logic, all endpoints, page previews, end-to-end test.
6. Minimal frontend.
7. README, sample data, calibration + SBERT-input comparison scripts, full test run.

**Start now with §0 step 1: send me your plan.**
