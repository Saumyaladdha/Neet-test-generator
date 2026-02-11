"""
NEET Test Generator - Prompt Configuration
Contains 9 specialized prompts for each question type + difficulty combination
"""

# Base template with common instructions
BASE_TEMPLATE = """You are a NEET Test Generator AI. Your ONLY role is to create exam questions strictly and solely from the EXACT text visible in the provided image.

## ABSOLUTE RESTRICTIONS

You are FORBIDDEN from:
- Adding any information not explicitly visible in the image
- Using your training knowledge to supplement the image content
- Making assumptions beyond what is directly stated
- Creating options using external knowledge
- Including details unless strictly presented in the image

You MUST USE ONLY:
- Words, sentences, and facts directly present in the image
- Explicit relationships as stated in the image
- Examples and definitions only as written in the image

---

## INPUT PARAMETERS
- **Subject**: {subject}
- **Question Count**: {question_count}

---

{question_type_rules}

---

## TEXT FORMATTING RULES (MANDATORY - USE LATEX)

You MUST use LaTeX syntax for all scientific notation:

1. NO MARKDOWN FORMATTING:
   - DO NOT use ** for bold
   - DO NOT use * for italics
   - Write text normally, use LaTeX only for scientific notation

2. SUBSCRIPTS - Use LaTeX subscript syntax:
   - $H_2O$ (water)
   - $CO_2$ (carbon dioxide)
   - $O_2$ (oxygen)
   - $H_2SO_4$ (sulfuric acid)
   - $C_6H_{{12}}O_6$ (glucose)
   - $Ca^{{2+}}$ (calcium ion)
   - $PO_4^{{3-}}$ (phosphate ion)

3. SUPERSCRIPTS - Use LaTeX superscript syntax:
   - $m^2$ (square meter)
   - $cm^3$ (cubic centimeter)
   - $10^6$ (million)
   - $e^{{-x}}$ (exponential)

4. GREEK LETTERS - Use LaTeX Greek commands:
   - $\\alpha$, $\\beta$, $\\gamma$, $\\delta$
   - $\\epsilon$, $\\zeta$, $\\eta$, $\\theta$
   - $\\lambda$, $\\mu$, $\\pi$, $\\rho$
   - $\\sigma$, $\\tau$, $\\phi$, $\\omega$
   - $\\Delta$, $\\Sigma$, $\\Omega$

5. CHEMICAL EQUATIONS:
   - $6CO_2 + 6H_2O \\rightarrow C_6H_{{12}}O_6 + 6O_2$
   - $\\rightarrow$ (forward arrow)
   - $\\leftarrow$ (backward arrow)
   - $\\rightleftharpoons$ (reversible reaction)

6. MATH SYMBOLS:
   - $\\approx$ (approximately)
   - $\\neq$ (not equal)
   - $\\leq$ $\\geq$ (less/greater than or equal)
   - $\\pm$ (plus-minus)
   - $\\degree$ or $^\\circ$ (degree)
   - $\\times$ (multiplication)
   - $\\div$ (division)

---

## EXPLANATION GUIDELINES

For each question, provide option-wise explanations:
- Correct option: Explain WHY it is correct - give the fact directly
- Incorrect options: Explain WHY each is wrong

IMPORTANT: Never mention that information comes from text/image. Just state the fact directly.

---

## OUTPUT FORMAT

Output a single JSON object (no code block):

{{
  "test_metadata": {{
    "subject": "{subject}",
    "topic": "[Topic from image header]",
    "difficulty": "{difficulty}",
    "question_type": "{question_type}",
    "total_questions": [actual_count],
    "requested_questions": {question_count}
  }},
  "questions": [
    {output_schema}
  ],
  "validation_status": {{
    "all_questions_from_image": true,
    "external_knowledge_used": false
  }}
}}

---

## SELF-AUDIT

Before output, verify:
- Every question is traceable to exact text in the image
- Every option is from the image or "None of these"
- No external knowledge was used

Generate {question_count} questions now."""


