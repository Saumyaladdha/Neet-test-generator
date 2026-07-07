"""
NEET Test Generator - OpenAI API Wrapper
Generates exam questions from textbook images using gpt-5.4-mini
"""

import base64
import io
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Literal, Optional, Union
from openai import OpenAI
from PIL import Image
from prompts.selector import get_prompt

logger = logging.getLogger(__name__)

_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_DOLLAR_MATH_RE = re.compile(r"\$[^$]*\$")


def _hindi_purity_failures(questions: list, min_devanagari_ratio: float = 0.3) -> list:
    """
    Return indices of questions whose text is not predominantly Devanagari.

    Content inside $...$ (chemical formulas, LaTeX) is excluded before counting,
    since those are expected to stay in Roman script per NCERT Hindi convention.
    A question with no alphabetic content at all (e.g. a pure numeral sequence)
    is skipped rather than flagged.
    """
    failures = []
    for i, q in enumerate(questions):
        text = q.get("question_text", "")
        opts = q.get("options", {})
        combined = text + " " + " ".join(str(v) for v in (opts.values() if isinstance(opts, dict) else opts))
        combined = _DOLLAR_MATH_RE.sub("", combined)
        devanagari_count = len(_DEVANAGARI_RE.findall(combined))
        latin_count = len(_LATIN_RE.findall(combined))
        total = devanagari_count + latin_count
        if total == 0:
            continue
        if (devanagari_count / total) < min_devanagari_ratio:
            failures.append(i)
    return failures


def _assert_hindi_purity(questions: list, batch_label: str, attempt: int) -> None:
    """
    Hindi-only guard: raise if too many questions in this batch are not
    predominantly Devanagari. Raising here (instead of branching the shared
    retry loop) lets the existing except-Exception handler in generate_chunk
    retry/back off exactly like it does for any other batch failure — English
    and other-language calls never execute this function at all.
    """
    if not questions:
        return
    bad_idxs = _hindi_purity_failures(questions)
    if len(bad_idxs) > len(questions) // 2:
        _log.warning("batch.language_check_failed",
                      batch=batch_label,
                      attempt=attempt + 1,
                      failed=len(bad_idxs),
                      total=len(questions))
        raise ValueError(
            f"medium=hindi requested but {len(bad_idxs)}/{len(questions)} "
            f"questions were not predominantly Devanagari"
        )

# Structured logger for the worker-oriented generate_chunk path
from core.logger import get_logger as _get_logger
from core.parser import strip_markdown_fences
_log = _get_logger(__name__)


