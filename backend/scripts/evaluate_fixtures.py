"""Run every test fixture (and the demo set) through the pipeline and tabulate scores, flags, decisions and
per-stage timings — the data behind the robustness section of the project report.

Usage:  uv run python scripts/evaluate_fixtures.py --dir tests/.generated/fixtures [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services.pipeline import analyze_document, prepare_job
from app.services.resources import load_resources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    settings = get_settings()
    resources = load_resources(settings)
    job = prepare_job("Backend Engineer (Python)", (args.dir / "jd.txt").read_text(), resources)
    rows = []
    for path in sorted(p for p in args.dir.iterdir() if p.suffix in (".pdf", ".docx")):
        res = analyze_document(path.read_bytes(), path.name, job, resources)
        rows.append(
            {
                "file": path.name,
                "status": res.status.value,
                "clean": res.scores.clean.score if res.scores else None,
                "raw": res.scores.raw.score if res.scores else None,
                "delta": res.scores.gaming_delta if res.scores else None,
                "risk": res.risk_score,
                "risk_level": res.risk_level.value if res.risk_level else None,
                "decision": res.decision.system.value,
                "flags": [{"code": f.code, "severity": str(f.severity)} for f in res.flags],
                "timings_ms": res.timings_ms,
                "pages": res.page_count,
            }
        )
        print(
            f"{path.name:40s} {res.status.value:11s} clean={rows[-1]['clean']!s:6s} raw={rows[-1]['raw']!s:6s} {res.decision.system.value:15s} {', '.join(f.code for f in res.flags)}"
        )
    totals = [r["timings_ms"].get("total", 0) for r in rows if r["status"] == "DONE"]
    stages = {
        k: round(statistics.mean(r["timings_ms"].get(k, 0) for r in rows if r["status"] == "DONE"), 1)
        for k in ("validate", "extract", "visibility", "preprocess", "score", "detect", "decide")
    }
    print(
        f"\nmean total {statistics.mean(totals):.0f} ms, max {max(totals):.0f} ms on {resources.device}; per stage: {stages}"
    )
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "device": resources.device,
                    "model": settings.sbert_model,
                    "stages_ms": stages,
                    "rows": rows,
                },
                indent=2,
            )
        )
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
