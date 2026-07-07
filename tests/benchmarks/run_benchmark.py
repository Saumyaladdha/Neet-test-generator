"""
Detector Model Benchmark
========================
Tests all major vision+PDF models on NCERT-Class-11-Biology.pdf
and records: latency, parsed counts, cost estimate, finish reason.

Usage:
    cd test-generator
    source venv/bin/activate
    python benchmarks/run_benchmark.py

Output:
    benchmarks/results/benchmark_<timestamp>.json
    benchmarks/results/benchmark_<timestamp>.csv
    benchmarks/results/summary_<timestamp>.txt  (pretty table)

Set ANTHROPIC_API_KEY in .env to include Claude models.
"""

import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

# ── Logger setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("benchmark")

# ── Model registry ────────────────────────────────────────────────────────────
# Each entry: id, display_name, provider, input_cost_per_1m, output_cost_per_1m
# Costs in USD.

MODELS = [
    # ── OpenAI ────────────────────────────────────────────────────────────────
    # PDF: uploaded to OpenAI Files API (input_file content block)
    {
        "id":           "gpt-5.5",
        "name":         "GPT-5.5",
        "provider":     "openai",
        "input_$/1m":   5.00,
        "output_$/1m":  30.00,
        "notes":        "Flagship. 1M context. Vision + PDF via Files API.",
    },
    {
        "id":           "gpt-5.4",
        "name":         "GPT-5.4",
        "provider":     "openai",
        "input_$/1m":   2.50,
        "output_$/1m":  15.00,
        "notes":        "Previous flagship. Vision + PDF via Files API.",
    },
    {
        "id":           "gpt-5.4-mini",
        "name":         "GPT-5.4-mini",
        "provider":     "openai",
        "input_$/1m":   0.75,
        "output_$/1m":  4.50,
        "notes":        "Current default. 2x faster than gpt-5-mini.",
    },
    {
        "id":           "gpt-4.1-nano",
        "name":         "GPT-4.1 nano",
        "provider":     "openai",
        "input_$/1m":   0.10,
        "output_$/1m":  0.40,
        "notes":        "Cheapest OpenAI. Vision + PDF via Files API.",
    },

    # ── Gemini ────────────────────────────────────────────────────────────────
    # PDF: passed as S3 presigned URL directly (no upload needed)
    {
        "id":           "gemini-3.5-flash",
        "name":         "Gemini 3.5 Flash",
        "provider":     "gemini",
        "input_$/1m":   1.50,
        "output_$/1m":  9.00,
        "notes":        "Latest Gemini (May 2026). 1M ctx. 64K output.",
    },
    {
        "id":           "gemini-3.1-flash-lite",
        "name":         "Gemini 3.1 Flash-Lite",
        "provider":     "gemini",
        "input_$/1m":   0.25,
        "output_$/1m":  1.50,
        "notes":        "381.9 tok/s. Fastest Gemini. 1M ctx.",
    },
    {
        "id":           "gemini-2.5-flash",
        "name":         "Gemini 2.5 Flash",
        "provider":     "gemini",
        "input_$/1m":   0.30,
        "output_$/1m":  2.50,
        "notes":        "Current default. thinking_budget=0 for speed.",
    },
    {
        "id":           "gemini-2.5-flash-lite",
        "name":         "Gemini 2.5 Flash-Lite",
        "provider":     "gemini",
        "input_$/1m":   0.10,
        "output_$/1m":  0.40,
        "notes":        "Cheapest Gemini. Good for high volume.",
    },

    # ── Claude ────────────────────────────────────────────────────────────────
    # PDF: base64 encoded inline (no Files API for Claude)
    {
        "id":           "claude-sonnet-4-6",
        "name":         "Claude Sonnet 4.6",
        "provider":     "claude",
        "input_$/1m":   3.00,
        "output_$/1m":  15.00,
        "notes":        "Balanced speed/quality.",
    },
]

ROOT = Path(__file__).parent.parent