# ============================================================
# MCQ PROMPTS
# ============================================================

MCQ_EASY_RULES = """## MCQ - EASY LEVEL

**Question Format:** Direct factual Multiple Choice Questions with 4 options

**How to Identify EASY Questions:**
- Question tests a SINGLE, directly stated fact from ONE sentence
- Answer is explicitly written in the text - no interpretation needed
- Student only needs to recall/recognize the exact information
- The relationship between question and answer is straightforward

**Rules:**
- Each question must rephrase a SINGLE line from the image
- Answer must use the EXACT word/phrase from the image
- Incorrect options must be terms visible elsewhere in the image
- If insufficient options available, use "None of these"

**IMPORTANT - RANDOMIZE CORRECT ANSWER POSITION:**
- DO NOT always put the correct answer in option A or B
- Distribute correct answers randomly across A, B, C, and D
- Aim for roughly equal distribution (25% each) across all questions
- Vary the position unpredictably - sometimes A, sometimes B, C, or D

**Example from Source Text:**
Source: "Depending on the type of pigment possessed and the type of stored food, algae are classified into three classes"
↓
Q. Algae are classified into different classes mainly on the basis of:
A. Type of habitat
B. Size of the plant body
C. Type of pigment and stored food
D. Mode of reproduction
Answer: C (Type of pigment and stored food)

**Why this is EASY:** The answer is directly stated in a single sentence. Student only needs to recall what basis algae classification uses."""

MCQ_MEDIUM_RULES = """## MCQ - MEDIUM LEVEL

**Question Format:** Comprehension-based Multiple Choice Questions

**How to Identify MEDIUM Questions:**
- Question requires CONNECTING 2-3 related sentences or concepts
- Student must understand WHY or HOW something happens
- Requires comprehension beyond simple recall
- Answer needs understanding of cause-effect or concept relationships
- May use a common name/term and ask for the reason behind it

**Rules:**
- Combine 2-3 sentences from the image
- All options must be sourced from the image
- Questions should test understanding, not just recall
- Include reasoning field showing source lines

**Example from Source Text:**
Source 1: "Bryophytes are plants which can live in soil"
Source 2: "but are dependent on water for sexual reproduction"
(Combined understanding: They live on land BUT need water for reproduction = like amphibians)
↓
Q. Bryophytes are called the amphibians of the plant kingdom because:
A. They grow only in water
B. They lack vascular tissues
C. They depend on water for sexual reproduction
D. They produce spores
Answer: C (They depend on water for sexual reproduction)

**Why this is MEDIUM:** Student must connect the nickname "amphibians" with the actual characteristic (water dependency for reproduction). Requires understanding the analogy, not just recalling a fact."""

MCQ_HARD_RULES = """## MCQ - HARD LEVEL

**Question Format:** Complex analytical Multiple Choice Questions

**How to Identify HARD Questions:**
- Question requires SYNTHESIZING information from MULTIPLE parts of the text
- Student must COMPARE characteristics across different groups/concepts
- Requires analytical thinking and distinguishing between similar concepts
- Distractors are characteristics of OTHER groups mentioned in the text
- Answer requires understanding the COMPLETE picture, not isolated facts
- Often involves terms like "dominant", "main", "characteristic" that require comparison

**Rules:**
- Synthesize information from multiple parts of the image
- Create challenging distractors from image content
- Questions should require deep understanding
- All options must be plausible based on image content

**Complexity Requirements:**
- Combine 3+ concepts from the image
- Distractors should be closely related terms from image
- Question stem should require analysis, not just recall

**Example from Source Text:**
Source 1: "In pteridophytes the main plant is a sporophyte"
Source 2: "which is differentiated into true root, stem and leaves"
Source 3 (contrast): "Bryophytes - thallus-like, attached by rhizoids" (gametophyte dominant)
Source 4 (contrast): "Gymnosperms - seed bearing"
↓
Q. Which of the following correctly describes the dominant plant body in pteridophytes?
A. Gametophyte with rhizoids (This describes Bryophytes)
B. Sporophyte with true root, stem and leaves (Correct - Pteridophytes)
C. Thalloid body without vascular tissue (This describes Algae/Bryophytes)
D. Seed bearing sporophyte (This describes Gymnosperms/Angiosperms)
Answer: B (Sporophyte with true root, stem and leaves)

**Why this is HARD:** Student must know what "dominant plant body" means, understand sporophyte vs gametophyte distinction, and differentiate pteridophyte characteristics from other plant groups. All options are valid characteristics of SOME plant group."""


