# NEET Biology Test Generator

AI-powered question generator for NEET Biology exam preparation. Generates MCQ, Assertion-Reason, and Match the Column questions from PDF content using OpenAI's Responses API (gpt-5-mini).

---

## Features

- **3 Question Types:** MCQ, Assertion-Reason, Match the Column
- **3 Difficulty Levels:** Easy, Medium, Hard
- **PDF Splitting:** PDFs > 20 pages are automatically split into 10-page chunks and processed in parallel
- **Parallel Generation:** All chunks are uploaded and generated concurrently — time ~= single chunk, not N × chunk
- **No Answer Key Output:** Questions and options only — no correct_answer or explanation fields in any question type (MCQ, AR, MTC)
- **Excel Export:** Auto-generates `.xlsx` files saved to `output/`
- **LaTeX Formatting:** Scientific notation, Greek letters, subscripts/superscripts enforced via LaTeX syntax
- **Interactive CLI:** Menu-driven interface for question type, difficulty, and count

---

## Setup

```bash
# Create virtual environment
python -m venv envi
source envi/bin/activate

# Install dependencies
pip install openai python-dotenv openpyxl pypdf

# Add your OpenAI API key
echo "OPENAI_API_KEY=your-key-here" > .env
```

---

## Usage

```bash
python pdf_token_counter.py                          # Interactive mode
python pdf_token_counter.py /path/to/file.pdf        # Direct file
python pdf_token_counter.py "https://example.com/doc.pdf"  # URL
```

The tool prompts for question type, difficulty, and count. Output is saved to `output/` as:
`{pdf_name}_{type}_{difficulty}_{count}.xlsx`

---

## Project Structure

```
test-generator/
  prompts_biology.py      # All prompt templates (BASE_TEMPLATE_COMMON + DIFFICULTY_EXTRAS + 9 rule sets)
  pdf_token_counter.py    # Main CLI tool (PDF upload, splitting, parallel generation, export)
  export_excel.py         # Excel workbook generation (openpyxl)
  app.py                  # Web app interface
  requirements.txt        # Dependencies
  input/                  # Sample PDF files for testing
  output/                 # Generated Excel files (auto-created)
```

---

## PDF Splitting (pdf_token_counter.py)

**Rule:** PDF ≤ 20 pages → single API call. PDF > 20 pages → split into 10-page chunks.

**Proportional distribution (unitary method):**
- Chunks: [10, 10, 10, 5] pages, 35 questions total
- Distribution: [14, 14, 14, 7] questions

**Parallel processing:** All chunks are uploaded and generated concurrently using `ThreadPoolExecutor`. Time taken is ~= the slowest single chunk, not the sum.

**Retry policy:** Each chunk retries up to 2 times on JSON parse failure. If a chunk still fails, generation aborts — chunks are never skipped.

**Cleanup:** Temp chunk PDF files are always deleted in a `finally` block.

---

## Prompt Architecture (prompts_biology.py)

### Template Structure

```
BASE_TEMPLATE_COMMON          ← always included (all types, all difficulties)
  └── {difficulty_extras}     ← DIFFICULTY_EXTRAS injected here for medium/hard only

DIFFICULTY_EXTRAS             ← techniques: negative phrasing, scrambling, number traps
                                 NOT included for easy level
```

### Rule Sets (9 total)

| Type | Easy | Medium | Hard |
|------|------|--------|------|
| MCQ | MCQ_EASY_RULES | MCQ_MEDIUM_RULES | MCQ_HARD_RULES |
| Assertion-Reason | AR_EASY_RULES | AR_MEDIUM_RULES | AR_HARD_RULES |
| Match the Column | MTC_EASY_RULES | MTC_MEDIUM_RULES | MTC_HARD_RULES |

### Output Schemas

All three question types output **questions and options only** — no `correct_answer`, no `explanation`:

```json
MCQ / MCQ Hard:
{ "question_id", "question_type", "question_text", "options": {a,b,c,d} }

Assertion-Reason:
{ "question_id", "question_type", "question_text", "options": {a,b,c,d} }

Match the Column:
{ "question_id", "question_type", "question_text", "options": {a,b,c,d} }
```

One option is always the correct answer — the model constructs it that way but does not label it.

---

## Changes in This Version

### 1. No Answer Key / No Explanation (all question types)

**What changed:**
- Removed `correct_answer` and `explanation` fields from `MCQ_OUTPUT_SCHEMA`, `MCQ_HARD_OUTPUT_SCHEMA`, and `MTC_OUTPUT_SCHEMA`
- Added `## NO EXPLANATIONS, NO ANSWER KEY` override section to all 9 rule sets (MCQ Easy/Medium/Hard, AR Easy/Medium/Hard, MTC Easy/Medium/Hard)
- Removed `## EXPLANATION GUIDELINES` from `BASE_TEMPLATE_COMMON` (was telling the LLM to generate explanations for all types)
- Removed "VERIFY correct_answer FIELD (HARD FAILURE)" from Rule #6 — replaced with "VERIFY INTERNALLY"
- Updated Rule #7 to explicitly list `correct_answer` and `explanation` as **banned** extra fields

