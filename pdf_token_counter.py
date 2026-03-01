#!/home/swarnim/test_generator/test-generator/envi/bin/python
"""
PDF Token Counter - Count input tokens for a PDF file via OpenAI Responses API.

Usage:
  python pdf_token_counter.py "https://example.com/doc.pdf"          # URL
  python pdf_token_counter.py /path/to/local/file.pdf                # Local file
  python pdf_token_counter.py /path/to/file.pdf --analyze            # Also analyze
"""

import argparse
import concurrent.futures
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import traceback

from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader, PdfWriter

import prompts_biology
import export_excel

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

MODEL = "gpt-5-mini"
DEFAULT_PROMPT = "Summarize the key content of this PDF document."

# Pricing (₹ per 1M tokens) — update if model pricing changes
INPUT_COST_PER_1M_INR = 13.0
OUTPUT_COST_PER_1M_INR = 52.0


def upload_pdf(client: OpenAI, file_path: str) -> str:
    """
    Upload a local PDF to OpenAI via the Files API.

    Args:
        client: Initialized OpenAI client
        file_path: Path to the local PDF file

    Returns:
        The file ID from OpenAI
    """
    with open(file_path, "rb") as f:
        uploaded = client.files.create(file=f, purpose="user_data")
    logger.info(f"Uploaded file, ID: {uploaded.id}")
    return uploaded.id


def _build_file_content(file_id: str = None, file_url: str = None):
    """Build the input_file content block for either file_id or file_url."""
    if file_id:
        return {"type": "input_file", "file_id": file_id}
    return {"type": "input_file", "file_url": file_url}


def count_pdf_tokens(client: OpenAI, prompt: str, file_id: str = None, file_url: str = None) -> int:
    """
    Count input tokens for a PDF using the Responses API.

    Args:
        client: Initialized OpenAI client
        prompt: Text prompt to pair with the PDF
        file_id: OpenAI file ID (for local uploads)
        file_url: URL of the PDF file (for remote files)

    Returns:
        The number of input tokens
    """
    response = client.responses.input_tokens.count(
        model=MODEL,
        input=[{
            "role": "user",
            "content": [
                _build_file_content(file_id=file_id, file_url=file_url),
                {"type": "input_text", "text": prompt}
            ]
        }]
    )
    return response.input_tokens


def send_pdf_for_analysis(client: OpenAI, prompt: str, file_id: str = None, file_url: str = None) -> str:
    """
    Send a PDF to the Responses API for analysis.

    Args:
        client: Initialized OpenAI client
        prompt: Instructions for analyzing the PDF
        file_id: OpenAI file ID (for local uploads)
        file_url: URL of the PDF file (for remote files)

    Returns:
        The model's text response
    """
    response = client.responses.create(
        model=MODEL,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                _build_file_content(file_id=file_id, file_url=file_url)
            ]
        }]
    )

    output_text = ""
    for item in response.output:
        if hasattr(item, 'content') and item.content:
            for block in item.content:
                if hasattr(block, 'text'):
                    output_text += block.text

    usage = getattr(response, 'usage', None)
    input_tokens = getattr(usage, 'input_tokens', 0) if usage else 0
    output_tokens = getattr(usage, 'output_tokens', 0) if usage else 0
    return output_text, {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}


def get_pdf_page_count(pdf_path: str) -> int:
    """Count the number of pages in a local PDF file."""
    reader = PdfReader(pdf_path)
    return len(reader.pages)


def split_pdf_into_chunks(pdf_path: str, chunk_size: int = 10) -> list:
    """
    Split a PDF into chunks of chunk_size pages each.
    Last chunk gets the remainder.

    Returns:
        List of (temp_file_path, page_count) tuples.
    """
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    chunks = []

    for start in range(0, total_pages, chunk_size):
        end = min(start + chunk_size, total_pages)
        writer = PdfWriter()
        for page_idx in range(start, end):
            writer.add_page(reader.pages[page_idx])

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        writer.write(tmp)
        tmp.close()

        page_count = end - start
        chunks.append((tmp.name, page_count))
        logger.info(f"Chunk {len(chunks)}: pages {start + 1}-{end} ({page_count} pages) -> {tmp.name}")

    return chunks


