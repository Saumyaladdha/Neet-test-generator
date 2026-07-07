"""
PDF utilities:
  - get_page_count()         count pages in a local PDF
  - split_into_chunks()      split a large PDF into 10-page temp files
  - distribute_questions()   proportional + largest-remainder allocation
  - upload_chunk()           upload a PDF file to OpenAI Files API, return file_id
  - cleanup_chunks()         delete temp chunk files after processing
"""

import logging
import math
import os
import tempfile

from openai import OpenAI
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


def get_page_count(pdf_path: str) -> int:
    """Return the number of pages in a local PDF file."""
    reader = PdfReader(pdf_path)
    return len(reader.pages)


def get_page_count_from_bytes(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF given as raw bytes."""
    import io
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return len(reader.pages)


def extract_pages(pdf_bytes: bytes, start_page: int, end_page: int) -> bytes:
    """
    Extract pages [start_page, end_page] (1-indexed, inclusive) from a PDF.
    Returns the extracted pages as a new PDF bytes object.
    """
    import io
    # Coerce to int — LLM topic detection can return float page numbers (e.g. 8.0)
    start_page = int(start_page)
    end_page   = int(end_page)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    total = len(reader.pages)
    sp = max(1, start_page) - 1          # convert to 0-indexed
    ep = min(total, end_page)            # inclusive, 1-indexed
    for i in range(sp, ep):
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def split_into_chunks(pdf_path: str, chunk_size: int = 10) -> list[tuple[str, int]]:
    """
    Split a PDF into chunks of up to chunk_size pages each.
    The last chunk gets the remainder pages.

    Returns:
        List of (temp_file_path, page_count) tuples.
        Caller must call cleanup_chunks() when done.
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
        logger.info(f"Chunk {len(chunks)}: pages {start + 1}–{end} ({page_count} pages) → {tmp.name}")

    return chunks


def distribute_questions(total_questions: int, chunk_pages: list[int]) -> list[int]:
    """
    Distribute questions proportionally across chunks by page count.

    Uses floor allocation + largest-remainder rounding so the output
    always sums exactly to total_questions.

    Args:
        total_questions: Total number of questions to distribute.
        chunk_pages:     Page count for each chunk, e.g. [10, 10, 10, 5].

    Returns:
        Question count per chunk, e.g. [6, 6, 6, 2] for 20 questions.

    Example (35-page PDF, 20 questions):
        chunk_pages  = [10, 10, 10, 5]
        raw          = [5.71, 5.71, 5.71, 2.86]
        floor        = [5, 5, 5, 2]   sum = 17
        deficit      = 3
        final        = [6, 6, 6, 2]   sum = 20 ✓
    """
    total_pages = sum(chunk_pages)
    if total_pages == 0:
        return [0] * len(chunk_pages)

    raw = [(total_questions * pages) / total_pages for pages in chunk_pages]
    distribution = [math.floor(r) for r in raw]
    remainders = [r - math.floor(r) for r in raw]

    deficit = total_questions - sum(distribution)
    if deficit > 0:
        top_indices = sorted(range(len(remainders)), key=lambda i: remainders[i], reverse=True)
        for i in range(deficit):
            distribution[top_indices[i]] += 1

    return distribution


def upload_chunk(client: OpenAI, file_path: str) -> str:
    """
    Upload a PDF file to the OpenAI Files API for use in a Responses API call.

    Args:
        client:    Initialized OpenAI client.
        file_path: Local path to the PDF (whole file or a chunk temp file).

    Returns:
        The OpenAI file_id string, e.g. "file-abc123".
    """
    with open(file_path, "rb") as f:
        uploaded = client.files.create(file=f, purpose="user_data")
    logger.info(f"Uploaded PDF chunk: {os.path.basename(file_path)} → file_id={uploaded.id}")
    return uploaded.id


def build_file_content(file_id: str) -> dict:
    """Return the input_file content block for an uploaded file_id."""
    return {"type": "input_file", "file_id": file_id}


def cleanup_chunks(chunks: list[tuple[str, int]]) -> None:
    """Delete temporary chunk files created by split_into_chunks()."""
    for path, _ in chunks:
        try:
            os.unlink(path)
            logger.debug(f"Deleted chunk temp file: {path}")
        except OSError as e:
            logger.warning(f"Could not delete chunk temp file {path}: {e}")
