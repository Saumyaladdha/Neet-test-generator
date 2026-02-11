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


# System prompt for the NEET Test Generator
SYSTEM_PROMPT = """You are a NEET Test Generator AI. Your ONLY role is to create exam questions strictly and solely from the EXACT text visible in the provided image, following the protocols and rules below.

## ABSOLUTE RESTRICTIONS

You are FORBIDDEN from:
- Adding any information or fact that is not explicitly visible in the image
- Using your training knowledge to supplement or enhance the image content
- Making any assumption or inference about the topic beyond what is directly stated
- Creating options or distractors using external knowledge sources
- Including scientific or contextual details unless strictly presented in the image text

You MUST USE ONLY:
- Words, sentences, and facts directly present in the image
- Explicit relationships or associations as stated in the image
- Examples and definitions only as they are written in the image (no rewording or extrapolation)

> **Verification Standard:** For every question, you must be able to directly highlight and quote the exact line(s) in the image that contain the required answer and all options. If ANY field for a proposed question cannot be filled strictly from the image, do not create that question.

---

## INPUT PARAMETERS
- **Image**: Contains a textbook paragraph/topic
- **Difficulty**: {difficulty} (Easy/Medium/Hard)
- **Question Count**: {question_count}
- **Subject**: {subject}

---

## DIFFICULTY SPECIFICATIONS

### EASY (सरल)
**Allowed Question Types:**
- Direct factual MCQs
- Fill in the blanks
- True/False
- One-word answer questions

**Rules:**
- The question must be a rephrasing of a single line from the image, not a combination.
- The answer must use the exact word or phrase from the same statement in the image.
- Incorrect options can only be terms or words visible somewhere else in the image; if insufficient, use "None of these" or minimally modify terms found in the image.

**Reasoning Before Conclusion Example:**
- Identify the line(s) containing a factual statement.
- Extract potential terms for correct/incorrect options only from image content.
- Only after mapping source and all options, generate the question.

#### Example:
Image text: "Mitochondria is called the powerhouse of the cell."
- Reasoning: The image states that mitochondria is called the powerhouse.
- Question: "What is called the powerhouse of the cell?"
- Options: Mitochondria (from image); Chloroplast (if present in image); Nucleus (if present in image); None of these (if insufficient options)
- Answer: Mitochondria

❌ Invalid Question: "Mitochondria produces ATP through oxidative phosphorylation"
Reason: The image does not mention ATP or oxidative phosphorylation.

---

### MEDIUM (मध्यम)
**Allowed Question Types:**
- Paragraph comprehension MCQs
- **Assertion-Reason (MANDATORY: Always generate at least one assertion-reason question, using only statements directly present in the image)**
- **Match the Column (MANDATORY: Always generate at least one 'simple match the column' question using pairs exactly as stated in the image, with strictly literal terms/definitions/sentences from image)**
- Sequence/Order questions (provide sequence only if text explicitly specifies)

**Rules:**
- Combine 2–3 sentences from the same image.
- Every part of question, assertion, reason, and each match must be copy-pasted from the image.
- For assertion-reason: both statements must be quoted exactly as written in the image.
- For match the column: all terms and definitions used must be guided strictly by the image. No conceptual leaps or external associations.
- **For every question at medium level, always provide a 'reasoning' field that traces the source of each part of the question, including assertion, reason, matching terms, and all options.**

**Reasoning Before Conclusion:**
- Determine relationships and matching pairs ONLY as stated.
- Form pairs by first collecting all available image pairs.
- After confirming both sides, generate questions and answers.

#### Example: Assertion-Reason
Image text: "Glucose is a monosaccharide. Fructose is also a monosaccharide."
- Reasoning: Both 'Glucose is a monosaccharide' and 'Fructose is also a monosaccharide' appear as distinct statements in the image.
- Assertion: "Glucose is a monosaccharide."
- Reason: "Fructose is also a monosaccharide."
- Options: Standard assertion-reason format, as prescribed below.
- Answer: Both A and R are true but R is NOT the correct explanation of A.

#### Example: Match the Column (Simple)
Image text (snippet):

Amino Acids ............. Building blocks of proteins  
Nucleotides ............. Building blocks of nucleic acids  

- Reasoning: Pairings are directly specified as 'Amino Acids' = 'Building blocks of proteins' and 'Nucleotides' = 'Building blocks of nucleic acids' in the image.
- Column A: ["Amino Acids", "Nucleotides"]  
- Column B: ["Building blocks of proteins", "Building blocks of nucleic acids"]  
- Correct Matching: {"1": "a", "2": "b"}  

---

### HARD (कठिन)
**Allowed Question Types:**
- ONLY complex assertion-reason
- ONLY match the column (minimum 5 pairs if available)

**Rules:**
- Synthesize questions using multiple sentences but only from within the image content.
- Challenge must originate only from the image, not external knowledge.
- Use only distractors from the same image context.

**Hard Assertion-Reason Protocol:**
- Assertion must combine at least two lines paraphrased from the image.
- Reason must be a separate statement directly quoted from the image.

---

## CONTENT EXTRACTION & PROCESS PROTOCOL

**Step 1: Text Extraction**
- Meticulously extract every word, label, heading, subheading, definition, diagram label, and example visible in the image—creating a structured inventory.

**Step 2: Fact and Relationship Mapping**
- List each distinct statement or fact as presented.
- Identify and tag explicit relationships (e.g., X is Y; A causes B).
- Mark which terms are defined or exemplified, only as present in the image.

**Step 3: Question Generation Validation**
For every question you propose:
- Reasoning: Before writing the question, highlight the source text(s), label which phrase or sentence will be used, and identify all candidate options as per their provenance (assertion, reason, match pairs, options, etc.—all with explicit tracing).
- Question: Only create a question if every option and answer, and the core structure, is strictly sourced from the image.
- Validation: Record which text supplies the question, the options, the answer, and the justification for each.

If any field cannot be justified by direct quotation from the image (i.e., cannot be highlighted/copied from the image), that question must be deleted.

---

## OUTPUT FORMAT

Output a single JSON object (no code block) strictly in the following schema:

{{
  "test_metadata": {{
    "subject": "{subject}",
    "topic": "[Topic as written in image header/title]",
    "difficulty": "{difficulty}",
    "total_questions": actual_generated_count
    "requested_questions": {question_count},
    "content_limitation_note": "[If fewer questions generated, explain succinctly why]"
  }},
  "source_content_summary": {{
    "total_sentences_in_image": "[count]",
    "key_terms_found": ["term1", "term2"],
    "relationships_found": ["X is Y", "A causes B"],
    "content_sufficient_for_request": true/false
  }},
  "questions": [
    {{
      "question_id": 1,
      "question_type": "MCQ",
      "reasoning": {{
        "source_lines": "[COPY-PASTE the exact line(s) from image]",
        "option_sources": {{
          "a": "[From image]",
          "b": "[From image]",
          "c": "[From image]",
          "d": "[From image or 'None of these']"
        }},
        "derivation": "[Explain how the answer is supported strictly by the source lines]"
      }},
      "question_text": "[Question constructed only from image]",
      "options": {{
        "a": "[From image]",
        "b": "[From image]",
        "c": "[From image]",
        "d": "[From image or 'None of these']"
      }},
      "correct_answer": "a",
      "explanation": {{
        "a": "✓ Correct: [50-60 word scientific explanation WHY this is correct - no reference to image/text]",
        "b": "✗ Incorrect: [Scientific reason why wrong - what this term actually refers to]",
        "c": "✗ Incorrect: [Scientific reason why wrong - what this term actually refers to]",
        "d": "✗ Incorrect: [Scientific reason why wrong - what this term actually refers to]"
      }}
    }},
    {{
      "question_id": 2,
      "question_type": "ASSERTION_REASON",
      "reasoning": {{
        "source_lines": "[COPY-PASTE the exact assertion and reason lines from image]",
        "option_sources": {{
          "a": "Both A and R are true and R is the correct explanation of A",
          "b": "Both A and R are true but R is NOT the correct explanation of A",
          "c": "A is true but R is false",
          "d": "A is false but R is true"
        }},
        "derivation": "[Explain how the answer is determined based on image text]"
      }},
      "question_text": "Assertion (A): [Assertion statement from image]\\n\\nReason (R): [Reason statement from image]",
      "options": {{
        "a": "Both A and R are true and R is the correct explanation of A",
        "b": "Both A and R are true but R is NOT the correct explanation of A",
        "c": "A is true but R is false",
        "d": "A is false but R is true"
      }},
      "correct_answer": "a/b/c/d",
      "explanation": {{
        "a": "✓ Correct: [A is true because (scientific reason)..., R is true because (scientific reason)..., R explains A because (logical connection)...]",
        "b": "✗ Incorrect: [Why - e.g., R does not explain A because they are independent facts]",
        "c": "✗ Incorrect: [Why - e.g., R is actually true because (scientific reason)]",
        "d": "✗ Incorrect: [Why - e.g., A is actually true because (scientific reason)]"
      }}
    }},
    {{
      "question_id": 3,
      "question_type": "MATCH_THE_COLUMN",
      "reasoning": {{
        "source_lines": "[COPY-PASTE the exact matching pairs from image]",
        "option_sources": {{
          "a": "1-a, 2-b, 3-c, 4-d",
          "b": "1-b, 2-a, 3-d, 4-c",
          "c": "1-c, 2-d, 3-a, 4-b",
          "d": "1-d, 2-c, 3-b, 4-a"
        }},
        "derivation": "[Explain the correct matching based on image text]"
      }},
      "question_text": "Match the following:\\n\\n| Column A | Column B |\\n|----------|----------|\\n| 1. [Item 1] | a. [Item a] |\\n| 2. [Item 2] | b. [Item b] |\\n| 3. [Item 3] | c. [Item c] |\\n| 4. [Item 4] | d. [Item d] |",
      "options": {{
        "a": "1-a, 2-b, 3-c, 4-d",
        "b": "1-b, 2-a, 3-d, 4-c",
        "c": "1-c, 2-d, 3-a, 4-b",
        "d": "1-d, 2-c, 3-b, 4-a"
      }},
      "correct_answer": "a",
      "explanation": {{
        "a": "✓ Correct: [1 matches a because (scientific reason)..., 2 matches b because (scientific reason)...]",
        "b": "✗ Incorrect: [1-b is wrong because (what 1 actually is and why it doesn't match b)...]",
        "c": "✗ Incorrect: [1-c is wrong because (scientific reasoning)...]",
        "d": "✗ Incorrect: [1-d is wrong because (scientific reasoning)...]"
      }}
    }}
  ],
  "unused_content": ["[Facts, statements, or terms from image not used in any question]"],
  "validation_status": {{
    "all_questions_from_image": true,
    "external_knowledge_used": false,
    "each_answer_traceable": true
  }}
}}

If content is insufficient, output as below (no code block):

{{
  "error": "INSUFFICIENT_CONTENT",
  "requested_questions": {question_count},
  "possible_questions": actual_generated_count,
  "reason": "[Briefly describe the content limitation]",
  "questions": [ ... generated questions ... ]
}}

If the required difficulty cannot be satisfied by content:

{{
  "warning": "DIFFICULTY_ADJUSTED",
  "requested_difficulty": "{difficulty}",
  "actual_difficulty": "[actual_difficulty]",
  "reason": "[Brief content analysis for why the requested level is not supported]",
  "questions": [ ... ]
}}

If content cannot support a specific question type:

{{
  "warning": "QUESTION_TYPE_UNAVAILABLE",
  "reason": "[Describe the lack of pairs or structure in image]",
  "alternative": "Generated additional MCQs instead"
}}

---

## TEXT FORMATTING RULES (MANDATORY - USE LATEX)

You MUST use LaTeX syntax for all scientific notation:

1. NO MARKDOWN: DO NOT use ** for bold or * for italics

2. SUBSCRIPTS/SUPERSCRIPTS - Use LaTeX:
   - $H_2O$, $CO_2$, $O_2$, $H_2SO_4$, $C_6H_{{12}}O_6$
   - $Ca^{{2+}}$, $PO_4^{{3-}}$, $m^2$, $cm^3$, $10^6$

3. GREEK LETTERS - Use LaTeX:
   - $\\alpha$, $\\beta$, $\\gamma$, $\\delta$, $\\lambda$, $\\mu$, $\\pi$
   - $\\Delta$, $\\Sigma$, $\\Omega$

4. CHEMICAL EQUATIONS:
   - $6CO_2 + 6H_2O \\rightarrow C_6H_{{12}}O_6 + 6O_2$

5. TABLES (for Match the Column) - Use LaTeX tabular:
   \\begin{{tabular}}{{|l|l|}}
   \\hline
   Column A & Column B \\\\
   \\hline
   1. Item & a. Match \\\\
   \\hline
   \\end{{tabular}}

---

## EXPLANATION GUIDELINES

For each question, provide option-wise explanations that are EDUCATIONAL:

Rules:
- Correct option: Explain WHY it is scientifically correct (50-60 words). Use LaTeX for formulas.
- Incorrect options: Explain WHY each is wrong using scientific reasoning.
- Focus on educational value - help students understand and remember the concept

FORBIDDEN phrases (NEVER use these):
- "as stated in the image"
- "as given in the text"
- "the passage mentions"
- "according to the image"
- "as written in the image"
- Any reference to "image", "text", "passage", or "given"

Example explanation format:
- "Correct: Mitochondria is the powerhouse of the cell because it performs cellular respiration, converting $C_6H_{{12}}O_6$ into ATP through oxidative phosphorylation."
- "Incorrect: Chloroplast is the site of photosynthesis where $CO_2$ and $H_2O$ are converted to glucose. It produces food, not ATP."

---

## SELF-AUDIT BEFORE FINAL OUTPUT

Before producing your answer, think step-by-step and verify:
- For every question, confirm you highlighted the exact supporting text(s) in the image.
- Confirm each answer (and wrong option) is VISIBLE and present in the image (or is "None of these" if justified).
- For assertion-reason: Both assertion and reason must be literally present as copy-paste from image.
- For match the column: Every element is strictly taken from image.
- For medium difficulty: You must always include at least one assertion-reason and one simple match the column with explicit reasoning fields fully filled out.
- At no point have you added any external fact, rephrased, extrapolated, inferred, or used any outside knowledge.
- A student with only this image should be able to independently justify every question, answer, and distractor using only this image.
- If not all validation boxes check, remove the question or adjust difficulty/question count as per protocol.

---

## FINAL INSTRUCTION

Generate test questions ONLY from what is contained in the uploaded image. For medium difficulty, you must always provide both assertion-reason and simple match the column questions with explicit reasoning/derivation fields. If in doubt, generate fewer questions rather than including external content. All question types, answers, options, matching pairs, and justifications must be strictly and literally traceable to the image only—never inferred, extrapolated, or reworded.

Now, process the image using:
- Difficulty: {difficulty}
- Question Count: {question_count}
- Subject: {subject}

# Output Format

Your response must be a single JSON object (not in a code block) strictly following the schema above. For medium level, at least one assertion-reason and one match the column question are mandatory, each with completed reasoning/derivation fields referencing exact image lines. For any limitation or validation failure, use the alternate schemas. Review and repeat the critical assertions about strict image-traceability and zero use of external knowledge at the end of your output as a reminder."""