def distribute_questions(total_questions: int, chunk_pages: list) -> list:
    """
    Distribute questions proportionally across chunks based on page count (unitary method).

    Args:
        total_questions: Total number of questions to generate
        chunk_pages: List of page counts per chunk, e.g. [10, 10, 10, 5]

    Returns:
        List of question counts per chunk, e.g. [6, 6, 6, 2] for 20 questions
    """
    total_pages = sum(chunk_pages)

    # Calculate proportional allocation using floor, track remainders
    raw = [(total_questions * pages) / total_pages for pages in chunk_pages]
    distribution = [math.floor(r) for r in raw]
    remainders = [r - math.floor(r) for r in raw]

    # Distribute leftover questions to chunks with largest remainders
    deficit = total_questions - sum(distribution)
    if deficit > 0:
        # Sort chunk indices by remainder (descending), give 1 extra to top chunks
        sorted_indices = sorted(range(len(remainders)), key=lambda i: remainders[i], reverse=True)
        for i in range(deficit):
            distribution[sorted_indices[i]] += 1

    return distribution


def _fix_latex_escapes(text: str) -> str:
    """Escape unescaped LaTeX backslash commands for valid JSON.

    Matches a backslash followed by 2+ alpha characters (e.g. \\alpha, \\rightarrow)
    but NOT already-escaped sequences (preceded by another backslash).
    Single-char JSON escapes (\\n, \\r, \\t, \\b, \\f) are untouched.
    """
    return re.sub(r'(?<!\\)\\([a-zA-Z]{2,})', r'\\\\\\1', text)


def parse_json_response(raw_response: str):
    """Parse JSON from raw LLM response, handling LaTeX escaping and markdown fencing."""
    # 1. Direct parse
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError:
        pass

    # 2. Fix unescaped LaTeX backslashes and retry
    fixed = _fix_latex_escapes(raw_response)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 3. Try markdown fencing extraction
    json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw_response, re.DOTALL)
    if json_match:
        content = json_match.group(1)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            fixed_content = _fix_latex_escapes(content)
            try:
                return json.loads(fixed_content)
            except json.JSONDecodeError:
                pass

    return None


def merge_results(chunk_results: list, total_requested: int) -> dict:
    """
    Merge multiple chunk results into a single unified result.
    Renumbers question_ids sequentially starting from 1.
    """
    all_questions = []
    base_result = None

    for chunk_result in chunk_results:
        if base_result is None:
            base_result = chunk_result

        for q in chunk_result.get("questions", []):
            q["question_id"] = len(all_questions) + 1
            all_questions.append(q)

    if base_result is None:
        return {"questions": [], "test_metadata": {}}

    base_result["questions"] = all_questions

    if "test_metadata" in base_result:
        base_result["test_metadata"]["total_questions"] = len(all_questions)
        base_result["test_metadata"]["requested_questions"] = total_requested

    return base_result


MAX_RETRIES = 2