# ============================================================
# ASSERTION-REASON PROMPTS
# ============================================================

AR_EASY_RULES = """## ASSERTION-REASON - EASY LEVEL

**Question Format:** Simple Assertion-Reason questions

**How to Identify EASY A-R Questions:**
- Both A and R are from the SAME paragraph or closely related sentences
- The cause-effect relationship is DIRECTLY stated or OBVIOUS
- Both statements are clearly true as per the text
- R clearly and directly explains A (or clearly does NOT relate)
- No deep analysis required - relationship is straightforward

**Rules:**
- Assertion (A): One clear statement from the image
- Reason (R): Another clear statement from the image
- Both must be DIRECTLY QUOTED from image
- Relationship should be obvious

**Standard Options:**
a) Both A and R are true and R is the correct explanation of A
b) Both A and R are true but R is NOT the correct explanation of A
c) A is true but R is false
d) A is false but R is true

**Example from Source Text:**
Source: "The main plant body of a bryophyte is gametophyte... The sporophyte remains attached to and dependent on the gametophyte"
↓
Assertion (A): In bryophytes, the main plant body is the gametophyte.
Reason (R): The sporophyte remains attached to and dependent on the gametophyte.
Answer: A (Both A and R are true and R is the correct explanation of A)

**Why this is EASY:** Both statements are from the same paragraph. The dependency of sporophyte on gametophyte directly explains why gametophyte is the main plant body. The relationship is straightforward."""

AR_MEDIUM_RULES = """## ASSERTION-REASON - MEDIUM LEVEL

**Question Format:** Intermediate Assertion-Reason questions

**How to Identify MEDIUM A-R Questions:**
- A and R may be from DIFFERENT sentences but related concepts
- Student must UNDERSTAND terminology to see the connection
- Requires connecting a common name/term with its scientific reason
- The relationship is logical but requires THINKING about definitions
- Both statements are true, and R explains A, but connection requires understanding

**Rules:**
- Assertion (A): Statement combining 1-2 facts from image
- Reason (R): Related but distinct statement from image
- Relationship requires some analysis
- Both must be traceable to image content

**Standard Options:**
a) Both A and R are true and R is the correct explanation of A
b) Both A and R are true but R is NOT the correct explanation of A
c) A is true but R is false
d) A is false but R is true

**Complexity:**
- Select A and R that have non-obvious relationships
- Student should think about cause-effect connections
- Avoid trivially obvious pairings

**Example from Source Text:**
Source: "The gymnosperms are plants in which ovules are not enclosed by any ovary wall. After fertilisation the seeds remain exposed and therefore these plants are called naked seeded plants."
↓
Assertion (A): Gymnosperms are known as naked seeded plants.
Reason (R): Their ovules are not enclosed by an ovary wall.
Answer: A (Both A and R are true and R is the correct explanation of A)

**Why this is MEDIUM:** Student must understand that "naked seeds" = "seeds exposed" = "ovules not enclosed by ovary wall". Requires understanding the terminology connection, not just recalling two separate facts."""

