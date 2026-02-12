from __future__ import annotations
from openai import OpenAI
import os

def generate_question_distribution(
    image_urls: list[str],
    system_prompt: str,
    tools: list[dict],
    model: str = "gpt-5-mini",
    max_output_tokens: int = 2048,
    api_key: str = None
) -> dict:
    """
    Generate question difficulty distribution using OpenAI API.

    Args:
        image_urls: List of base64 encoded image URLs
        system_prompt: The system prompt/instructions
        tools: List of tool definitions
        model: Model to use (default: gpt-5-mini)
        max_output_tokens: Maximum tokens in response (default: 2048)
        api_key: Optional API key (uses OPENAI_API_KEY env var if not provided)

    Returns:
        The API response object
    """
    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    
    response = client.responses.create(
        model=model,
        instructions=system_prompt,
        input=[
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": url} for url in image_urls]
            }
        ],
        tools=tools,
        text={"format": {"type": "text"}},
        reasoning={},
        max_output_tokens=max_output_tokens,
        store=True,
        include=["web_search_call.action.sources"]
    )
    
    return response


# ============================================================
# DUMMY PROMPT - REPLACE WITH YOUR OWN
# ============================================================
SYSTEM_PROMPT = """Estimate and recommend the **maximum possible** number of easy, medium, and hard questions that can realistically be generated from the uploaded content—including all diagrams, figures, tables, formulas, and text—while providing clear, step-by-step reasoning for each count.

**IMPORTANT: Only 3 question types are allowed:**
1. **MCQ (Multiple Choice Questions)**
2. **Match the Column**
3. **Assertion-Reason**

---

## CRITICAL RULE: Time-Consuming = HARD

**Any question that requires significant time to solve is automatically HARD category, regardless of conceptual simplicity.**

This includes:
- Match the Column with 5+ pairs (time-consuming to verify all; always maximize number of such questions given the number of available logical pairs)
- MCQs with calculations
- MCQs requiring elimination of similar options
- Assertion-Reason (always requires time to analyze both statements)
- Any question needing multiple steps to solve

---

## Question Type & Difficulty Mapping

### EASY (Simple MCQs ONLY)
- Direct fact recall MCQs (one-step thinking)
- Definition-based MCQs
- Term identification MCQs
- Label/part identification from diagrams
- Unit/Symbol identification MCQs
- "Which of the following is..." type MCQs (obvious answer)
- Single concept MCQs with immediately clear answer
- **Rule:** If answer is obvious within 10 seconds = EASY

### MEDIUM (Conceptual MCQs, Simple Match the Column, and LONG/CONFUSING Questions)
- **Conceptual MCQs:** Require understanding but not extensive time
- **Simple Match the Column (4 pairs only):**
  - Term ↔ Definition
  - Term ↔ Function
  - Process ↔ Result
  - Scientist ↔ Discovery
  - Formula ↔ Name
  - Part ↔ Function
- Comparison MCQs (straightforward)
- Application-based MCQs (single concept application)
- **Long MCQs:** Any question that takes a long time to read, is wordy, or whose length creates confusion—even if it's not hard to solve. (For example, questions with complex or verbose stems, or that require careful reading/rewriting to extract key facts, but are quick to answer if read carefully.)
- **Rule:** Requires thinking, or is long/confusing enough to take time to read/parse (regardless of answer difficulty), but still solvable in 30-60 seconds. If unsure: if the question is long and might confuse the average solver due to its presentation (not necessarily answer complexity), assign as MEDIUM.

### HARD (Assertion-Reason + Complex Match the Column + Time-Consuming MCQs)
- **Assertion-Reason (ALWAYS HARD):**
  - Cause ↔ Effect statements
  - Principle ↔ Explanation
  - Statement ↔ Justification
  - Fact ↔ Reason
  - All A-R format questions require time to analyze both statements
- **Complex Match the Column (5+ pairs) = HARD:**
  - For every set of 5 or more logical pairs, generate the maximum possible number of Match the Column questions with 5 or more pairs each.
  - More pairs = more time to verify = HARD
  - Cross-category matching = HARD
  - Matching requiring deeper knowledge = HARD
  - **Always maximize the number of possible Hard Match the Column questions by using every available set of 5 or more logical pairs.**
- **Time-Consuming MCQs:**
  - Multi-step reasoning MCQs
  - Calculation-based MCQs
  - MCQs where all options look similar
  - MCQs requiring analysis of 3+ concepts
  - Diagram analysis MCQs (complex interpretation)
  - "Which statement is INCORRECT" type (must verify all options)
  - "Select the correct combination" type MCQs
- **Rule:** Takes more than 60 seconds to solve = HARD

---

## Extraction Guidelines for Maximum Questions

### From TEXT content, extract:
- Every unique term → Easy MCQ
- Every definition → Easy MCQ
- Every single fact → Easy MCQ
- Every relationship → Medium MCQ
- Every cause-effect → Assertion-Reason (Hard)
- Every comparison → Medium/Hard MCQ (based on complexity)
- Every exception/special case → Hard MCQ
- Every "because/therefore/hence" statement → Assertion-Reason (Hard)
- Any question with a long/wordy/confusing stem (even if answer is simple) → Medium MCQ

### From DIAGRAMS/FIGURES, extract:
- Every labeled part → Easy MCQ ("Identify part X")
- Simple part-function pairs (4 pairs) → Medium Match the Column
- Complex part-function pairs (5+ pairs) → Hard Match the Column; always maximize total number of Hard Match the Column questions from 5+ pair sets
- Process flow analysis → Hard MCQ
- "What if X changes" → Hard MCQ
- Relationships between multiple parts → Assertion-Reason (Hard)
- Long/verbose diagram questions ("Given the following diagram description...") → Medium MCQ

### From FORMULAS, extract:
- Formula name identification → Easy MCQ
- Unit identification → Easy MCQ
- Variable meaning → Easy MCQ
- Simple formula application → Medium MCQ
- Multi-step calculation → Hard MCQ
- Variable relationships → Assertion-Reason (Hard)
- Formula-Name matching (4 pairs) → Medium Match the Column
- Formula-Application matching (5+ pairs) → Hard Match the Column; always maximize
- Long calculation questions that are not multi-step but require reading a long stem → Medium MCQ

### From TABLES, extract:
- Single cell data recall → Easy MCQ
- Row/column comparison → Medium MCQ
- Simple column matching (4 pairs) → Medium Match the Column
- Complex table analysis → Hard MCQ
- Large table matching (5+ pairs) → Hard Match the Column; always maximize
- Pattern/trend identification → Hard MCQ
- Questions with extensive table description (long stem/complex setup but quick answer) → Medium MCQ

---

## Match the Column Counting Rules

| Number of Pairs | Difficulty | Reason |
|-----------------|------------|--------|
| 4 pairs | MEDIUM | Quick to solve |
| 5 pairs | HARD | Time-consuming |
| 6+ pairs | HARD | Very time-consuming |

**Calculation:**
- Count ALL logical pairings in content.
- Pairs that can be grouped (4 pairs) = Medium Match the Column.
- For every set of 5 or more logical pairs, generate the maximum number of possible Hard Match the Column questions (i.e., maximize total number of such questions by dividing total eligible pairs into as many non-overlapping 5+ pair groupings as possible). For example, if you have 12 pairs, you can make 2 questions of 6 pairs each (12÷6=2), or 2 questions of 5 and 7 pairs, etc. Always show step-by-step how maximum is determined.
- Assign difficulty based on pair count per question.

---

## Assertion-Reason Generation Rules (ALWAYS HARD)

**Identify ALL these patterns in content:**
1. Cause → Effect statements
2. Principle → Application
3. Fact → Explanation
4. Process → Outcome
5. "X happens because Y"
6. "Due to X, Y occurs"
7. "X leads to Y"
8. "X is responsible for Y"

**Each pattern = 1 Assertion-Reason question (Hard)**

**A-R Format possibilities from same content:**
- Both A and R correct, R explains A ✓
- Both A and R correct, R does NOT explain A
- A correct, R incorrect
- A incorrect, R correct
- Both incorrect

**This means: 1 cause-effect relationship can generate up to 2-3 different A-R questions by changing the correctness/relationship**

---

## Maximization Checklist

Before finalizing counts, verify:

**For EASY:**
☐ Counted every unique term?
☐ Counted every definition?
☐ Counted every simple fact?
☐ Counted every diagram label?
☐ Counted every unit/symbol?

**For MEDIUM:**
☐ Counted all conceptual MCQs (solvable in <60 sec)?
☐ Counted all 4-pair Match the Column possibilities?
☐ Counted all simple comparison MCQs?
☐ Counted every long/wordy/confusing question where reading the question takes time or creates confusion—even if the answer is simple?

**For HARD:**
☐ Counted ALL Assertion-Reason pairs?
☐ Counted all 5+ pair Match the Column possibilities? **Ensure you have maximized the number of such questions by making as many Hard Match the Column (5+ pairs) questions as possible from the available logical pairs.**
☐ Counted all calculation-based MCQs?
☐ Counted all multi-step reasoning MCQs?
☐ Counted all "verify all options" type MCQs?
☐ Counted all complex diagram analysis MCQs?
☐ **Asked: "Is there ANY question that takes time to solve that I missed?"**

---

## Output Format

**Output ONLY a valid JSON object** (no commentary, markdown, or extra text):

{
  "easy_count": [integer],
  "easy_reasoning": "[List ALL simple MCQ sources: terms, definitions, facts, labels. Explain why each is EASY (immediate answer). State maximum reached.]",
  "medium_count": [integer],
  "medium_reasoning": "[List ALL conceptual MCQs + ALL 4-pair Match the Column questions + ALL long/confusing/wordy questions that require time to read or could create confusion. Show pair counting: X pairs ÷ 4 = Y questions. Explain why each is MEDIUM (solvable in 30-60 sec or long/confusing in its stem). State maximum reached.]",
  "hard_count": [integer],
  "hard_reasoning": "[List ALL Assertion-Reason pairs + ALL maximized 5+ pair Match the Column + ALL time-consuming MCQs. For Hard Match the Column, explicitly show the step-by-step maximization process: count total eligible pairs, divide into largest non-overlapping sets of 5+ pairs, and show how many questions are generated. Explain why each is HARD (takes >60 sec OR requires deep analysis). State maximum reached.]"
}

---

## Examples

**Example 1: Science Chapter with Diagram**
{
  "easy_count": 22,
  "easy_reasoning": "Simple MCQs: 12 unique terms (each=1 MCQ), 5 definitions (each=1 MCQ), 5 diagram labels (each=1 MCQ). All answerable in <10 seconds. Total: 12+5+5 = 22 Easy MCQs. No more direct recall content available.",
  "medium_count": 7,
  "medium_reasoning": "Conceptual MCQs: 4 relationship-based MCQs (require understanding, solvable in 30-60 sec). Match the Column: Found 8 term-definition pairs, creating 2 Match the Column questions (8÷4=2) with 4 pairs each (MEDIUM). One MCQ with a long/wordy stem that may confuse students, added as Medium. Total: 4+2+1 = 7 Medium questions. All conceptual and long/confusing questions counted.",
  "hard_count": 9,
  "hard_reasoning": "Assertion-Reason: 5 cause-effect relationships = 5 A-R questions (each takes >60 sec to analyze both statements). Complex Match the Column: Found 12 part-function pairs; maximized by making 2 Match the Column questions with 6 pairs each (12÷6=2). Each such question is HARD. Time-consuming MCQs: 2 calculation-based MCQs + 1 'find incorrect statement' MCQ (must verify all options). Total: 5+2+3 = 10 Hard questions. All time-consuming possibilities counted and maximized."
}

**Example 2: Theory-Heavy Content**
{
  "easy_count": 15,
  "easy_reasoning": "15 direct facts and terms, each generating immediate-answer MCQ. No diagrams.",
  "medium_count": 5,
  "medium_reasoning": "3 conceptual understanding MCQs. 1 Match the Column with 4 pairs (term↔definition). 1 long-worded MCQ whose stem could cause confusion, assigned to Medium. Total: 3+1+1 = 5 Medium.",
  "hard_count": 7,
  "hard_reasoning": "4 Assertion-Reason questions from cause-effect statements. Match the Column: 5 application pairs maximized as 1 question with 5 pairs (HARD). 2 MCQs requiring verification of all 4 options (time-consuming). Total: 4+1+2 = 7 Hard."
}

---

## FINAL REMINDER

**HARD = Time-Consuming OR Conceptually Complex**

**MEDIUM = Requires thinking, OR is long or confusing to read—question length or confusing framing counts as Medium, even if answer is simple**

Always ask:
1. "Does this question take significant time to solve?" → If YES = HARD
2. "Does the question take significant time just to read, or is it long/confusing in its stem?" → If YES = MEDIUM (unless it also takes significant time to solve—then HARD)
3. "Does Match the Column have 5+ pairs?" → If YES = HARD, and always MAXIMIZE the total number of such questions from available pairs
4. "Is this Assertion-Reason?" → ALWAYS HARD
5. "Must solver verify multiple options/steps?" → If YES = HARD

**MAXIMIZE extraction. If content exists, a question CAN be made. Do NOT undercount any category, especially Hard Match the Column. Explicitly maximize and show reasoning for all 5+ pair Match the Column questions.** 

---

# Steps

1. Extract and count all possible easy, medium, and hard question candidates from the content according to the explicit mapping rules above.
2. For each, provide clear, step-by-step reasoning, listing sources of every question and showing how you categorize, maximize, or assign them (especially for medium: include any long/confusing stems).
3. Always maximize number of Match the Column (5+ pair) HARD questions.
4. Output ONLY a valid JSON object in the exact output format below. 

# Output Format

Output must be a single valid JSON object with keys: "easy_count", "easy_reasoning", "medium_count", "medium_reasoning", "hard_count", "hard_reasoning". Do not include any commentary, markdown, or extra text. 

---

**REMINDER:** Medium questions include not just conceptual or 4-pair-match-the-column, but also any long, wordy, or confusing questions—even if the answer is simple or quick once understood. Be rigorous in counting and explaining these as Medium. Persist until all step-by-step maximization and categorization criteria above are satisfied before outputting your final answer.
"""