def _process_single_chunk(client, chunk_index, total_chunks, chunk_path, chunk_page_count, chunk_q_count, q_type, difficulty):
    """
    Process a single chunk: upload, generate, parse with retries.
    Returns (chunk_index, result) on success or (chunk_index, None) on failure.
    """
    logger.info(f"Chunk {chunk_index + 1}/{total_chunks}: {chunk_q_count} questions from {chunk_page_count} pages")

    # Upload chunk
    chunk_file_id = upload_pdf(client, chunk_path)

    # Build prompt with this chunk's question count
    chunk_prompt = prompts_biology.get_prompt(q_type, difficulty, "biology", chunk_q_count)

    # Generate with retries
    result = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  Chunk {chunk_index + 1}/{total_chunks}: Generating {chunk_q_count} questions (attempt {attempt})...")
        raw_response, chunk_usage = send_pdf_for_analysis(client, chunk_prompt, file_id=chunk_file_id)

        result = parse_json_response(raw_response)
        if result is not None:
            result["_usage"] = chunk_usage
            num_q = len(result.get("questions", []))
            print(f"  Chunk {chunk_index + 1}/{total_chunks}: Done — {num_q} questions")
            logger.info(f"Chunk {chunk_index + 1}: Got {num_q} questions (requested {chunk_q_count})")
            return (chunk_index, result)
        else:
            logger.error(f"Chunk {chunk_index + 1}, attempt {attempt}: JSON parse failed")
            logger.error(f"Raw (first 300): {raw_response[:300]}")
            if attempt < MAX_RETRIES:
                print(f"  Chunk {chunk_index + 1}: Parse failed, retrying...")

    print(f"  Chunk {chunk_index + 1}: FAILED after {MAX_RETRIES} attempts", file=sys.stderr)
    return (chunk_index, None)


def generate_from_chunks(client, chunks, questions_per_chunk, q_type, difficulty, total_question_count):
    """
    Generate questions from multiple PDF chunks in PARALLEL and merge results.
    Each chunk is uploaded, sent for analysis, and parsed concurrently.
    Retries up to MAX_RETRIES times per chunk on parse failure.
    """
    # Build list of work items (skip chunks with 0 questions)
    work_items = []
    for i, ((chunk_path, chunk_page_count), chunk_q_count) in enumerate(zip(chunks, questions_per_chunk)):
        if chunk_q_count <= 0:
            logger.info(f"Skipping chunk {i + 1} (0 questions assigned)")
            continue
        work_items.append((i, chunk_path, chunk_page_count, chunk_q_count))

    if not work_items:
        return merge_results([], total_question_count)

    total = len(chunks)
    print(f"Processing {len(work_items)} chunks in parallel...")

    # Process all chunks concurrently
    indexed_results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(work_items)) as executor:
        futures = {
            executor.submit(
                _process_single_chunk,
                client, idx, total, path, pages, q_count, q_type, difficulty
            ): idx
            for idx, path, pages, q_count in work_items
        }

        for future in concurrent.futures.as_completed(futures):
            chunk_idx, result = future.result()
            if result is None:
                print(f"Error: Chunk {chunk_idx + 1} failed after {MAX_RETRIES} attempts. Aborting generation.", file=sys.stderr)
                # Cancel remaining futures
                for f in futures:
                    f.cancel()
                return None
            indexed_results[chunk_idx] = result

    # Merge in original chunk order
    ordered_results = [indexed_results[idx] for idx, _, _, _ in work_items]

    # Aggregate token usage across all chunks
    total_input = sum(r.get("_usage", {}).get("input_tokens", 0) for r in ordered_results)
    total_output = sum(r.get("_usage", {}).get("output_tokens", 0) for r in ordered_results)
    merged = merge_results(ordered_results, total_question_count)
    merged["_usage"] = {"input_tokens": total_input, "output_tokens": total_output, "total_tokens": total_input + total_output}
    return merged