# All PDFs to benchmark — add more entries as needed
PDF_FILES = [
    ROOT / "NCERT-Class-11-Biology.pdf",
    ROOT / "kebo101.pdf",
    ROOT / "kech101.pdf",
    ROOT / "NCERT-Hindi-Class-12-Biology.pdf",
]

# JSON output format instruction (same as detector)
_JSON_INSTRUCTION = """
Return ONLY a valid JSON object with exactly these 9 integer fields:
{
  "easy_mcq_count": <integer>,
  "easy_ar_count": <integer>,
  "easy_mtc_count": <integer>,
  "medium_mcq_count": <integer>,
  "medium_ar_count": <integer>,
  "medium_mtc_count": <integer>,
  "hard_mcq_count": <integer>,
  "hard_ar_count": <integer>,
  "hard_mtc_count": <integer>
}
"""

_COUNT_FIELDS = [
    "easy_mcq_count", "easy_ar_count", "easy_mtc_count",
    "medium_mcq_count", "medium_ar_count", "medium_mtc_count",
    "hard_mcq_count", "hard_ar_count", "hard_mtc_count",
]

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "detector" / "system_prompt.txt"


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def parse_json_counts(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found: {text[:200]}")
    data = json.loads(text[start:end + 1])
    return {f: int(data.get(f, 0)) for f in _COUNT_FIELDS}


# ── Provider implementations ──────────────────────────────────────────────────

def run_openai(model_id: str, pdf_bytes: bytes, system_prompt: str) -> dict:
    """Upload PDF to OpenAI Files API then run detector."""
    import tempfile
    from openai import OpenAI

    client = OpenAI()

    log.info("[OpenAI] Uploading PDF to Files API (%.1f MB)...", len(pdf_bytes)/1024/1024)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            uploaded = client.files.create(file=f, purpose="user_data")
        file_id = uploaded.id
    finally:
        os.unlink(tmp_path)

    log.info("[OpenAI] Upload done — file_id=%s. Sending to %s...", file_id[:12], model_id)
    response = client.responses.create(
        model=model_id,
        instructions=system_prompt + _JSON_INSTRUCTION,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_file", "file_id": file_id},
                {"type": "input_text", "text": "Analyse this PDF and return the JSON counts."},
            ],
        }],
        max_output_tokens=1024,
        temperature=0,
        store=True,
    )

    # Extract text — handle both "output_text" and plain "text" block types
    text = ""
    for item in getattr(response, "output", []):
        if getattr(item, "type", None) == "message":
            for block in getattr(item, "content", []):
                if hasattr(block, "text"):
                    text += block.text
        elif getattr(item, "type", None) == "text":
            text += getattr(item, "text", "")

    usage = getattr(response, "usage", None)
    input_tokens  = getattr(usage, "input_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

    log.info("[OpenAI] Response received — in=%d out=%d tokens", input_tokens, output_tokens)
    try:
        client.files.delete(file_id)
        log.info("[OpenAI] Cleaned up file_id=%s", file_id[:12])
    except Exception:
        pass

    return {"text": text, "input_tokens": input_tokens, "output_tokens": output_tokens}


def run_gemini(model_id: str, pdf_url: str, system_prompt: str) -> dict:
    """Pass S3 presigned URL directly to Gemini — no upload needed."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    log.info("[Gemini] Sending PDF via presigned URL to %s...", model_id)
    response = client.models.generate_content(
        model=model_id,
        contents=[types.Content(role="user", parts=[
            types.Part.from_uri(file_uri=pdf_url, mime_type="application/pdf"),
            # from_text() requires keyword arg in current google-genai SDK
            types.Part.from_text(text="Analyse this PDF and return the JSON counts."),
        ])],
        config=types.GenerateContentConfig(
            system_instruction=system_prompt + _JSON_INSTRUCTION,
            response_mime_type="application/json",
            max_output_tokens=1024,
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )

    usage = getattr(response, "usage_metadata", None)
    input_tokens  = getattr(usage, "prompt_token_count", 0) if usage else 0
    output_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0

    log.info("[Gemini] Response received — in=%d out=%d tokens", input_tokens, output_tokens)
    return {"text": response.text, "input_tokens": input_tokens, "output_tokens": output_tokens}


# Claude's context limit is 200K tokens. The NCERT PDF (~245-280K tokens) exceeds this,
# so we extract only the first MAX_CLAUDE_PAGES pages for benchmarking.
# 30 pages ≈ 140K tokens — safely within 200K after adding system prompt.
def run_claude(model_id: str, pdf_bytes: bytes, system_prompt: str) -> dict:
    """Send PDF as base64 to Claude Messages API."""
    import anthropic
    import base64

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    log.info("[Claude] Encoding PDF (%.1f MB) as base64 and sending to %s...",
             len(pdf_bytes)/1024/1024, model_id)
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    response = client.messages.create(
        model=model_id,
        max_tokens=1024,
        temperature=0,
        system=system_prompt + _JSON_INSTRUCTION,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {"type": "text", "text": "Analyse this PDF and return the JSON counts."},
            ],
        }],
    )

    log.info("[Claude] Response received — stop_reason=%s in=%d out=%d tokens",
             response.stop_reason, response.usage.input_tokens, response.usage.output_tokens)
    if response.stop_reason == "refusal":
        log.warning("[Claude] Refused to process document (copyright/policy).")
        raise ValueError("Claude refused to process this document (copyright/policy).")

    # Exclude thinking blocks — only keep text blocks
    text = "".join(
        b.text for b in response.content
        if getattr(b, "type", None) == "text" and hasattr(b, "text")
    )
    usage = response.usage
    return {
        "text": text,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


# ── Benchmark runner ──────────────────────────────────────────────────────────

def run_model(model: dict, pdf_bytes: bytes, pdf_s3_url: str, system_prompt: str) -> dict:
    provider = model["provider"]
    model_id = model["id"]

    log.info("━━ Starting: %s (%s)", model["name"], provider)
    start = time.perf_counter()
    error = None
    counts = {}
    raw_text = ""
    input_tokens = output_tokens = 0

    try:
        if provider == "openai":
            res = run_openai(model_id, pdf_bytes, system_prompt)
        elif provider == "gemini":
            res = run_gemini(model_id, pdf_s3_url, system_prompt)
        elif provider == "claude":
            res = run_claude(model_id, pdf_bytes, system_prompt)
        else:
            raise ValueError(f"Unknown provider: {provider}")

        raw_text      = res["text"]
        input_tokens  = res["input_tokens"]
        output_tokens = res["output_tokens"]
        log.info("[Parse] Extracting JSON from response (%d chars)...", len(raw_text))
        counts = parse_json_counts(raw_text)

    except Exception as exc:
        error = str(exc)
        log.error("✗ %s FAILED: %s", model["name"], error[:120])

    elapsed = round(time.perf_counter() - start, 2)

    cost_usd = round(
        (input_tokens / 1_000_000) * model["input_$/1m"] +
        (output_tokens / 1_000_000) * model["output_$/1m"],
        6,
    )

    total_q = sum(counts.values()) if counts else 0
    status = "OK" if not error else "FAIL"

    if error:
        log.info("✗ %-25s  FAIL  %5.1fs  error: %s", model["name"], elapsed, error[:60])
    else:
        log.info("✓ %-25s  OK    %5.1fs  %3d Qs  in=%d out=%d  $%.5f",
                 model["name"], elapsed, total_q, input_tokens, output_tokens, cost_usd)

    return {
        "model_id":       model_id,
        "model_name":     model["name"],
        "provider":       provider,
        "status":         status,
        "latency_s":      elapsed,
        "input_tokens":   input_tokens,
        "output_tokens":  output_tokens,
        "cost_usd":       cost_usd,
        "input_$/1m":     model["input_$/1m"],
        "output_$/1m":    model["output_$/1m"],
        "notes":          model["notes"],
        "counts":         counts,
        "total_questions": total_q,
        "raw_text":       raw_text[:300],
        "error":          error or "",
    }


def upload_pdf_to_s3(pdf_bytes: bytes) -> str:
    """Upload PDF to S3 and return presigned URL (used by Gemini path)."""
    import uuid
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from core.storage import upload_bytes, get_presigned_url

    log.info("[S3] Uploading %.1f MB to S3...", len(pdf_bytes)/1024/1024)
    uri = upload_bytes(
        pdf_bytes,
        user_id="benchmark",
        context_id=str(uuid.uuid4()),
        filename="pdfs/benchmark.pdf",
        content_type="application/pdf",
    )
    url = get_presigned_url(uri, expires=7200)
    log.info("[S3] Upload complete — presigned URL generated")
    return url


def _get_page_count(pdf_path: Path) -> int:
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        n = doc.page_count
        doc.close()
        return n
    except Exception:
        return 0


def print_summary(results: list, pdf_name: str, pdf_pages: int) -> str:
    lines = []
    lines.append("\n" + "=" * 108)
    lines.append(f"  DETECTOR BENCHMARK — {pdf_name}  ({pdf_pages} pages)")
    lines.append("=" * 108)
    header = f"{'Model':<25} {'Provider':<8} {'Status':<8} {'Pages':>6} {'Latency':>9} {'Total Qs':>9} {'In Tok':>8} {'Out Tok':>8} {'Cost':>10}"
    lines.append(header)
    lines.append("-" * 108)

    for r in sorted(results, key=lambda x: x["latency_s"]):
        line = (
            f"{r['model_name']:<25} "
            f"{r['provider']:<8} "
            f"{r['status']:<8} "
            f"{r['pdf_pages']:>6} "
            f"{r['latency_s']:>8.1f}s "
            f"{r['total_questions']:>9} "
            f"{r['input_tokens']:>8} "
            f"{r['output_tokens']:>8} "
            f"${r['cost_usd']:>9.5f}"
        )
        lines.append(line)

    lines.append("=" * 108)
    lines.append("\nCOUNTS BREAKDOWN (easy/medium/hard × mcq/ar/mtc):")
    lines.append(f"{'Model':<25} {'E-MCQ':>6} {'E-AR':>6} {'E-MTC':>6} {'M-MCQ':>6} {'M-AR':>6} {'M-MTC':>6} {'H-MCQ':>6} {'H-AR':>6} {'H-MTC':>6} {'TOTAL':>6}")
    lines.append("-" * 108)
    for r in results:
        if r["status"] == "OK":
            c = r["counts"]
            line = (
                f"{r['model_name']:<25} "
                f"{c.get('easy_mcq_count',0):>6} "
                f"{c.get('easy_ar_count',0):>6} "
                f"{c.get('easy_mtc_count',0):>6} "
                f"{c.get('medium_mcq_count',0):>6} "
                f"{c.get('medium_ar_count',0):>6} "
                f"{c.get('medium_mtc_count',0):>6} "
                f"{c.get('hard_mcq_count',0):>6} "
                f"{c.get('hard_ar_count',0):>6} "
                f"{c.get('hard_mtc_count',0):>6} "
                f"{r['total_questions']:>6}"
            )
            lines.append(line)
        else:
            lines.append(f"{r['model_name']:<25}  {r['status']}: {r['error'][:70]}")

    lines.append("=" * 108)
    return "\n".join(lines)


def _run_pdf(pdf_path: Path, models_to_run: list, system_prompt: str,
             has_gemini: bool, ts: str) -> list:
    """Run all models against a single PDF. Returns results list."""
    pdf_name = pdf_path.name
    pdf_pages = _get_page_count(pdf_path)
    pdf_bytes = pdf_path.read_bytes()
    size_mb = len(pdf_bytes) / 1024 / 1024

    log.info("")
    log.info("══════════════════════════════════════════════════════════")
    log.info("  PDF: %s  (%d pages, %.1f MB)", pdf_name, pdf_pages, size_mb)
    log.info("══════════════════════════════════════════════════════════")

    # Upload once to S3 for Gemini path
    pdf_s3_url = ""
    gemini_in_run = any(m["provider"] == "gemini" for m in models_to_run)
    if gemini_in_run and has_gemini:
        try:
            pdf_s3_url = upload_pdf_to_s3(pdf_bytes)
        except Exception as e:
            log.error("[S3] Upload failed — Gemini will be skipped: %s", e)

    results = []
    for i, model in enumerate(models_to_run, 1):
        if model["provider"] == "gemini" and not pdf_s3_url:
            log.warning("Skipping %s — no S3 URL", model["name"])
            continue
        log.info("[%d/%d] %s", i, len(models_to_run), model["name"])
        result = run_model(model, pdf_bytes, pdf_s3_url, system_prompt)
        result["pdf_name"]  = pdf_name
        result["pdf_pages"] = pdf_pages
        results.append(result)
        time.sleep(1)

    ok    = sum(1 for r in results if r["status"] == "OK")
    fail  = len(results) - ok
    log.info("PDF done: %d OK, %d FAIL", ok, fail)
    return results


def main():
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║        NEET Detector Model Benchmark                     ║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    log.info("PDFs: %s", [p.name for p in PDF_FILES])
    log.info("Models registered: %d", len(MODELS))

    missing = [p for p in PDF_FILES if not p.exists()]
    if missing:
        log.error("PDFs not found: %s", missing)
        sys.exit(1)

    system_prompt = load_system_prompt()
    log.info("System prompt loaded (%d chars)", len(system_prompt))

    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))

    try:
        import anthropic
        anthropic_ok = True
    except ImportError:
        anthropic_ok = False
        if has_claude:
            log.warning("anthropic package not installed. Run: pip install anthropic")

    skip = set()
    if not has_openai:  skip.add("openai")
    if not has_gemini:  skip.add("gemini")
    if not has_claude or not anthropic_ok: skip.add("claude")

    models_to_run = [m for m in MODELS if m["provider"] not in skip]
    skipped = [m for m in MODELS if m["provider"] in skip]

    only_ids = os.environ.get("BENCHMARK_MODEL_IDS", "").strip()
    if only_ids:
        wanted = {x.strip() for x in only_ids.split(",") if x.strip()}
        models_to_run = [m for m in models_to_run if m["id"] in wanted]
        log.info("BENCHMARK_MODEL_IDS filter applied: %s", sorted(wanted))

    log.info("API keys — OpenAI:%s  Gemini:%s  Claude:%s",
             "YES" if has_openai else "NO",
             "YES" if has_gemini else "NO",
             "YES" if has_claude else "NO")
    if skipped:
        log.warning("Skipping (no key): %s", [m["name"] for m in skipped])
    log.info("Running %d models × %d PDFs", len(models_to_run), len(PDF_FILES))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)

    all_results = []
    all_summaries = []

    for pdf_path in PDF_FILES:
        pdf_pages = _get_page_count(pdf_path)
        results = _run_pdf(pdf_path, models_to_run, system_prompt, has_gemini, ts)
        all_results.extend(results)
        summary = print_summary(results, pdf_path.name, pdf_pages)
        print(summary)
        all_summaries.append(summary)

    # Save combined results
    json_path = out_dir / f"benchmark_{ts}.json"
    csv_path  = out_dir / f"benchmark_{ts}.csv"
    txt_path  = out_dir / f"summary_{ts}.txt"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": ts, "pdfs": [p.name for p in PDF_FILES], "results": all_results}, f, indent=2)

    flat_fields = [
        "pdf_name", "pdf_pages", "model_name", "provider", "status", "latency_s",
        "total_questions", "input_tokens", "output_tokens", "cost_usd",
        "input_$/1m", "output_$/1m", "notes", "error",
    ] + _COUNT_FIELDS
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=flat_fields, extrasaction="ignore")
        writer.writeheader()
        for r in all_results:
            row = {**r, **r.get("counts", {})}
            writer.writerow(row)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(all_summaries))

    ok_total   = sum(1 for r in all_results if r["status"] == "OK")
    fail_total = len(all_results) - ok_total
    log.info("")
    log.info("All done — %d OK, %d FAIL across %d PDFs",
             ok_total, fail_total, len(PDF_FILES))
    log.info("Results saved:")
    log.info("  JSON:    %s", json_path)
    log.info("  CSV:     %s", csv_path)
    log.info("  Summary: %s", txt_path)


if __name__ == "__main__":
    main()
