#!/home/swarnim/test_generator/test-generator/envi/bin/python
"""
PDF Token Counter - Count input tokens for a PDF file via OpenAI Responses API.

Usage:
  python pdf_token_counter.py "https://example.com/doc.pdf"          # URL
  python pdf_token_counter.py /path/to/local/file.pdf                # Local file
  python pdf_token_counter.py /path/to/file.pdf --analyze            # Also analyze
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback

from dotenv import load_dotenv
from openai import OpenAI

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
    return output_text


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
        if is_url:
            file_url = pdf_source
        else:
            try:
                logger.info(f"Uploading local file: {pdf_source}")
                file_id = upload_pdf(client, pdf_source)
            except Exception as e:
                logger.error(f"File upload failed: {e}")
                logger.error(traceback.format_exc())
                pdf_source = None
                continue

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
        try:
            gen_prompt = prompts_biology.get_prompt(q_type, difficulty, "biology", question_count)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            continue

        print(f"\n--- Generating {question_count} {q_type_label} ({diff_label}) questions ---")

        try:
            prompt_token_count = count_pdf_tokens(client, gen_prompt, file_id=file_id, file_url=file_url)
            print(f"Prompt token count: {prompt_token_count:,}")
        except Exception as e:
            logger.warning(f"Could not count prompt tokens: {e}")

        try:
            logger.info(f"Generating {question_count} {q_type_label} ({diff_label}) questions from PDF...")
            print("Generating questions... (this may take a minute)")
            start_time = time.time()
            raw_response = send_pdf_for_analysis(client, gen_prompt, file_id=file_id, file_url=file_url)
            elapsed = time.time() - start_time

            # Parse JSON from the response
            try:
                result = json.loads(raw_response)
            except json.JSONDecodeError:
                json_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw_response, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    print("Error: Could not parse API response as JSON.", file=sys.stderr)
                    print(f"Raw response (first 500 chars): {raw_response[:500]}", file=sys.stderr)
                    # Ask if they want to try another PDF anyway
                    again = input("\nDo you want to use another PDF? (y/n): ").strip().lower()
                    if again == 'y':
                        pdf_source = None
                        continue
                    else:
                        break

            # Export to Excel
            excel_bytes = export_excel.export_questions_to_excel(result, time_taken=elapsed)

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