def main():
    parser = argparse.ArgumentParser(
        description="Count input tokens for a PDF file and optionally analyze it via OpenAI Responses API."
    )
    parser.add_argument("pdf_source", nargs="?", default=None, help="URL (http/https) or local file path to a PDF")
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help=f"Text prompt to send with the PDF (default: '{DEFAULT_PROMPT}')"
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Also send the PDF for analysis and print the response"
    )
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not found. Set it in your .env file or environment.", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    QUESTION_TYPES = {
        "1": ("mcq", "MCQ"),
        "2": ("assertion_reason", "Assertion-Reason"),
        "3": ("match_the_column", "Match the Column"),
    }
    DIFFICULTIES = {
        "1": ("easy", "Easy"),
        "2": ("medium", "Medium"),
        "3": ("hard", "Hard"),
    }
    DEFAULT_QUESTION_COUNT = 50

    pdf_source = args.pdf_source
    file_id = None
    file_url = None

    # Main loop
    while True:
        # --- Step 1: Load PDF ---
        if not pdf_source:
            pdf_source = input("\nEnter PDF file path or URL: ").strip()
            if not pdf_source:
                print("Error: No PDF source provided.", file=sys.stderr)
                sys.exit(1)

        is_url = pdf_source.startswith(("http://", "https://"))
        if not is_url and not os.path.isfile(pdf_source):
            print(f"Error: File not found: {pdf_source}", file=sys.stderr)
            pdf_source = None
            continue

        file_id = None
        file_url = None
        page_count = None
        needs_splitting = False

        if is_url:
            file_url = pdf_source
        else:
            # Count pages for split decision
            try:
                page_count = get_pdf_page_count(pdf_source)
                needs_splitting = page_count > 20
            except Exception as e:
                logger.warning(f"Could not count pages: {e}")

            if not needs_splitting:
                # Upload whole PDF (no splitting needed)
                try:
                    logger.info(f"Uploading local file: {pdf_source}")
                    file_id = upload_pdf(client, pdf_source)
                except Exception as e:
                    logger.error(f"File upload failed: {e}")
                    logger.error(traceback.format_exc())
                    pdf_source = None
                    continue

        if page_count:
            print(f"\nPDF loaded: {pdf_source} ({page_count} pages)")
        else:
            print(f"\nPDF loaded: {pdf_source}")

        # --- Step 2: Choose question type ---
        print("\nSelect question type:")
        print("  1. MCQ")
        print("  2. Assertion-Reason")
        print("  3. Match the Column")
        type_choice = input("Enter choice (1/2/3): ").strip()
        if type_choice not in QUESTION_TYPES:
            print("Invalid choice. Defaulting to MCQ.")
            type_choice = "1"
        q_type, q_type_label = QUESTION_TYPES[type_choice]

        # --- Step 3: Choose difficulty ---
        print("\nSelect difficulty:")
        print("  1. Easy")
        print("  2. Medium")
        print("  3. Hard")
        diff_choice = input("Enter choice (1/2/3): ").strip()
        if diff_choice not in DIFFICULTIES:
            print("Invalid choice. Defaulting to Easy.")
            diff_choice = "1"
        difficulty, diff_label = DIFFICULTIES[diff_choice]

        # --- Step 4: Choose question count ---
        count_input = input(f"\nNumber of questions to generate (default {DEFAULT_QUESTION_COUNT}): ").strip()
        if count_input:
            try:
                question_count = int(count_input)
                if question_count < 1:
                    print("Must be at least 1. Using default.")
                    question_count = DEFAULT_QUESTION_COUNT
            except ValueError:
                print("Invalid number. Using default.")
                question_count = DEFAULT_QUESTION_COUNT
        else:
            question_count = DEFAULT_QUESTION_COUNT

        # --- Step 5: Build prompt and generate ---
        print(f"\n--- Generating {question_count} {q_type_label} ({diff_label}) questions ---")

        try:
            if needs_splitting:
                # --- Chunked generation for large PDFs ---
                chunks = []
                try:
                    chunks = split_pdf_into_chunks(pdf_source, chunk_size=10)
                    chunk_pages = [pg for _, pg in chunks]
                    questions_per_chunk = distribute_questions(question_count, chunk_pages)

                    print(f"PDF split into {len(chunks)} chunks: {chunk_pages} pages")
                    print(f"Questions per chunk: {questions_per_chunk}")

                    start_time = time.time()
                    result = generate_from_chunks(
                        client, chunks, questions_per_chunk,
                        q_type, difficulty, question_count
                    )
                    elapsed = time.time() - start_time

                    if result is None:
                        print("Error: Generation failed — could not parse one or more chunks.", file=sys.stderr)
                        again = input("\nDo you want to use another PDF? (y/n): ").strip().lower()
                        if again == 'y':
                            pdf_source = None
                            continue
                        else:
                            break
                finally:
                    # Clean up temp chunk files
                    for chunk_path, _ in chunks:
                        try:
                            os.unlink(chunk_path)
                        except OSError:
                            pass

            else:
                # --- Single-call generation (existing behavior) ---
                try:
                    gen_prompt = prompts_biology.get_prompt(q_type, difficulty, "biology", question_count)
                except ValueError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    continue

                try:
                    prompt_token_count = count_pdf_tokens(client, gen_prompt, file_id=file_id, file_url=file_url)
                    print(f"Prompt token count: {prompt_token_count:,}")
                except Exception as e:
                    logger.warning(f"Could not count prompt tokens: {e}")

                logger.info(f"Generating {question_count} {q_type_label} ({diff_label}) questions from PDF...")
                print("Generating questions... (this may take a minute)")
                start_time = time.time()
                raw_response, api_usage = send_pdf_for_analysis(client, gen_prompt, file_id=file_id, file_url=file_url)
                elapsed = time.time() - start_time

                result = parse_json_response(raw_response)
                if result is not None:
                    result["_usage"] = api_usage
                if result is None:
                    print("Error: Could not parse API response as JSON.", file=sys.stderr)
                    print(f"Raw response (first 500 chars): {raw_response[:500]}", file=sys.stderr)
                    again = input("\nDo you want to use another PDF? (y/n): ").strip().lower()
                    if again == 'y':
                        pdf_source = None
                        continue
                    else:
                        break

            # Export to Excel
            usage = result.pop("_usage", {})
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            tot_tok = usage.get("total_tokens", in_tok + out_tok)
            in_cost = round(in_tok * INPUT_COST_PER_1M_INR / 1_000_000, 4)
            out_cost = round(out_tok * OUTPUT_COST_PER_1M_INR / 1_000_000, 4)
            tot_cost = round(in_cost + out_cost, 4)

            excel_bytes = export_excel.export_questions_to_excel(
                result,
                time_taken=elapsed,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                input_cost=in_cost,
                output_cost=out_cost,
                total_cost=tot_cost,
            )

            # Save to dedicated output/ folder
            script_dir = os.path.dirname(os.path.abspath(__file__))
            output_dir = os.path.join(script_dir, "output")
            os.makedirs(output_dir, exist_ok=True)

            pdf_name = os.path.splitext(os.path.basename(pdf_source))[0]
            output_file = os.path.join(output_dir, f"{pdf_name}_{q_type}_{difficulty}_{question_count}.xlsx")
            with open(output_file, "wb") as f:
                f.write(excel_bytes)

            num_questions = len(result.get("questions", []))
            print(f"\n{'='*50}")
            print(f"Question Generation Complete!")
            print(f"{'='*50}")
            print(f"Type:       {q_type_label}")
            print(f"Difficulty: {diff_label}")
            print(f"Questions:  {num_questions}")
            print(f"Time taken: {elapsed:.1f}s")
            if needs_splitting:
                chunk_pages = [pg for _, pg in chunks] if chunks else []
                print(f"Chunks:     {len(chunk_pages)} ({chunk_pages} pages)")
            print(f"Excel file: {os.path.abspath(output_file)}")
            print(f"{'='*50}")

        except Exception as e:
            logger.error(f"Question generation failed: {e}")
            logger.error(traceback.format_exc())

        # --- Step 6: Ask if user wants to use another PDF ---
        again = input("\nDo you want to use another PDF? (y/n): ").strip().lower()
        if again != 'y':
            print("Done. Goodbye!")
            break
        pdf_source = None  # Reset to prompt for new PDF


if __name__ == "__main__":
    main()