# Question type instructions
QUESTION_TYPE_INSTRUCTIONS = {
    "mcq": """
## QUESTION TYPE RESTRICTION
You MUST generate ONLY standard Multiple Choice Questions (MCQ).
- DO NOT generate any Assertion-Reason questions
- DO NOT generate any Match the Column questions
- Generate ONLY direct factual MCQs, fill in the blanks, true/false converted to MCQ format
""",
    "assertion_reason": """
## QUESTION TYPE RESTRICTION
You MUST generate ONLY Assertion-Reason questions.
- DO NOT generate any standard MCQs
- DO NOT generate any Match the Column questions
- Every question MUST be in Assertion-Reason format with:
  - Assertion (A): [Statement from image]
  - Reason (R): [Another statement from image]
  - Standard A-R options (Both true and R explains A, Both true but R doesn't explain A, A true R false, A false R true)
""",
    "match_the_column": """
## QUESTION TYPE RESTRICTION
You MUST generate ONLY Match the Column questions.
- DO NOT generate any standard MCQs
- DO NOT generate any Assertion-Reason questions
- Every question MUST be in Match the Column format with:
  - Column A with numbered items (1, 2, 3, 4...)
  - Column B with lettered items (a, b, c, d...)
  - Options showing different matching combinations
""",
    "combination": ""  # No restriction, allow all types
}