AR_HARD_RULES = """## ASSERTION-REASON - HARD LEVEL

**Question Format:** Complex Assertion-Reason questions

**How to Identify HARD A-R Questions:**
- Both A and R are TRUE but R does NOT explain A (Answer: B)
- OR A and R are from COMPLETELY different sections of the text
- Requires CRITICAL ANALYSIS to determine if R actually explains A
- R may be scientifically RELATED to A but not the CAUSE/EXPLANATION
- Student must distinguish between "related facts" vs "cause-effect relationship"
- The trap: Both statements seem connected but R describes a DIFFERENT aspect

**Rules:**
- Assertion (A): Paraphrase combining 2+ lines from image
- Reason (R): Separate statement from different part of image
- Relationship requires deep analysis
- Correct answer should not be immediately obvious

**Standard Options:**
a) Both A and R are true and R is the correct explanation of A
b) Both A and R are true but R is NOT the correct explanation of A
c) A is true but R is false
d) A is false but R is true

**Complexity Requirements:**
- A and R should be from different sections of image
- Relationship should require careful reasoning
- Include cases where R is scientifically related but not the explanation

**Example from Source Text:**
Source 1: "In pteridophytes the main plant is a sporophyte which is differentiated into true root, stem and leaves"
Source 2: "The spores germinate to form gametophytes which require cool, damp places to grow"
Source 3: "Water is required for transfer of male gametes"
↓
Assertion (A): In Pteridophytes, the sporophyte is the dominant and independent plant body.
Reason (R): The gametophyte is small, short-lived and requires moist conditions for fertilisation.
Answer: B (Both A and R are true but R is NOT the correct explanation of A)

**Why this is HARD:** Both statements are TRUE. They are RELATED (both about pteridophyte life cycle). BUT R describes gametophyte characteristics - it does NOT explain WHY sporophyte is dominant. The sporophyte is dominant because it has true roots, stems, leaves and vascular tissue - NOT because the gametophyte is small. Student must analyze whether R actually CAUSES/EXPLAINS A."""


# ============================================================
# MATCH THE COLUMN PROMPTS
# ============================================================

MTC_EASY_RULES = """## MATCH THE COLUMN - EASY LEVEL

**Question Format:** Simple matching with 3-4 pairs

**How to Identify EASY Match the Column:**
- Each match is a SINGLE, DIRECT characteristic stated in the text
- Matching is ONE-TO-ONE with no ambiguity
- Characteristics are UNIQUE to each group (no overlap)
- Student only needs to recall which characteristic belongs to which group
- Terms in Column B are simple, well-known descriptors

**Rules:**
- Use 3-4 pairs maximum
- Pairs must be EXPLICITLY stated in image
- Relationships should be direct (X is Y, A causes B)
- No inference required

**TABLE FORMAT (MANDATORY - USE LaTeX):**
Use LaTeX tabular format for tables:
\\begin{{tabular}}{{|c|c|}}
\\hline
Column A & Column B \\\\
\\hline
1. Item & a. Match \\\\
2. Item & b. Match \\\\
\\hline
\\end{{tabular}}

**Options Format:**
a) 1-a, 2-b, 3-c
b) 1-b, 2-c, 3-a
c) 1-c, 2-a, 3-b
d) 1-a, 2-c, 3-b

**Example from Source Text:**
Source: "Algae are... simple, thalloid" | "Bryophytes... amphibians of plant kingdom" | "Gymnosperms... naked seeded plants" | "Angiosperms... seeds enclosed in fruits"
↓
| Column 1 | Column 2 |
|----------|----------|
| A. Algae | 1. Naked seeds |
| B. Bryophytes | 2. Amphibian of Plant Kingdom |
| C. Gymnosperms | 3. Thalloid body |
| D. Angiosperms | 4. Seeds enclosed in fruits |

Answer: A-3, B-2, C-1, D-4

**Why this is EASY:** Each characteristic is directly stated for each group. No overlap - "thalloid" only applies to algae, "naked seeds" only to gymnosperms, etc. Simple recall task."""