# ============================================================
# DUMMY TOOLS - REPLACE WITH YOUR OWN
# ============================================================
TOOLS = [
  {
    "type": "function",
    "name": "set_question_difficulty_distribution",
    "description": "Specify the number and reasoning for easy, medium, and hard questions.",
    "strict": True,
    "parameters": {
      "type": "object",
      "properties": {
        "easy_count": {
          "type": "integer",
          "description": "Number of easy questions to include."
        },
        "easy_reasoning": {
          "type": "string",
          "description": "Reason for selecting this number of easy questions."
        },
        "medium_count": {
          "type": "integer",
          "description": "Number of medium questions to include."
        },
        "medium_reasoning": {
          "type": "string",
          "description": "Reason for selecting this number of medium questions."
        },
        "hard_count": {
          "type": "integer",
          "description": "Number of hard questions to include."
        },
        "hard_reasoning": {
          "type": "string",
          "description": "Reason for selecting this number of hard questions."
        }
      },
      "required": [
        "easy_count",
        "easy_reasoning",
        "medium_count",
        "medium_reasoning",
        "hard_count",
        "hard_reasoning"
      ],
      "additionalProperties": False
    }
  }
]


# Example usage
if __name__ == "__main__":
    # Dummy images - REPLACE WITH YOUR OWN
    images = [
        "/home/ubuntu/apps/test-generator-from-paragraph/Test Cases for POC of test generation/Biology/case_1/plantkingdom_1.png",
        "/home/ubuntu/apps/test-generator-from-paragraph/Test Cases for POC of test generation/Biology/case_1/plantkingdom_2.png"
    ]
    
    response = generate_question_distribution(
        image_urls=images,
        system_prompt=SYSTEM_PROMPT,  # ADD YOUR PROMPT
        tools=TOOLS,                   # ADD YOUR TOOLS
        model="gpt-5-mini"
    )
    
    print(response)