**Why:** Centralised answer key management — questions go to students without answers, evaluated separately.

---

### 2. PDF Splitting + Parallel Generation (pdf_token_counter.py)

**What changed:**
- Added `pypdf` to `requirements.txt`
- New functions: `get_pdf_page_count()`, `split_pdf_into_chunks()`, `distribute_questions()`, `merge_results()`
- New function `_process_single_chunk()` — handles upload + generate + retry for one chunk
- `generate_from_chunks()` uses `ThreadPoolExecutor` to run all chunks concurrently
- Results are merged in original chunk order with sequential question IDs

---

### 3. LaTeX Notation + JSON Parse Safety (pdf_token_counter.py)

**What changed:**
- All 9 rule sections have `## CHEMICAL & MATHEMATICAL NOTATION` enforcing LaTeX (`$H_2O$`, `$\alpha$`, etc.)
- `_fix_latex_escapes()` — regex escapes unescaped LaTeX backslash commands that break JSON parsing
- `parse_json_response()` — 4-step pipeline: direct parse → fix LaTeX → markdown fencing → fix LaTeX in fencing

**Why:** LaTeX is the LLM's native notation. JSON parse failures happen when `\alpha` in a string is treated as an invalid escape — the fixer doubles the backslash before sending to `json.loads()`.

---

### 4. BASE_TEMPLATE Split (prompts_biology.py)

**What changed:**
- `BASE_TEMPLATE` split into `BASE_TEMPLATE_COMMON` + `DIFFICULTY_EXTRAS`
- `DIFFICULTY_EXTRAS` contains: negative phrasing, scrambling, number traps
- Easy prompts get `difficulty_extras=""` — they never see these techniques
- Medium/Hard prompts get the full `DIFFICULTY_EXTRAS` block

---

### 5. MCQ Easy Rules Additions (prompts_biology.py — MCQ_EASY_RULES)

**EASY-LEVEL OVERRIDE** (top of section):
> Do NOT use negative phrasing, do NOT scramble sequences, do NOT use number/count-based traps.

**"None of these" ban:**
> If insufficient distractors available, construct scientifically plausible wrong options. Do NOT use "None of these" as a fallback.

**BANNED: QUESTION ASKS X, OPTIONS ANSWER Y:**
> Options must directly answer what the question asks. If question asks "Which cell type?" → options must be cell type names, not time durations.

**BANNED: ANSWER VISIBLE IN THE QUESTION STEM:**
> The correct answer must never appear in the question text. If it does → rewrite the question.

---

### 6. MCQ Medium Rules Additions (prompts_biology.py — MCQ_MEDIUM_RULES)

Added the same two banned patterns as Easy (axis mismatch, answer in stem), plus the biographical ban — all at the top of the section for high LLM attention.

---

### 7. Language Rule (prompts_biology.py — BASE_TEMPLATE_COMMON)

Added under ABSOLUTE RESTRICTIONS:
> All questions, options, and explanations must be in English only. Even if the source content contains Hindi or bilingual text, output must be entirely in English.

---

### 8. SOURCE COMPREHENSION Rename (prompts_biology.py — BASE_TEMPLATE_COMMON)

`## IMAGE COMPREHENSION` → `## SOURCE COMPREHENSION`, all "the image" references → "the source content".

---

### 9. Biographical Ban — Expanded and Reinforced (prompts_biology.py)

**Rule #9 in BASE_TEMPLATE_COMMON** expanded to include:
- Banned biographical details list (birth, death, degrees, awards, career timeline)
- Banned metadata list (unit numbers, chapter titles, page numbers)
- Allowed scientist question patterns
- The biology test: "does the answer teach BIOLOGY?"

**Reinforced in MCQ Easy, Medium, Hard rule sections** (top of each) so the LLM sees it before generating, not buried after 300 lines.

---

## Testing Status

- [x] MCQ Easy — tested, working
- [x] MCQ Medium — tested, working
- [x] Assertion-Reason Easy — tested, working
- [ ] Assertion-Reason Medium — testing in progress
- [ ] Assertion-Reason Hard — not tested
- [ ] MCQ Hard — not tested
- [ ] Match the Column Easy/Medium/Hard — not tested

---

## Known Issues / Future Work

- [ ] Parallel generation confirmation: verify "Processing N chunks in parallel..." appears in logs for chunked PDFs
- [ ] AR Medium/Hard: biographical ban not yet reinforced in rule sections (only in BASE_TEMPLATE)
- [ ] MTC Easy/Medium/Hard: biographical ban not yet reinforced in rule sections
- [ ] MCQ Hard: biographical ban added but compressed vs Easy/Medium
- [ ] Support subjects beyond Biology (Chemistry prompts exist in prompts_chemistry.py — do not modify)