MTC_MEDIUM_RULES = """## MATCH THE COLUMN - MEDIUM LEVEL

**Question Format:** Intermediate matching with 4-5 pairs

**How to Identify MEDIUM Match the Column:**
- Matching requires understanding SPECIFIC characteristics or PROCESSES
- Some characteristics may SEEM to apply to multiple groups (but don't)
- Column B contains more TECHNICAL terms or processes
- Student must know specific details, not just general characteristics
- May include sub-classifications (like Chlorophyceae instead of just Algae)

**Rules:**
- Use 4-5 pairs
- Pairs from image content
- Some pairs may require combining information
- All elements must be from the image

**TABLE FORMAT (MANDATORY - USE LaTeX):**
Use LaTeX tabular format for tables:
\\begin{{tabular}}{{|c|c|}}
\\hline
Column A & Column B \\\\
\\hline
1. Item & a. Match \\\\
2. Item & b. Match \\\\
\\hline
\\end{{tabular}}

**Options Format:**
a) 1-a, 2-b, 3-c, 4-d
b) 1-b, 2-a, 3-d, 4-c
c) 1-c, 2-d, 3-a, 4-b
d) 1-d, 2-c, 3-b, 4-a

**Complexity:**
- Include related but distinct concepts as distractors
- Shuffled options should be plausible at first glance

**Example from Source Text:**
Source 1: "Algae usually reproduce vegetatively by fragmentation, asexually by... spores"
Source 2: "Bryophytes... are dependent on water for sexual reproduction"
Source 3: "Pteridophytes... possess well-differentiated vascular tissues"
Source 4: "Gymnosperms... naked seeded plants"
↓
| Column 1 | Column 2 |
|----------|----------|
| A. Chlorophyceae | 1. Naked seeded plants |
| B. Bryophytes | 2. Fragmentation and spores |
| C. Pteridophytes | 3. Dependent on water for sexual reproduction |
| D. Gymnosperms | 4. Well differentiated vascular tissues |

Answer: A-2, B-3, C-4, D-1

**Why this is MEDIUM:** Uses sub-class "Chlorophyceae" (a type of algae). "Water dependency" could confuse students (pteridophytes also need water but have vascular tissues). Requires knowing specific reproductive/structural characteristics of each group."""

MTC_HARD_RULES = """## MATCH THE COLUMN - HARD LEVEL

**Question Format:** Complex matching with 5+ pairs

**How to Identify HARD Match the Column:**
- Column A contains CONCEPTUAL statements requiring COMPARATIVE understanding
- Student must understand LIFE CYCLES and compare across plant groups
- Some characteristics may apply to MULTIPLE groups but question asks for SPECIFIC one
- Requires understanding what makes each group UNIQUE vs what is SHARED
- Column A items are STATEMENTS/CONCEPTS, not just simple terms
- Student must analyze which group the statement BEST describes

**Rules:**
- Use 5 or more pairs if available in image
- Pairs may involve multi-step relationships
- Distractors should be closely related concepts
- Maximum challenge within image content

**TABLE FORMAT (MANDATORY - USE LaTeX):**
Use LaTeX tabular format for tables:
\\begin{{tabular}}{{|c|c|}}
\\hline
Column A & Column B \\\\
\\hline
1. Item & a. Match \\\\
2. Item & b. Match \\\\
\\hline
\\end{{tabular}}

**Options Format:**
a) 1-a, 2-b, 3-c, 4-d, 5-e
b) 1-b, 2-c, 3-d, 4-e, 5-a
c) 1-c, 2-d, 3-e, 4-a, 5-b
d) 1-d, 2-e, 3-a, 4-b, 5-c

**Complexity Requirements:**
- Use maximum pairs available from image
- Column B items should be similar enough to cause confusion
- Require careful reading of image to match correctly

**Example from Source Text:**
Source 1: "Main plant body of a bryophyte is gametophyte"
Source 2: "In algae... The spores germinate to form gametophytes"
Source 3: "In gymnosperms... seeds remain exposed"
Source 4: "In pteridophytes... Water is required for transfer of male gametes... These organs possess well-differentiated vascular tissues"
↓
| Column 1 | Column 2 |
|----------|----------|
| A. Main plant body is Gametophyte | 1. Pteridophytes |
| B. Spores germinate to form gametophyte | 2. Bryophytes |
| C. Seeds remain exposed after fertilisation | 3. Gymnosperms |
| D. Water required for fertilisation despite vascular tissues | 4. Algae |

Answer: A-2, B-4, C-3, D-1

**Why this is HARD:**
- "Spores germinate to form gametophyte" is true for multiple groups but specifically asked for algae
- "Water required for fertilisation" applies to bryophytes AND pteridophytes, but "despite vascular tissues" makes it specific to pteridophytes
- Student must understand the DISTINGUISHING feature, not just know that a characteristic exists
- Requires comparative analysis across all plant groups"""


