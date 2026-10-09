"""Calibration on a labelled resume set (fit / not_fit): score distribution, precision / recall / F1 for
shortlist thresholds 50–85, the best threshold and ROC AUC. Runs the full pipeline offline (no database).

Usage:
    uv run python scripts/calibrate.py --resumes ../samples/calibration --labels ../samples/calibration/labels.csv \
        --jd ../samples/calibration/jd.txt [--model sentence-transformers/all-mpnet-base-v2] [--json out.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, get_settings
from app.services.pipeline import analyze_document, prepare_job
from app.services.resources import Resources, load_resources

THRESHOLDS = list(range(50, 90, 5))


def read_labels(path: Path) -> dict[str, bool]:
    with path.open() as fh:
        return {
            row["file_name"]: row["label"].strip().lower() in ("fit", "1", "yes", "true")
            for row in csv.DictReader(fh)
        }


def score_set(
    resumes: Path, labels: dict[str, bool], jd: str, resources: Resources, settings: Settings
) -> list[dict]:
    job = prepare_job("Calibration job", jd, resources)
    rows = []
    for name, fit in sorted(labels.items()):
        started = time.perf_counter()
        result = analyze_document((resumes / name).read_bytes(), name, job, resources, settings=settings)
        rows.append(
            {
                "file": name,
                "fit": fit,
                "clean": result.scores.clean.score if result.scores else 0.0,
                "raw": result.scores.raw.score if result.scores else 0.0,
                "decision": result.decision.system.value,
                "flags": [f.code for f in result.flags],
                "ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
    return rows


def auc(rows: list[dict]) -> float:
    pos = [r["clean"] for r in rows if r["fit"]]
    neg = [r["clean"] for r in rows if not r["fit"]]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def metrics(rows: list[dict]) -> dict:
    table = []
    for t in THRESHOLDS:
        tp = sum(r["fit"] and r["clean"] >= t for r in rows)
        fp = sum((not r["fit"]) and r["clean"] >= t for r in rows)
        fn = sum(r["fit"] and r["clean"] < t for r in rows)
        tn = sum((not r["fit"]) and r["clean"] < t for r in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        table.append(
            {
                "threshold": t,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
                "accuracy": round((tp + tn) / len(rows), 3),
            }
        )
    best = max(table, key=lambda r: (r["f1"], r["precision"], r["threshold"]))
    fit = [r["clean"] for r in rows if r["fit"]]
    unfit = [r["clean"] for r in rows if not r["fit"]]
    return {
        "n": len(rows),
        "n_fit": len(fit),
        "n_not_fit": len(unfit),
        "mean_fit": round(statistics.mean(fit), 2) if fit else None,
        "mean_not_fit": round(statistics.mean(unfit), 2) if unfit else None,
        "separation": round(statistics.mean(fit) - statistics.mean(unfit), 2) if fit and unfit else None,
        "auc": round(auc(rows), 4),
        "thresholds": table,
        "best": best,
        "mean_ms": round(statistics.mean(r["ms"] for r in rows), 1),
    }


def histogram(rows: list[dict]) -> str:
    lines = ["score    fit  not_fit"]
    for lo in range(0, 100, 10):
        hi = lo + 10
        a = sum(
            (r["fit"] and lo <= r["clean"] < hi) or (hi == 100 and r["fit"] and r["clean"] == 100)
            for r in rows
        )
        b = sum((not r["fit"]) and lo <= r["clean"] < hi for r in rows)
        lines.append(f"{lo:3d}-{hi:<3d}  {'#' * a:<12s} {'o' * b}")
    return "\n".join(lines)


def build(args: argparse.Namespace) -> tuple[Settings, Resources]:
    overrides: dict = {}
    if args.model:
        overrides["sbert_model"] = args.model
    if args.sbert_input:
        overrides["sbert_input"] = args.sbert_input
    settings = get_settings().model_copy(update=overrides)
    resources = load_resources(settings, check_dim=False)
    return settings, resources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--resumes", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--jd", type=Path, required=True)
    parser.add_argument("--model", default=None, help="SBERT model (default: SBERT_MODEL setting)")
    parser.add_argument("--sbert-input", choices=["natural", "stopword_removed"], default=None)
    parser.add_argument("--json", type=Path, default=None, help="write all results to this JSON file")
    args = parser.parse_args(argv)

    settings, resources = build(args)
    labels = read_labels(args.labels)
    rows = score_set(args.resumes, labels, args.jd.read_text(), resources, settings)
    m = metrics(rows)
    print(
        f"model={settings.sbert_model}  sbert_input={settings.sbert_input}  resumes={m['n']} (fit {m['n_fit']}, not fit {m['n_not_fit']})"
    )
    print(
        f"mean clean score: fit {m['mean_fit']}  not fit {m['mean_not_fit']}  separation {m['separation']}  ROC AUC {m['auc']}"
    )
    print("\nScore distribution (# = fit, o = not fit)\n" + histogram(rows))
    print("\nthreshold  precision  recall   F1     accuracy  (tp fp fn tn)")
    for r in m["thresholds"]:
        mark = "  <- best" if r["threshold"] == m["best"]["threshold"] else ""
        print(
            f"   {r['threshold']:3d}       {r['precision']:.3f}    {r['recall']:.3f}  {r['f1']:.3f}   {r['accuracy']:.3f}    ({r['tp']} {r['fp']} {r['fn']} {r['tn']}){mark}"
        )
    print(
        f"\nBest threshold by F1: {m['best']['threshold']} (precision {m['best']['precision']}, recall {m['best']['recall']})"
    )
    print(f"Mean processing time: {m['mean_ms']} ms per resume on {resources.device}")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "model": settings.sbert_model,
                    "sbert_input": settings.sbert_input,
                    "device": resources.device,
                    "metrics": m,
                    "rows": rows,
                },
                indent=2,
            )
        )
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