def calculate_image_tokens(image_bytes: bytes, detail: str = "high") -> dict:
    """
    Calculate token count for an image based on OpenAI's formula.

    For low detail: 85 tokens (fixed)
    For high detail:
        1. Scale so longest side <= 2048px
        2. Scale so shortest side <= 768px
        3. Count 512x512 tiles
        4. Tokens = (tiles × 170) + 85
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size

        if detail == "low":
            return {
                "width": width,
                "height": height,
                "detail": detail,
                "tokens": 85
            }

        # High detail calculation
        # Step 1: Scale to fit within 2048x2048
        max_dim = max(width, height)
        if max_dim > 2048:
            scale = 2048 / max_dim
            width = int(width * scale)
            height = int(height * scale)

        # Step 2: Scale so shortest side is 768px
        min_dim = min(width, height)
        if min_dim > 768:
            scale = 768 / min_dim
            width = int(width * scale)
            height = int(height * scale)

        # Step 3: Count 512x512 tiles
        tiles_x = math.ceil(width / 512)
        tiles_y = math.ceil(height / 512)
        total_tiles = tiles_x * tiles_y

        # Step 4: Calculate tokens
        tokens = (total_tiles * 170) + 85

        return {
            "original_size": img.size,
            "scaled_size": (width, height),
            "tiles": total_tiles,
            "detail": detail,
            "tokens": tokens
        }
    except Exception as e:
        logger.warning(f"Could not calculate image tokens: {e}")
        return {"error": str(e), "tokens": "unknown"}





def encode_image_to_base64(image_path: Union[str, Path]) -> str:
    """
    Encode an image file to base64 string.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Base64 encoded string of the image
    """
    image_path = Path(image_path)
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def get_image_media_type(image_path: Union[str, Path]) -> str:
    """
    Determine the media type based on file extension.
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Media type string (e.g., 'image/png', 'image/jpeg')
    """
    extension = Path(image_path).suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return media_types.get(extension, "image/png")


def generate_neet_test(
    image_source: Union[str, Path, bytes],
    subject: str = "biology",
    difficulty: Literal["easy", "medium", "hard"] = "hard",
    question_count: int = 5,
    question_type: str = "mcq",
    model: str = "gpt-5.4-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions from a textbook image.

    Args:
        image_source: Path to the image file, or base64 encoded image bytes
        subject: Subject for the test (e.g., 'biology', 'physics', 'chemistry')
        difficulty: Difficulty level - 'easy', 'medium', or 'hard'
        question_count: Number of questions to generate
        question_type: Type of questions (mcq, assertion_reason, match_the_column)
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        api_key: Optional API key (uses OPENAI_API_KEY env var if not provided)

    Returns:
        Dictionary containing the generated test questions

    Raises:
        FileNotFoundError: If image_source is a path that doesn't exist
        ValueError: If image_source format is invalid
    """
    # Initialize client
    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    # Process image source
    if isinstance(image_source, (str, Path)):
        image_path = Path(image_source)
        if image_path.exists():
            base64_image = encode_image_to_base64(image_path)
            media_type = get_image_media_type(image_path)
        else:
            base64_image = str(image_source)
            media_type = "image/png"
    elif isinstance(image_source, bytes):
        base64_image = base64.b64encode(image_source).decode("utf-8")
        media_type = "image/png"
    else:
        raise ValueError("image_source must be a file path, base64 string, or bytes")

    # Get prompt from prompts_config
    formatted_prompt = get_prompt(question_type, difficulty, subject, question_count)

    # Build the request
    messages = [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": formatted_prompt}]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": f"data:{media_type};base64,{base64_image}"
                }
            ]
        }
    ]

    request_params = {
        "model": model,
        "input": messages,
        "max_output_tokens": max_tokens,
        "store": True,
        "text": {"format": {"type": "text"}},
        "reasoning": {},
    }

    response = client.responses.create(**request_params)

    if hasattr(response, 'usage') and response.usage:
        logger.info(f"Token usage - Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens}, Total: {response.usage.total_tokens}")

    result_text = ""
    for item in response.output:
        if hasattr(item, 'type'):
            if item.type == 'message' and hasattr(item, 'content'):
                for content_block in item.content:
                    if hasattr(content_block, 'text'):
                        result_text += content_block.text
            elif item.type == 'text' and hasattr(item, 'text'):
                result_text += item.text

    try:
        result = json.loads(strip_markdown_fences(result_text))
    except json.JSONDecodeError:
        result = {
            "raw_response": result_text,
            "parse_error": "Failed to parse response as JSON"
        }

    return result