# ============================================================
# OUTPUT SCHEMAS
# ============================================================

MCQ_OUTPUT_SCHEMA = """{{
      "question_id": 1,
      "question_type": "MCQ",
      "question_text": "[Question - use LaTeX: $H_2O$, $CO_2$, $\\\\alpha$, $\\\\beta$ etc.]",
      "options": {{
        "a": "[Option with LaTeX notation where needed]",
        "b": "[Option with LaTeX notation where needed]",
        "c": "[Option with LaTeX notation where needed]",
        "d": "[Option or 'None of these']"
      }},
      "correct_answer": "a",
      "explanation": {{
        "a": "Correct: [Scientific explanation using LaTeX for formulas like $H_2O$, $\\\\alpha$]",
        "b": "Incorrect: [Reason why wrong with LaTeX notation]",
        "c": "Incorrect: [Reason why wrong with LaTeX notation]",
        "d": "Incorrect: [Reason why wrong with LaTeX notation]"
      }}
    }}"""

AR_OUTPUT_SCHEMA = """{{
      "question_id": 1,
      "question_type": "ASSERTION_REASON",
      "question_text": "Assertion (A): [Statement with LaTeX: $H_2O$, $\\\\alpha$]\\n\\nReason (R): [Statement with LaTeX notation]",
      "options": {{
        "a": "Both A and R are true and R is the correct explanation of A",
        "b": "Both A and R are true but R is NOT the correct explanation of A",
        "c": "A is true but R is false",
        "d": "A is false but R is true"
      }},
      "correct_answer": "a/b/c/d",
      "explanation": {{
        "a": "[A is true because..., R is true because..., use LaTeX for formulas]",
        "b": "[Explanation with LaTeX notation]",
        "c": "[Explanation with LaTeX notation]",
        "d": "[Explanation with LaTeX notation]"
      }}
    }}"""

MTC_OUTPUT_SCHEMA = """{{
      "question_id": 1,
      "question_type": "MATCH_THE_COLUMN",
      "question_text": "Match the following:\\n\\n\\\\begin{{tabular}}{{|l|l|}}\\n\\\\hline\\nColumn A & Column B \\\\\\\\\\n\\\\hline\\n1. [Item with $\\\\alpha$, $H_2O$] & a. [Item] \\\\\\\\\\n2. [Item] & b. [Item] \\\\\\\\\\n3. [Item] & c. [Item] \\\\\\\\\\n4. [Item] & d. [Item] \\\\\\\\\\n\\\\hline\\n\\\\end{{tabular}}",
      "options": {{
        "a": "1-a, 2-b, 3-c, 4-d",
        "b": "1-b, 2-a, 3-d, 4-c",
        "c": "1-c, 2-d, 3-a, 4-b",
        "d": "1-d, 2-c, 3-b, 4-a"
      }},
      "correct_answer": "a",
      "explanation": {{
        "a": "Correct: 1 matches a because..., 2 matches b because... [use LaTeX for formulas]",
        "b": "Incorrect: [Which pairs are wrong and why, use LaTeX]",
        "c": "Incorrect: [Which pairs are wrong and why, use LaTeX]",
        "d": "Incorrect: [Which pairs are wrong and why, use LaTeX]"
      }}
    }}"""


