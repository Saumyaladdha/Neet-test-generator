"""
NEET Test Generator - OpenAI API Wrapper
Generates exam questions from textbook images using gpt-5-mini
"""

import base64
import io
import json
import logging
import math
from pathlib import Path
from typing import Literal, Optional, Union
from openai import OpenAI
from PIL import Image
from prompts_config import get_prompt, PROMPTS_CONFIG

logger = logging.getLogger(__name__)


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
    model: str = "gpt-5-mini",
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
        clean_text = result_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        result = json.loads(clean_text)
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
    model: str = "gpt-5-mini",
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
        clean_text = result_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        result = json.loads(clean_text)
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
        clean_text = result_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        return json.loads(clean_text)
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
    model: str = "gpt-5-mini",
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
    model: str = "gpt-5-mini",
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
    model: str = "gpt-5-mini",
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