def generate_neet_test_from_url(
    image_url: str,
    subject: str = "biology",
    difficulty: Literal["easy", "medium", "hard"] = "hard",
    question_count: int = 5,
    question_type: str = "mcq",
    model: str = "gpt-5.4-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions from a textbook image URL.

    Args:
        image_url: URL of the image
        subject: Subject for the test (e.g., 'biology', 'physics', 'chemistry')
        difficulty: Difficulty level - 'easy', 'medium', or 'hard'
        question_count: Number of questions to generate
        question_type: Type of questions (mcq, assertion_reason, match_the_column)
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        api_key: Optional API key (uses OPENAI_API_KEY env var if not provided)

    Returns:
        Dictionary containing the generated test questions
    """
    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    formatted_prompt = get_prompt(question_type, difficulty, subject, question_count)

    messages = [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": formatted_prompt}]
        },
        {
            "role": "user",
            "content": [{"type": "input_image", "image_url": image_url}]
        }
    ]

    request_params = {
        "model": model,
        "input": messages,
        "max_output_tokens": max_tokens,
        "store": True,
        "text": {"format": {"type": "text"}},
        "reasoning": {},
    }

    response = client.responses.create(**request_params)

    if hasattr(response, 'usage') and response.usage:
        logger.info(f"Token usage - Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens}, Total: {response.usage.total_tokens}")

    result_text = ""
    for item in response.output:
        if hasattr(item, 'type'):
            if item.type == 'message' and hasattr(item, 'content'):
                for content_block in item.content:
                    if hasattr(content_block, 'text'):
                        result_text += content_block.text
            elif item.type == 'text' and hasattr(item, 'text'):
                result_text += item.text

    try:
        result = json.loads(strip_markdown_fences(result_text))
    except json.JSONDecodeError:
        result = {
            "raw_response": result_text,
            "parse_error": "Failed to parse response as JSON"
        }

    return result


def _make_single_batch_request(
    client,
    image_content,
    model: str,
    temperature: float,
    max_tokens: int,
    previous_questions: list = None,
    question_type: str = "mcq",
    difficulty: str = "medium",
    subject: str = "biology",
    question_count: int = 3,
) -> dict:
    """
    Internal helper to make a single batch API request.

    Args:
        client: OpenAI client instance
        image_content: Dict or list of dicts with image data (type and url/base64)
        model: Model to use
        temperature: Temperature setting
        max_tokens: Max tokens
        previous_questions: List of previously generated question texts to avoid
        question_type: Type of questions (mcq, assertion_reason, match_the_column)
        difficulty: Difficulty level
        subject: Subject for prompts
        question_count: Number of questions for this batch

    Returns:
        Parsed result dictionary
    """
    # Get the prompt from PROMPTS_CONFIG for the given question_type + difficulty
    # For combination mode, default to mcq prompt for the given difficulty
    effective_type = question_type if question_type != "combination" else "mcq"
    prompt_key = (effective_type, difficulty)

    if prompt_key in PROMPTS_CONFIG:
        formatted_prompt = get_prompt(effective_type, difficulty, subject, question_count)
        logger.info(f"[PROMPT] Using PROMPTS_CONFIG prompt for ({effective_type}, {difficulty})")
    else:
        raise ValueError(f"No prompt configured for ({effective_type}, {difficulty})")

    # Build user content -- support single image or multiple images
    if isinstance(image_content, list):
        user_content = list(image_content)
    else:
        user_content = [image_content]

    # Add previous questions if provided (to avoid repetition)
    if previous_questions:
        avoid_text = "IMPORTANT: The following questions have already been generated. DO NOT repeat or create similar questions:\n\n"
        for i, q in enumerate(previous_questions, 1):
            avoid_text += f"{i}. {q}\n"
        avoid_text += "\nGenerate completely NEW and DIFFERENT questions from the image content."

        user_content.append({
            "type": "input_text",
            "text": avoid_text
        })
        logger.info(f"[AVOID REPETITION] Sending {len(previous_questions)} previous question(s) to avoid:")
        for i, q in enumerate(previous_questions, 1):
            logger.info(f"  Previous Q{i}: {q[:100]}...")
    else:
        logger.info(f"[AVOID REPETITION] No previous questions (first batch or single batch)")

    messages = [
        {
            "role": "system",
            "content": [{"type": "input_text", "text": formatted_prompt}]
        },
        {
            "role": "user",
            "content": user_content
        }
    ]

    # === PRINT THE FINAL PROMPT GOING TO LLM ===
    logger.info("=" * 80)
    logger.info("[FINAL PROMPT TO LLM] System prompt (first 500 chars):")
    logger.info(formatted_prompt)
    logger.info(f"[FINAL PROMPT TO LLM] User content has {len(user_content)} item(s):")
    for i, item in enumerate(user_content):
        if item.get("type") == "input_image":
            img_url = item.get("image_url", "")
            if img_url.startswith("data:"):
                logger.info(f"  Item {i+1}: [IMAGE] base64 data ({len(img_url)} chars)")
            else:
                logger.info(f"  Item {i+1}: [IMAGE] URL: {img_url[:80]}...")
        elif item.get("type") == "input_text":
            logger.info(f"  Item {i+1}: [TEXT] (full content below)")
            logger.info(item.get('text', ''))
    logger.info("=" * 80)

    request_params = {
        "model": model,
        "input": messages,
        "max_output_tokens": max_tokens,
        "store": True,
        "text": {"format": {"type": "text"}},
        "reasoning": {},
    }

    # PROMPTS_CONFIG prompts define their own JSON output schema — no tools needed
    logger.info("[TOOLS] No tools sent (using PROMPTS_CONFIG prompt with built-in JSON schema)")

    logger.info(f"[API CALL] Sending request to model={model}, max_output_tokens={max_tokens}")
    response = client.responses.create(**request_params)

    # Log token usage
    if hasattr(response, 'usage') and response.usage:
        logger.info(f"Token usage - Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens}, Total: {response.usage.total_tokens}")

    # Extract response text
    result_text = ""
    for item in response.output:
        if hasattr(item, 'type'):
            if item.type == 'message' and hasattr(item, 'content'):
                for content_block in item.content:
                    if hasattr(content_block, 'text'):
                        result_text += content_block.text
            elif item.type == 'text' and hasattr(item, 'text'):
                result_text += item.text

    # Parse JSON from text response
    try:
        return json.loads(strip_markdown_fences(result_text))
    except json.JSONDecodeError:
        return {
            "raw_response": result_text,
            "parse_error": "Failed to parse response as JSON"
        }


def generate_neet_test_batched(
    image_source: Union[str, Path, bytes],
    subject: str = "biology",
    difficulty: Literal["easy", "medium", "hard"] = "hard",
    question_count: int = 5,
    question_type: Literal["mcq", "assertion_reason", "match_the_column"] = "mcq",
    batch_size: int = 3,
    model: str = "gpt-5.4-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions in batches to avoid repetition.

    For question_count=5 and batch_size=3:
    - Batch 1: Generate 3 questions
    - Batch 2: Generate 2 questions (with batch 1 questions sent to avoid repetition)

    Args:
        image_source: Path to the image file, or base64 encoded image bytes
        subject: Subject for the test
        difficulty: Difficulty level - 'easy', 'medium', or 'hard'
        question_count: Total number of questions to generate
        question_type: Type of questions - 'mcq', 'assertion_reason', or 'match_the_column'
        batch_size: Number of questions per batch (default 3)
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        api_key: Optional API key

    Returns:
        Combined dictionary with all generated questions
    """
    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    # Process image source
    if isinstance(image_source, (str, Path)):
        image_path = Path(image_source)
        if image_path.exists():
            base64_image = encode_image_to_base64(image_path)
            media_type = get_image_media_type(image_path)
        else:
            base64_image = str(image_source)
            media_type = "image/png"
    elif isinstance(image_source, bytes):
        base64_image = base64.b64encode(image_source).decode("utf-8")
        media_type = "image/png"
    else:
        raise ValueError("image_source must be a file path, base64 string, or bytes")

    image_content = {
        "type": "input_image",
        "image_url": f"data:{media_type};base64,{base64_image}"
    }

    # Calculate and log image token count
    if isinstance(image_source, bytes):
        image_token_info = calculate_image_tokens(image_source)
    elif isinstance(image_source, (str, Path)) and Path(image_source).exists():
        with open(image_source, 'rb') as f:
            image_token_info = calculate_image_tokens(f.read())
    else:
        image_token_info = {"tokens": "unknown (base64 string input)"}

    logger.info(f"Image token estimate: {image_token_info}")

    # Calculate batches
    batches = []
    remaining = question_count
    while remaining > 0:
        batch_count = min(batch_size, remaining)
        batches.append(batch_count)
        remaining -= batch_count

    logger.info("=" * 80)
    logger.info(f"[BATCH PLAN] Total questions requested: {question_count}, batch_size: {batch_size}")
    logger.info(f"[BATCH PLAN] Will run {len(batches)} batch(es): {batches}")
    logger.info(f"[BATCH PLAN] Settings: subject={subject}, difficulty={difficulty}, question_type={question_type}")
    logger.info("=" * 80)

    # Collect all questions
    all_questions = []
    previous_question_texts = []
    combined_result = None

    for batch_idx, batch_count in enumerate(batches):
        logger.info("")
        logger.info("#" * 80)
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Starting — generating {batch_count} question(s)")
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Questions generated so far: {len(all_questions)}")
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Previous questions to avoid: {len(previous_question_texts)}")
        logger.info("#" * 80)

        # Make request
        batch_result = _make_single_batch_request(
            client=client,
            image_content=image_content,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            previous_questions=previous_question_texts if batch_idx > 0 else None,
            question_type=question_type,
            difficulty=difficulty,
            subject=subject,
            question_count=batch_count
        )

        # Handle parse errors
        if "parse_error" in batch_result:
            logger.error(f"[BATCH {batch_idx + 1}/{len(batches)}] PARSE ERROR: {batch_result.get('parse_error')}")
            logger.error(f"[BATCH {batch_idx + 1}/{len(batches)}] Raw response: {batch_result.get('raw_response', '')[:300]}...")
            if combined_result is None:
                return batch_result
            else:
                # Return what we have so far
                break

        # Extract questions from this batch
        batch_question_count = 0
        if "questions" in batch_result:
            for q in batch_result["questions"]:
                # Renumber question IDs
                q["question_id"] = len(all_questions) + 1
                all_questions.append(q)
                batch_question_count += 1

                # Add question text to avoid list
                if "question_text" in q:
                    previous_question_texts.append(q["question_text"])
                    logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Got Q{q['question_id']} ({q.get('question_type', 'unknown')}): {q['question_text'][:100]}...")

        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Completed — got {batch_question_count} question(s), total so far: {len(all_questions)}")

        # Store first batch result as base for combined result
        if combined_result is None:
            combined_result = batch_result
        else:
            # Merge source_content_summary if present
            if "source_content_summary" in batch_result and "source_content_summary" in combined_result:
                # Merge key_terms_found
                existing_terms = set(combined_result["source_content_summary"].get("key_terms_found", []))
                new_terms = batch_result["source_content_summary"].get("key_terms_found", [])
                combined_result["source_content_summary"]["key_terms_found"] = list(existing_terms | set(new_terms))

            # Merge unused_content
            if "unused_content" in batch_result:
                existing_unused = set(combined_result.get("unused_content", []))
                new_unused = batch_result.get("unused_content", [])
                combined_result["unused_content"] = list(existing_unused & set(new_unused))

    logger.info("=" * 80)
    logger.info(f"[DONE] All batches complete. Total questions generated: {len(all_questions)}")
    logger.info("=" * 80)

    # Update combined result with all questions
    if combined_result:
        combined_result["questions"] = all_questions

        # Update metadata
        if "test_metadata" in combined_result:
            combined_result["test_metadata"]["total_questions"] = len(all_questions)
            combined_result["test_metadata"]["requested_questions"] = question_count
            combined_result["test_metadata"]["question_type"] = question_type
            combined_result["test_metadata"]["batch_info"] = {
                "batch_size": batch_size,
                "batches_used": len(batches),
                "questions_per_batch": batches
            }

    return combined_result


def generate_neet_test_multi_image_batched(
    image_contents: list,
    subject: str = "biology",
    difficulty: Literal["easy", "medium", "hard"] = "hard",
    question_count: int = 5,
    question_type: str = "mcq",
    batch_size: int = 3,
    model: str = "gpt-5.4-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions from MULTIPLE images in batches.

    Sends ALL images to the LLM in every batch so the model can see
    the full content across all pages/images.

    Args:
        image_contents: List of image content dicts (each with type and image_url)
        subject: Subject for the test
        difficulty: Difficulty level
        question_count: Total number of questions to generate
        question_type: Type of questions
        batch_size: Number of questions per batch
        model: OpenAI model to use
        temperature: Sampling temperature
        max_tokens: Maximum tokens in response
        api_key: Optional API key

    Returns:
        Combined dictionary with all generated questions
    """
    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    logger.info(f"[MULTI-IMAGE] Received {len(image_contents)} image(s)")

    # Calculate batches
    batches = []
    remaining = question_count
    while remaining > 0:
        batch_count = min(batch_size, remaining)
        batches.append(batch_count)
        remaining -= batch_count

    logger.info("=" * 80)
    logger.info(f"[BATCH PLAN] Total questions requested: {question_count}, batch_size: {batch_size}")
    logger.info(f"[BATCH PLAN] Will run {len(batches)} batch(es): {batches}")
    logger.info(f"[BATCH PLAN] Images: {len(image_contents)}, Settings: subject={subject}, difficulty={difficulty}, question_type={question_type}")
    logger.info("=" * 80)

    all_questions = []
    previous_question_texts = []
    combined_result = None

    for batch_idx, batch_count in enumerate(batches):
        logger.info("")
        logger.info("#" * 80)
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Starting — generating {batch_count} question(s)")
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Questions generated so far: {len(all_questions)}")
        logger.info("#" * 80)

        batch_result = _make_single_batch_request(
            client=client,
            image_content=image_contents,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            previous_questions=previous_question_texts if previous_question_texts else None,
            question_type=question_type,
            difficulty=difficulty,
            subject=subject,
            question_count=batch_count,
        )

        if "parse_error" in batch_result:
            logger.error(f"[BATCH {batch_idx + 1}] Parse error: {batch_result.get('parse_error')}")
            if combined_result is None:
                combined_result = batch_result
            continue

        batch_questions = batch_result.get("questions", [])
        logger.info(f"[BATCH {batch_idx + 1}] Generated {len(batch_questions)} question(s)")

        for q in batch_questions:
            q_text = q.get("question_text", "")
            previous_question_texts.append(q_text)

        all_questions.extend(batch_questions)

        if combined_result is None:
            combined_result = batch_result
        else:
            combined_result["questions"] = all_questions

    if combined_result and "questions" in combined_result:
        for i, q in enumerate(combined_result["questions"], 1):
            q["question_id"] = i

        if "test_metadata" in combined_result:
            combined_result["test_metadata"]["total_questions"] = len(all_questions)
            combined_result["test_metadata"]["requested_questions"] = question_count
            combined_result["test_metadata"]["question_type"] = question_type
            combined_result["test_metadata"]["batch_info"] = {
                "batch_size": batch_size,
                "batches_used": len(batches),
                "questions_per_batch": batches,
                "images_used": len(image_contents)
            }

    return combined_result


def generate_neet_test_from_url_batched(
    image_url: str,
    subject: str = "biology",
    difficulty: Literal["easy", "medium", "hard"] = "hard",
    question_count: int = 5,
    question_type: Literal["mcq", "assertion_reason", "match_the_column"] = "mcq",
    batch_size: int = 3,
    model: str = "gpt-5.4-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions from URL in batches to avoid repetition.

    Args:
        image_url: URL of the image
        subject: Subject for the test
        difficulty: Difficulty level - 'easy', 'medium', or 'hard'
        question_count: Total number of questions to generate
        question_type: Type of questions - 'mcq', 'assertion_reason', or 'match_the_column'
        batch_size: Number of questions per batch (default 3)
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        api_key: Optional API key

    Returns:
        Combined dictionary with all generated questions
    """
    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    image_content = {
        "type": "input_image",
        "image_url": image_url
    }

    # For URL images, we can't calculate tokens locally without downloading
    # The actual token count will be shown in the API response
    logger.info(f"Using image URL: {image_url[:80]}... (token count will be in API response)")

    # Calculate batches
    batches = []
    remaining = question_count
    while remaining > 0:
        batch_count = min(batch_size, remaining)
        batches.append(batch_count)
        remaining -= batch_count

    logger.info("=" * 80)
    logger.info(f"[BATCH PLAN - URL] Total questions requested: {question_count}, batch_size: {batch_size}")
    logger.info(f"[BATCH PLAN - URL] Will run {len(batches)} batch(es): {batches}")
    logger.info(f"[BATCH PLAN - URL] Settings: subject={subject}, difficulty={difficulty}, question_type={question_type}")
    logger.info("=" * 80)

    # Collect all questions
    all_questions = []
    previous_question_texts = []
    combined_result = None

    for batch_idx, batch_count in enumerate(batches):
        logger.info("")
        logger.info("#" * 80)
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Starting — generating {batch_count} question(s)")
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Questions generated so far: {len(all_questions)}")
        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Previous questions to avoid: {len(previous_question_texts)}")
        logger.info("#" * 80)

        batch_result = _make_single_batch_request(
            client=client,
            image_content=image_content,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            previous_questions=previous_question_texts if batch_idx > 0 else None,
            question_type=question_type,
            difficulty=difficulty,
            subject=subject,
            question_count=batch_count
        )

        if "parse_error" in batch_result:
            logger.error(f"[BATCH {batch_idx + 1}/{len(batches)}] PARSE ERROR: {batch_result.get('parse_error')}")
            logger.error(f"[BATCH {batch_idx + 1}/{len(batches)}] Raw response: {batch_result.get('raw_response', '')[:300]}...")
            if combined_result is None:
                return batch_result
            else:
                break

        batch_question_count = 0
        if "questions" in batch_result:
            for q in batch_result["questions"]:
                q["question_id"] = len(all_questions) + 1
                all_questions.append(q)
                batch_question_count += 1
                if "question_text" in q:
                    previous_question_texts.append(q["question_text"])
                    logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Got Q{q['question_id']} ({q.get('question_type', 'unknown')}): {q['question_text'][:100]}...")

        logger.info(f"[BATCH {batch_idx + 1}/{len(batches)}] Completed — got {batch_question_count} question(s), total so far: {len(all_questions)}")

        if combined_result is None:
            combined_result = batch_result
        else:
            if "source_content_summary" in batch_result and "source_content_summary" in combined_result:
                existing_terms = set(combined_result["source_content_summary"].get("key_terms_found", []))
                new_terms = batch_result["source_content_summary"].get("key_terms_found", [])
                combined_result["source_content_summary"]["key_terms_found"] = list(existing_terms | set(new_terms))

            if "unused_content" in batch_result:
                existing_unused = set(combined_result.get("unused_content", []))
                new_unused = batch_result.get("unused_content", [])
                combined_result["unused_content"] = list(existing_unused & set(new_unused))

    logger.info("=" * 80)
    logger.info(f"[DONE] All batches complete. Total questions generated: {len(all_questions)}")
    logger.info("=" * 80)

    if combined_result:
        combined_result["questions"] = all_questions
        if "test_metadata" in combined_result:
            combined_result["test_metadata"]["total_questions"] = len(all_questions)
            combined_result["test_metadata"]["requested_questions"] = question_count
            combined_result["test_metadata"]["question_type"] = question_type
            combined_result["test_metadata"]["batch_info"] = {
                "batch_size": batch_size,
                "batches_used": len(batches),
                "questions_per_batch": batches
            }

    return combined_result


# ── Worker-oriented chunk generator ───────────────────────────────────────────

_LANG_CODE = {"english": "en", "hindi": "hi"}


def generate_chunk(
    *,
    file_type: str,
    presigned_url: str = None,
    openai_file_id: str = None,
    subject: str,
    medium: str = "english",
    question_type: str,
    difficulty: str,
    question_count: int,
    previous_questions: list = None,
    batch_size: int = None,
    model: str = None,
    api_key: str = None,
    user_id: str = None,
    test_id: str = None,
    batch_id: str = None,
) -> list:
    """
    Generate questions from a single chunk (image or PDF chunk).

    For images:  pass presigned_url (S3 HTTPS URL accepted by OpenAI).
    For PDFs:    pass openai_file_id (from OpenAI Files API upload).

    Returns list of question dicts. Returns partial list on batch failure.
    """
    from core.config import OPENAI_MODEL, BATCH_SIZE, BATCH_MAX_RETRIES, BATCH_RETRY_DELAYS

    if batch_size is None:
        batch_size = BATCH_SIZE
    if model is None:
        model = OPENAI_MODEL

    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    lang = _LANG_CODE.get(medium.lower(), "en")

    # Accept both "pdf" and "application/pdf" from worker
    is_pdf = file_type in ("pdf", "application/pdf")
    if is_pdf:
        if not openai_file_id:
            raise ValueError("openai_file_id required for PDF chunks")
        media_item = {"type": "input_file", "file_id": openai_file_id}
        file_hint = f"file_id={openai_file_id[:12]}..."
    else:
        if not presigned_url:
            raise ValueError("presigned_url required for image chunks")
        media_item = {"type": "input_image", "image_url": presigned_url}
        file_hint = "presigned_url"

    # Divide into batches
    question_count = int(question_count)  # guard against DynamoDB Decimal
    batch_counts = []
    remaining = question_count
    while remaining > 0:
        batch_counts.append(min(batch_size, remaining))
        remaining -= batch_counts[-1]

    chunk_start = time.time()
    _log.info("chunk.start",
              user_id=user_id, test_id=test_id, batch_id=batch_id,
              file_type=file_type,
              subject=subject,
              medium=medium,
              question_type=question_type,
              difficulty=difficulty,
              question_count=question_count,
              # This chunk's questions are split into N LLM calls ("sub-batches"),
              # each capped at BATCH_SIZE questions — distinct from batch_id, which
              # identifies this whole chunk within the job.
              sub_batch_count=len(batch_counts),
              sub_batch_sizes=batch_counts,
              model=model,
              is_pdf=is_pdf,
              file=file_hint,
              prev_questions=len(previous_questions or []))

    all_questions = []
    prev_texts = list(previous_questions or [])

    for batch_idx, batch_count in enumerate(batch_counts):
        batch_label = f"{batch_idx + 1}/{len(batch_counts)}"
        batch_start = time.time()

        prompt = get_prompt(question_type, difficulty, subject, batch_count, language=lang)

        user_content = [media_item]
        if prev_texts:
            avoid_text = (
                "IMPORTANT: Do NOT repeat or create similar questions to these:\n\n"
                + "\n".join(f"{i+1}. {q}" for i, q in enumerate(prev_texts))
                + "\n\nGenerate completely NEW and DIFFERENT questions."
            )
            user_content.append({"type": "input_text", "text": avoid_text})

        messages = [
            {"role": "system", "content": [{"type": "input_text", "text": prompt}]},
            {"role": "user", "content": user_content},
        ]

        _log.info("batch.start",
                  user_id=user_id, test_id=test_id, batch_id=batch_id,
                  sub_batch=batch_label,
                  sub_batch_question_count=batch_count,
                  avoid_repeat_of_n_questions=len(prev_texts),
                  prompt_length_chars=len(prompt))

        batch_result = None
        last_error = None

        for attempt in range(BATCH_MAX_RETRIES):
            api_start = time.time()
            try:
                response = client.responses.create(
                    model=model,
                    input=messages,
                    max_output_tokens=10000,
                    store=True,
                    text={"format": {"type": "text"}},
                    reasoning={},
                )
                latency = round(time.time() - api_start, 2)

                # Log token usage from response
                if hasattr(response, "usage") and response.usage:
                    _log.info("batch.api_ok",
                              user_id=user_id, test_id=test_id, batch_id=batch_id,
                              sub_batch=batch_label,
                              attempt=attempt + 1,
                              latency_seconds=latency,
                              tokens_in=response.usage.input_tokens,
                              tokens_out=response.usage.output_tokens,
                              tokens_total=response.usage.total_tokens)
                else:
                    _log.info("batch.api_ok",
                              user_id=user_id, test_id=test_id, batch_id=batch_id,
                              sub_batch=batch_label,
                              attempt=attempt + 1,
                              latency_seconds=latency,
                              tokens="unavailable")

                # Extract response text
                result_text = ""
                for item in response.output:
                    if getattr(item, "type", None) == "message":
                        for block in getattr(item, "content", []):
                            if hasattr(block, "text"):
                                result_text += block.text
                    elif getattr(item, "type", None) == "text":
                        result_text += getattr(item, "text", "")

                batch_result = json.loads(strip_markdown_fences(result_text))

                questions_found = len(batch_result.get("questions", []))
                _log.info("batch.parse_ok",
                          user_id=user_id, test_id=test_id, batch_id=batch_id,
                          sub_batch=batch_label,
                          questions_found=questions_found,
                          raw_response_length_chars=len(result_text))

                if lang == "hi":
                    _assert_hindi_purity(batch_result["questions"], batch_label, attempt)

                break

            except json.JSONDecodeError as exc:
                latency = round(time.time() - api_start, 2)
                last_error = exc
                raw_preview = result_text[:300] if "result_text" in dir() else ""
                _log.warning("batch.parse_error",
                             user_id=user_id, test_id=test_id, batch_id=batch_id,
                             sub_batch=batch_label,
                             attempt=attempt + 1,
                             max_attempts=BATCH_MAX_RETRIES,
                             latency_seconds=latency,
                             error=f"Model response was not valid JSON: {exc}",
                             raw_response_preview=raw_preview)

            except Exception as exc:
                latency = round(time.time() - api_start, 2)
                last_error = exc
                _log.warning("batch.api_error",
                             user_id=user_id, test_id=test_id, batch_id=batch_id,
                             sub_batch=batch_label,
                             attempt=attempt + 1,
                             max_attempts=BATCH_MAX_RETRIES,
                             latency_seconds=latency,
                             error=str(exc))

            # Retry backoff
            if attempt < BATCH_MAX_RETRIES - 1:
                delay = BATCH_RETRY_DELAYS[attempt] if attempt < len(BATCH_RETRY_DELAYS) else 4
                _log.warning("batch.retry",
                             user_id=user_id, test_id=test_id, batch_id=batch_id,
                             sub_batch=batch_label,
                             attempt_that_failed=attempt + 1,
                             next_attempt=attempt + 2,
                             wait_seconds_before_retry=delay,
                             error=str(last_error))
                time.sleep(delay)

        if batch_result is None:
            _log.error("batch.exhausted",
                       user_id=user_id, test_id=test_id, batch_id=batch_id,
                       sub_batch=batch_label,
                       reason=f"All {BATCH_MAX_RETRIES} attempts failed",
                       last_error=str(last_error),
                       questions_generated_before_giving_up=len(all_questions))
            break

        batch_questions = batch_result.get("questions", [])
        for q in batch_questions:
            prev_texts.append(q.get("question_text", ""))
        all_questions.extend(batch_questions)

        _log.info("batch.done",
                  user_id=user_id, test_id=test_id, batch_id=batch_id,
                  sub_batch=batch_label,
                  questions_generated=len(batch_questions),
                  total_questions_so_far=len(all_questions),
                  elapsed_seconds=round(time.time() - batch_start, 2))

    elapsed = round(time.time() - chunk_start, 2)
    _log.info("chunk.done",
              user_id=user_id, test_id=test_id, batch_id=batch_id,
              total_questions_generated=len(all_questions),
              questions_requested=question_count,
              elapsed_seconds=elapsed,
              subject=subject,
              question_type=question_type,
              difficulty=difficulty)

    return all_questions


# Example usage
if __name__ == "__main__":
    # Example 1: Generate from a local file
    # result = generate_neet_test(
    #     image_source="textbook_page.png",
    #     subject="biology",
    #     difficulty="hard",
    #     question_count=5
    # )
    # print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Example 2: Generate from a URL
    # result = generate_neet_test_from_url(
    #     image_url="https://example.com/textbook_image.png",
    #     subject="physics",
    #     difficulty="medium",
    #     question_count=10
    # )
    # print(json.dumps(result, indent=2, ensure_ascii=False))
    
    print("NEET Test Generator module loaded successfully.")
    print("Use generate_neet_test() for local images or generate_neet_test_from_url() for URLs.")