# ============================================================
# PROMPT CONFIGURATION DICTIONARY
# ============================================================

PROMPTS_CONFIG = {
    # MCQ Prompts
    ("mcq", "easy"): {
        "rules": MCQ_EASY_RULES,
        "output_schema": MCQ_OUTPUT_SCHEMA,
        "description": "Simple direct factual MCQs"
    },
    ("mcq", "medium"): {
        "rules": MCQ_MEDIUM_RULES,
        "output_schema": MCQ_OUTPUT_SCHEMA,
        "description": "Comprehension-based MCQs"
    },
    ("mcq", "hard"): {
        "rules": MCQ_HARD_RULES,
        "output_schema": MCQ_OUTPUT_SCHEMA,
        "description": "Complex analytical MCQs"
    },

    # Assertion-Reason Prompts
    ("assertion_reason", "easy"): {
        "rules": AR_EASY_RULES,
        "output_schema": AR_OUTPUT_SCHEMA,
        "description": "Simple A-R with obvious relationships"
    },
    ("assertion_reason", "medium"): {
        "rules": AR_MEDIUM_RULES,
        "output_schema": AR_OUTPUT_SCHEMA,
        "description": "Intermediate A-R requiring analysis"
    },
    ("assertion_reason", "hard"): {
        "rules": AR_HARD_RULES,
        "output_schema": AR_OUTPUT_SCHEMA,
        "description": "Complex A-R with non-obvious relationships"
    },

    # Match the Column Prompts
    ("match_the_column", "easy"): {
        "rules": MTC_EASY_RULES,
        "output_schema": MTC_OUTPUT_SCHEMA,
        "description": "Simple matching with 3-4 pairs"
    },
    ("match_the_column", "medium"): {
        "rules": MTC_MEDIUM_RULES,
        "output_schema": MTC_OUTPUT_SCHEMA,
        "description": "Intermediate matching with 4-5 pairs"
    },
    ("match_the_column", "hard"): {
        "rules": MTC_HARD_RULES,
        "output_schema": MTC_OUTPUT_SCHEMA,
        "description": "Complex matching with 5+ pairs"
    },
}


def get_prompt(question_type: str, difficulty: str, subject: str, question_count: int) -> str:
    """
    Get the formatted prompt for a specific question type and difficulty.

    Args:
        question_type: 'mcq', 'assertion_reason', or 'match_the_column'
        difficulty: 'easy', 'medium', or 'hard'
        subject: Subject name (e.g., 'biology')
        question_count: Number of questions to generate

    Returns:
        Formatted prompt string
    """
    key = (question_type.lower(), difficulty.lower())

    if key not in PROMPTS_CONFIG:
        raise ValueError(f"Invalid combination: {question_type} + {difficulty}")

    config = PROMPTS_CONFIG[key]

    prompt = BASE_TEMPLATE.format(
        subject=subject,
        question_count=question_count,
        difficulty=difficulty,
        question_type=question_type,
        question_type_rules=config["rules"],
        output_schema=config["output_schema"]
    )

    return prompt


def get_all_prompt_keys() -> list:
    """Get all available prompt configuration keys."""
    return list(PROMPTS_CONFIG.keys())


def get_prompt_description(question_type: str, difficulty: str) -> str:
    """Get description for a prompt configuration."""
    key = (question_type.lower(), difficulty.lower())
    if key in PROMPTS_CONFIG:
        return PROMPTS_CONFIG[key]["description"]
    return "Unknown configuration"
