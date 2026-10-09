"""Compare SBERT inputs on the labelled sample set: natural sentences (stopwords kept, PII masked) vs the
stopword-removed lexical text. Reports separation, ROC AUC and best-threshold F1 for both.

Usage:
    uv run python scripts/compare_sbert_input.py --resumes ../samples/calibration --labels ../samples/calibration/labels.csv \
        --jd ../samples/calibration/jd.txt [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services.resources import load_resources
from scripts.calibrate import metrics, read_labels, score_set


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--resumes", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--jd", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    base = get_settings()
    resources = load_resources(base, check_dim=False)
    labels = read_labels(args.labels)
    jd = args.jd.read_text()
    results = {}
    for mode in ("natural", "stopword_removed"):
        settings = base.model_copy(update={"sbert_input": mode})
        rows = score_set(args.resumes, labels, jd, resources, settings)
        results[mode] = {"metrics": metrics(rows), "rows": rows}

    print(f"model={base.sbert_model}  resumes={len(labels)}\n")
    print(f"{'metric':28s} {'natural':>12s} {'stopword_removed':>18s}")
    for key, label in (
        ("mean_fit", "mean score (fit)"),
        ("mean_not_fit", "mean score (not fit)"),
        ("separation", "separation (fit - not fit)"),
        ("auc", "ROC AUC"),
    ):
        print(
            f"{label:28s} {results['natural']['metrics'][key]:>12} {results['stopword_removed']['metrics'][key]:>18}"
        )
    for mode in results:
        best = results[mode]["metrics"]["best"]
        print(
            f"best threshold [{mode}]: {best['threshold']} → precision {best['precision']} recall {best['recall']} F1 {best['f1']}"
        )
    diffs = [
        abs(a["clean"] - b["clean"])
        for a, b in zip(results["natural"]["rows"], results["stopword_removed"]["rows"], strict=True)
    ]
    print(f"mean |score difference| per resume: {sum(diffs) / len(diffs):.2f} points")
    if args.json:
        args.json.write_text(json.dumps({"model": base.sbert_model, "results": results}, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