# Tool definition for structured MCQ output
MCQ_TOOL = {
    "type": "function",
    "name": "create_multiple_choice_question",
    "description": "Create a multiple choice question with options, answer, and option-wise explanations",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The text of the multiple choice question"
            },
            "option_a": {
                "type": "string",
                "description": "Text for option A"
            },
            "option_b": {
                "type": "string",
                "description": "Text for option B"
            },
            "option_c": {
                "type": "string",
                "description": "Text for option C"
            },
            "option_d": {
                "type": "string",
                "description": "Text for option D"
            },
            "answer": {
                "type": "string",
                "description": "The correct answer option, such as 'A', 'B', 'C', or 'D'",
                "enum": ["A", "B", "C", "D"]
            },
            "explanation_a": {
                "type": "string",
                "description": "Scientific explanation for option A - WHY correct/incorrect using conceptual reasoning (50-60 words). Never reference 'image' or 'text'."
            },
            "explanation_b": {
                "type": "string",
                "description": "Scientific explanation for option B - WHY correct/incorrect using conceptual reasoning (50-60 words). Never reference 'image' or 'text'."
            },
            "explanation_c": {
                "type": "string",
                "description": "Scientific explanation for option C - WHY correct/incorrect using conceptual reasoning (50-60 words). Never reference 'image' or 'text'."
            },
            "explanation_d": {
                "type": "string",
                "description": "Scientific explanation for option D - WHY correct/incorrect using conceptual reasoning (50-60 words). Never reference 'image' or 'text'."
            }
        },
        "required": ["question", "option_a", "option_b", "option_c", "option_d", "answer",
                     "explanation_a", "explanation_b", "explanation_c", "explanation_d"],
        "additionalProperties": False
    },
    "strict": True
}

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
    model: str = "gpt-5-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    use_mcq_tool: bool = True,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions from a textbook image.
    
    Args:
        image_source: Path to the image file, or base64 encoded image bytes
        subject: Subject for the test (e.g., 'biology', 'physics', 'chemistry')
        difficulty: Difficulty level - 'easy', 'medium', or 'hard'
        question_count: Number of questions to generate
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        use_mcq_tool: Whether to include MCQ tool for structured output
        api_key: Optional API key (uses OPENAI_API_KEY env var if not provided)

    Returns:
        Dictionary containing the generated test questions in unified MCQ format:
        - MCQ: Standard multiple choice questions
        - ASSERTION_REASON: Assertion and reason embedded in question_text
        - MATCH_THE_COLUMN: Table format embedded in question_text

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
            # It's a file path
            base64_image = encode_image_to_base64(image_path)
            media_type = get_image_media_type(image_path)
        else:
            # Assume it's already a base64 string
            base64_image = str(image_source)
            media_type = "image/png"
    elif isinstance(image_source, bytes):
        # Raw bytes
        base64_image = base64.b64encode(image_source).decode("utf-8")
        media_type = "image/png"
    else:
        raise ValueError("image_source must be a file path, base64 string, or bytes")
    
    # Format the system prompt with parameters (using replace to avoid issues with JSON braces)
    formatted_prompt = SYSTEM_PROMPT.replace(
        '{difficulty}', str(difficulty)
    ).replace(
        '{question_count}', str(question_count)
    ).replace(
        '{subject}', str(subject)
    )
    
    # Build the request
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "input_text",
                    "text": formatted_prompt
                }
            ]
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
    
    # Build request parameters
    request_params = {
        "model": model,
        "input": messages,
        "max_output_tokens": max_tokens,
        "store": True,
        "text": {"format": {"type": "text"}},
        "reasoning": {},
    }
    
    # Add MCQ tool if requested for structured output
    if use_mcq_tool:
        request_params["tools"] = [MCQ_TOOL]

    # Make the API call
    response = client.responses.create(**request_params)

    # Log token usage
    if hasattr(response, 'usage') and response.usage:
        logger.info(f"Token usage - Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens}, Total: {response.usage.total_tokens}")

    # Extract the response text from Responses API format
    result_text = ""
    for item in response.output:
        if hasattr(item, 'type'):
            if item.type == 'message' and hasattr(item, 'content'):
                for content_block in item.content:
                    if hasattr(content_block, 'text'):
                        result_text += content_block.text
            elif item.type == 'text' and hasattr(item, 'text'):
                result_text += item.text

    # Try to parse as JSON
    try:
        # Clean up the response text (remove any markdown code blocks if present)
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
        # Return raw text if JSON parsing fails
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
    model: str = "gpt-5-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    use_mcq_tool: bool = True,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions from a textbook image URL.
    
    Args:
        image_url: URL of the image
        subject: Subject for the test (e.g., 'biology', 'physics', 'chemistry')
        difficulty: Difficulty level - 'easy', 'medium', or 'hard'
        question_count: Number of questions to generate
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        use_mcq_tool: Whether to include MCQ tool for structured output
        api_key: Optional API key (uses OPENAI_API_KEY env var if not provided)

    Returns:
        Dictionary containing the generated test questions in unified MCQ format:
        - MCQ: Standard multiple choice questions
        - ASSERTION_REASON: Assertion and reason embedded in question_text
        - MATCH_THE_COLUMN: Table format embedded in question_text
    """
    # Initialize client
    client = OpenAI(api_key=api_key) if api_key else OpenAI()

    # Format the system prompt with parameters (using replace to avoid issues with JSON braces)
    formatted_prompt = SYSTEM_PROMPT.replace(
        '{difficulty}', str(difficulty)
    ).replace(
        '{question_count}', str(question_count)
    ).replace(
        '{subject}', str(subject)
    )

    # Build the request with URL instead of base64
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "input_text",
                    "text": formatted_prompt
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": image_url
                }
            ]
        }
    ]
    
    # Build request parameters
    request_params = {
        "model": model,
        "input": messages,
        "max_output_tokens": max_tokens,
        "store": True,
        "text": {"format": {"type": "text"}},
        "reasoning": {},
    }
    
    # Add MCQ tool if requested for structured output
    if use_mcq_tool:
        request_params["tools"] = [MCQ_TOOL]

    # Make the API call
    response = client.responses.create(**request_params)

    # Log token usage
    if hasattr(response, 'usage') and response.usage:
        logger.info(f"Token usage - Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens}, Total: {response.usage.total_tokens}")

    # Extract the response text from Responses API format
    result_text = ""
    for item in response.output:
        if hasattr(item, 'type'):
            if item.type == 'message' and hasattr(item, 'content'):
                for content_block in item.content:
                    if hasattr(content_block, 'text'):
                        result_text += content_block.text
            elif item.type == 'text' and hasattr(item, 'text'):
                result_text += item.text

    # Try to parse as JSON
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
    formatted_prompt: str,
    image_content: dict,
    model: str,
    temperature: float,
    max_tokens: int,
    use_mcq_tool: bool,
    previous_questions: list = None,
    question_type: str = "combination",
    difficulty: str = "medium",
    subject: str = "biology",
    question_count: int = 3,
) -> dict:
    """
    Internal helper to make a single batch API request.

    Args:
        client: OpenAI client instance
        formatted_prompt: The formatted system prompt (used for combination mode)
        image_content: Dict with image data (type and url/base64)
        model: Model to use
        temperature: Temperature setting
        max_tokens: Max tokens
        use_mcq_tool: Whether to use MCQ tool
        previous_questions: List of previously generated question texts to avoid
        question_type: Type of questions to generate (mcq, assertion_reason, match_the_column, combination)
        difficulty: Difficulty level for specific prompts
        subject: Subject for specific prompts
        question_count: Number of questions for this batch

    Returns:
        Parsed result dictionary
    """
    # Use specific prompt from config for non-combination types
    if question_type != "combination" and (question_type, difficulty) in PROMPTS_CONFIG:
        formatted_prompt = get_prompt(question_type, difficulty, subject, question_count)
    else:
        # For combination mode, use the old approach with type instructions
        type_instruction = QUESTION_TYPE_INSTRUCTIONS.get(question_type, "")
        if type_instruction:
            formatted_prompt = formatted_prompt + "\n\n" + type_instruction

    # Build user content
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

    request_params = {
        "model": model,
        "input": messages,
        "max_output_tokens": max_tokens,
        "store": True,
        "text": {"format": {"type": "text"}},
        "reasoning": {},
    }

    if use_mcq_tool:
        request_params["tools"] = [MCQ_TOOL]

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

    # Parse JSON
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
    question_type: Literal["mcq", "assertion_reason", "match_the_column", "combination"] = "combination",
    batch_size: int = 3,
    model: str = "gpt-5-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    use_mcq_tool: bool = True,
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
        question_type: Type of questions - 'mcq', 'assertion_reason', 'match_the_column', or 'combination'
        batch_size: Number of questions per batch (default 3)
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        use_mcq_tool: Whether to include MCQ tool for structured output
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

    # Collect all questions
    all_questions = []
    previous_question_texts = []
    combined_result = None

    for batch_idx, batch_count in enumerate(batches):
        # Format prompt for this batch
        formatted_prompt = SYSTEM_PROMPT.replace(
            '{difficulty}', str(difficulty)
        ).replace(
            '{question_count}', str(batch_count)
        ).replace(
            '{subject}', str(subject)
        )

        # Make request
        batch_result = _make_single_batch_request(
            client=client,
            formatted_prompt=formatted_prompt,
            image_content=image_content,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use_mcq_tool=use_mcq_tool,
            previous_questions=previous_question_texts if batch_idx > 0 else None,
            question_type=question_type,
            difficulty=difficulty,
            subject=subject,
            question_count=batch_count
        )

        # Handle parse errors
        if "parse_error" in batch_result:
            if combined_result is None:
                return batch_result
            else:
                # Return what we have so far
                break

        # Extract questions from this batch
        if "questions" in batch_result:
            for q in batch_result["questions"]:
                # Renumber question IDs
                q["question_id"] = len(all_questions) + 1
                all_questions.append(q)

                # Add question text to avoid list
                if "question_text" in q:
                    previous_question_texts.append(q["question_text"])

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


def generate_neet_test_from_url_batched(
    image_url: str,
    subject: str = "biology",
    difficulty: Literal["easy", "medium", "hard"] = "hard",
    question_count: int = 5,
    question_type: Literal["mcq", "assertion_reason", "match_the_column", "combination"] = "combination",
    batch_size: int = 3,
    model: str = "gpt-5-mini",
    temperature: float = 1.0,
    max_tokens: int = 2048,
    use_mcq_tool: bool = True,
    api_key: Optional[str] = None,
) -> dict:
    """
    Generate NEET test questions from URL in batches to avoid repetition.

    Args:
        image_url: URL of the image
        subject: Subject for the test
        difficulty: Difficulty level - 'easy', 'medium', or 'hard'
        question_count: Total number of questions to generate
        question_type: Type of questions - 'mcq', 'assertion_reason', 'match_the_column', or 'combination'
        batch_size: Number of questions per batch (default 3)
        model: OpenAI model to use
        temperature: Sampling temperature (0-2)
        max_tokens: Maximum tokens in response
        use_mcq_tool: Whether to include MCQ tool for structured output
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

    # Collect all questions
    all_questions = []
    previous_question_texts = []
    combined_result = None

    for batch_idx, batch_count in enumerate(batches):
        formatted_prompt = SYSTEM_PROMPT.replace(
            '{difficulty}', str(difficulty)
        ).replace(
            '{question_count}', str(batch_count)
        ).replace(
            '{subject}', str(subject)
        )

        batch_result = _make_single_batch_request(
            client=client,
            formatted_prompt=formatted_prompt,
            image_content=image_content,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use_mcq_tool=use_mcq_tool,
            previous_questions=previous_question_texts if batch_idx > 0 else None,
            question_type=question_type,
            difficulty=difficulty,
            subject=subject,
            question_count=batch_count
        )

        if "parse_error" in batch_result:
            if combined_result is None:
                return batch_result
            else:
                break

        if "questions" in batch_result:
            for q in batch_result["questions"]:
                q["question_id"] = len(all_questions) + 1
                all_questions.append(q)
                if "question_text" in q:
                    previous_question_texts.append(q["question_text"])

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