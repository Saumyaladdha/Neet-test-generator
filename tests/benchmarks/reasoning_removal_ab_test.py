"""
A/B test: detector latency BEFORE vs AFTER removing the 9 unused
*_reasoning fields from the detection output schema.

Hits the real, running /detect endpoint (http://127.0.0.1:8001 by default)
so results reflect actual production behavior, not a mocked call.

Usage:
    python tests/benchmarks/reasoning_removal_ab_test.py before
    # ... then apply the schema change ...
    python tests/benchmarks/reasoning_removal_ab_test.py after
    # ... then print the comparison ...
    python tests/benchmarks/reasoning_removal_ab_test.py compare

Output:
    tests/benchmarks/results/reasoning_ab_<before|after>_<timestamp>.json
    tests/benchmarks/results/reasoning_ab_summary_<timestamp>.txt
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8001"
USER_ID = "e2e-test-user-english"
RESULTS_DIR = Path(__file__).parent / "results"

PDFS = [
    ("tests/5pagePdf.pdf", "5-page"),
    ("tests/10pagePdf.pdf", "10-page"),
    ("tests/NCERT-Class-11-Biology.pdf", "NCERT-Class-11-Biology (large)"),
]


def _run_detect(path: str) -> dict:
    with open(path, "rb") as f:
        files = {"files": (Path(path).name, f, "application/pdf")}
        data = {"user_id": USER_ID, "subject": "biology", "medium": "english", "file_type": "pdf"}
        t0 = time.time()
        r = requests.post(f"{BASE_URL}/detect", files=files, data=data, stream=True, timeout=120)
        result = None
        for line in r.iter_lines():
            if line and line.startswith(b"data:") and b"results" in line:
                result = json.loads(line[5:].decode())
        wall_clock = round(time.time() - t0, 2)

    if result is None:
        return {"error": "no result parsed", "wall_clock_seconds": wall_clock}

    total_questions = sum(
        cell.get("count", 0)
        for by_type in result["results"].values()
        for cell in by_type.values()
    )
    return {
        "server_elapsed_seconds": result.get("elapsed_seconds"),
        "wall_clock_seconds": wall_clock,
        "total_questions_detected": total_questions,
        "results": result["results"],
    }


def run_phase(label: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for path, name in PDFS:
        print(f"[{label}] running {name} ({path}) ...")
        outcome = _run_detect(path)
        outcome["pdf"] = name
        outcome["path"] = path
        runs.append(outcome)
        print(f"[{label}] {name}: server_elapsed={outcome.get('server_elapsed_seconds')}s "
              f"wall_clock={outcome.get('wall_clock_seconds')}s "
              f"total_questions={outcome.get('total_questions_detected')}")

    out_path = RESULTS_DIR / f"reasoning_ab_{label}.json"
    with open(out_path, "w") as f:
        json.dump({"label": label, "runs": runs}, f, indent=2)
    print(f"\nSaved: {out_path}")
    return out_path


def run_trials(label: str, n: int) -> Path:
    """Run each PDF n times under the current schema; save raw + aggregate stats."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    per_pdf = []
    for path, name in PDFS:
        trial_runs = []
        for i in range(n):
            print(f"[{label}] {name} trial {i+1}/{n} ...")
            outcome = _run_detect(path)
            trial_runs.append(outcome)
            print(f"[{label}] {name} trial {i+1}/{n}: "
                  f"server_elapsed={outcome.get('server_elapsed_seconds')}s "
                  f"total_questions={outcome.get('total_questions_detected')}")

        latencies = [r["server_elapsed_seconds"] for r in trial_runs if r.get("server_elapsed_seconds") is not None]
        counts = [r["total_questions_detected"] for r in trial_runs if r.get("total_questions_detected") is not None]
        per_pdf.append({
            "pdf": name,
            "path": path,
            "trials": trial_runs,
            "latency_min": min(latencies) if latencies else None,
            "latency_max": max(latencies) if latencies else None,
            "latency_mean": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "questions_min": min(counts) if counts else None,
            "questions_max": max(counts) if counts else None,
            "questions_mean": round(sum(counts) / len(counts), 1) if counts else None,
        })

    out_path = RESULTS_DIR / f"reasoning_ab_trials_{label}.json"
    with open(out_path, "w") as f:
        json.dump({"label": label, "n_trials": n, "per_pdf": per_pdf}, f, indent=2)
    print(f"\nSaved: {out_path}")
    return out_path


def compare_trials():
    before_path = RESULTS_DIR / "reasoning_ab_trials_before.json"
    after_path = RESULTS_DIR / "reasoning_ab_trials_after.json"
    if not before_path.exists() or not after_path.exists():
        print("Need both 'trials before' and 'trials after' runs saved first.")
        sys.exit(1)

    before = json.load(open(before_path))
    after = json.load(open(after_path))

    lines = []
    lines.append("=" * 118)
    lines.append(f"  DETECTOR REASONING-FIELD REMOVAL — REPEATED TRIALS "
                 f"(n={before['n_trials']} before / n={after['n_trials']} after)")
    lines.append("=" * 118)
    header = (f"{'PDF':<32}{'Latency before (min-max, mean)':<32}{'Latency after (min-max, mean)':<32}"
              f"{'Qs before':<12}{'Qs after':<10}")
    lines.append(header)
    lines.append("-" * 118)
    for b, a in zip(before["per_pdf"], after["per_pdf"]):
        b_lat = f"{b['latency_min']}-{b['latency_max']}s (avg {b['latency_mean']})"
        a_lat = f"{a['latency_min']}-{a['latency_max']}s (avg {a['latency_mean']})"
        b_q = f"{b['questions_min']}-{b['questions_max']} (avg {b['questions_mean']})"
        a_q = f"{a['questions_min']}-{a['questions_max']} (avg {a['questions_mean']})"
        lines.append(f"{b['pdf']:<32}{b_lat:<32}{a_lat:<32}{b_q:<12}{a_q:<10}")

        # Overlap check: do the before/after question-count ranges overlap at all?
        overlap = not (a["questions_max"] < b["questions_min"] or b["questions_max"] < a["questions_min"])
        verdict = "RANGES OVERLAP — likely just normal LLM variance" if overlap else "NO OVERLAP — reasoning removal may have shifted counts"
        lines.append(f"    -> {verdict}")

    lines.append("=" * 118)
    summary = "\n".join(lines)
    print(summary)

    out_path = RESULTS_DIR / "reasoning_ab_trials_summary.txt"
    with open(out_path, "w") as f:
        f.write(summary + "\n")
    print(f"\nSaved: {out_path}")


def compare():
    before_path = RESULTS_DIR / "reasoning_ab_before.json"
    after_path = RESULTS_DIR / "reasoning_ab_after.json"
    if not before_path.exists() or not after_path.exists():
        print("Need both 'before' and 'after' runs saved first.")
        sys.exit(1)

    before = json.load(open(before_path))["runs"]
    after = json.load(open(after_path))["runs"]

    lines = []
    lines.append("=" * 100)
    lines.append("  DETECTOR REASONING-FIELD REMOVAL — BEFORE vs AFTER")
    lines.append("=" * 100)
    lines.append(f"{'PDF':<35}{'Before (s)':<15}{'After (s)':<15}{'Delta (s)':<15}{'Qs Before':<12}{'Qs After':<10}")
    lines.append("-" * 100)
    for b, a in zip(before, after):
        b_t = b.get("server_elapsed_seconds") or 0
        a_t = a.get("server_elapsed_seconds") or 0
        delta = round(b_t - a_t, 2)
        lines.append(
            f"{b['pdf']:<35}{b_t:<15}{a_t:<15}{delta:<15}"
            f"{b.get('total_questions_detected', '?'):<12}{a.get('total_questions_detected', '?'):<10}"
        )
    lines.append("=" * 100)
    summary = "\n".join(lines)
    print(summary)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S") if False else "latest"  # timestamp stamped by caller if needed
    out_path = RESULTS_DIR / f"reasoning_ab_summary_{ts}.txt"
    with open(out_path, "w") as f:
        f.write(summary + "\n")
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "trials" and sys.argv[2] in ("before", "after"):
        print("Usage: trials <before|after> <n>")
        sys.exit(1)
    if len(sys.argv) == 4 and sys.argv[1] == "trials" and sys.argv[2] in ("before", "after"):
        run_trials(sys.argv[2], int(sys.argv[3]))
    elif len(sys.argv) == 2 and sys.argv[1] == "compare-trials":
        compare_trials()
    elif len(sys.argv) == 2 and sys.argv[1] in ("before", "after", "compare"):
        compare() if sys.argv[1] == "compare" else run_phase(sys.argv[1])
    else:
        print(__doc__)
        sys.exit(1)
