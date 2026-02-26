# NEET Biology Test Generator

AI-powered question generator for NEET Biology exam preparation. Generates MCQ, Assertion-Reason, and Match the Column questions from PDF content using OpenAI's Responses API (gpt-5-mini).

---

## Features

- **3 Question Types:** MCQ, Assertion-Reason, Match the Column
- **3 Difficulty Levels:** Easy, Medium, Hard
- **PDF Input:** Local files or URLs (up to 50 pages)
- **Excel Export:** Auto-generates `.xlsx` files with all questions, options, answers, and explanations
- **Interactive CLI:** Menu-driven interface for selecting question type, difficulty, and count
- **LaTeX Formatting:** Scientific notation, Greek letters, subscripts/superscripts in questions

---

## Setup

```bash
# Create virtual environment
python -m venv envi
source envi/bin/activate

# Install dependencies
pip install openai python-dotenv openpyxl

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

The tool will prompt you to select question type, difficulty, and count. Output is saved to `output/` as an Excel file.

---

## Project Structure

```
test-generator/
  prompts_biology.py      # All prompt templates (BASE_TEMPLATE + 9 rule sets)
  pdf_token_counter.py     # Main CLI tool (PDF upload, generation, export)
  export_excel.py          # Excel workbook generation (openpyxl)
  test_generator.py        # Legacy/alternate generator
  app.py                   # Web app interface
  input/                   # Sample PDF files for testing
  output/                  # Generated Excel files
```

---

## Bugs Fixed

### 1. PDF Switching Flow (pdf_token_counter.py)
**Problem:** The previous nested-loop structure (outer loop for PDF selection + inner loop for "generate more?") was not working -- the program would get stuck or skip steps.

**Fix:** Flattened to a single `while True` loop:
Load PDF -> Choose type -> Choose difficulty -> Choose count -> Generate -> Export Excel -> "Use another PDF? (y/n)" -> if yes, loop; if no, exit.

### 2. Wrong Answer Keys in Assertion-Reason Questions (prompts_biology.py)
**Problem:** Generated AR questions had correct statements but the `correct_answer` field pointed to the wrong option.

**Fix:** Added two-level verification:
- **BASE_TEMPLATE Rule #6:** General verification bullet requiring re-reading all options before output
- **AR_EASY_RULES:** Explicit truth-value-to-option mapping table:
  - A true + R true + R explains A -> "a"
  - A true + R true + R does NOT explain A -> "b"
  - A true + R false -> "c"
  - A false + R true -> "d"

### 3. Trivial/Biographical Questions Being Generated (prompts_biology.py)
**Problem:** Nonsensical questions like "James Dewey Watson was born on which date?", "Francis Crick completed his B.Sc. in which year?", "UNIT 4 is titled which topic?" were being generated.

**Fix:** Added Rule #9 to BASE_TEMPLATE -- absolute ban on biographical details, textbook metadata, and zero-concept-value questions. Every question must test a biological concept.

---

## Prompt Changes

### BASE_TEMPLATE (common to all question types)
- Added **Rule #9: NO TRIVIAL, BIOGRAPHICAL, OR METADATA QUESTIONS** with banned/correct examples
- Added **correct_answer verification** to Rule #6 (HARD FAILURE if answer key doesn't match actual correct option)
- Removed duplicate bullet from ABSOLUTE RESTRICTIONS
- Removed 4 duplicate 7-word rule bullets from QUESTION WRITING STYLE (already in CRITICAL RULE)
- Removed duplicate grammar line from LANGUAGE PRECISION #8
- Removed entire SELF-AUDIT section (all 10 items were recaps of earlier rules)

### MCQ_EASY_RULES
- Removed 2 duplicate distractor rules from Category B (identical to Category A)
- Token savings: 468 (7.9%)

### AR_EASY_RULES
- Removed Rephrasing Rule section (covered by BASE_TEMPLATE Rule #1)
- Removed EASY LEVEL RULES #7 and #8 (duplicated TYPE 3/4 descriptions)
- Removed entire VALIDATION CHECKLIST (all items redundant)
- Added CORRECT ANSWER VERIFICATION section with explicit truth-value mapping
- Token savings: 134 (10.2%)

### MTC_EASY_RULES
- Removed 6 redundant items from EASY LEVEL RULES (duplicated QUESTION STRUCTURE, ZERO KEYWORD OVERLAP, CATEGORICAL CONSISTENCY, NO COMMON-SENSE sections, and BASE_TEMPLATE Rule #1)
- Removed entire VALIDATION CHECKLIST (all items restated earlier sections)
- Moved unique "at least one wrong option uses distractor" rule to QUESTION STRUCTURE
- Token savings: 357 (13.5%)

### AR_MEDIUM_RULES
- Removed rephrasing rules from COGNITIVE REQUIREMENT (covered by BASE_TEMPLATE Rule #1)
- Removed 4 redundant MEDIUM LEVEL CONSTRAINTS (#1 ABSOLUTE RESTRICTIONS, #2 COGNITIVE REQUIREMENT, #8 COGNITIVE REQUIREMENT, #9 BASE_TEMPLATE Rule #1)
- Removed entire VALIDATION CHECKLIST
- Token savings: 222 (16.5%)

### MTC_MEDIUM_RULES
- Removed 10 of 12 redundant MEDIUM-LEVEL CONSTRAINTS (duplicated QUESTION STRUCTURE, DESIGN SHIFT, COGNITIVE REQUIREMENT, BASE_TEMPLATE, and detailed rule sections)
- Removed entire VALIDATION CHECKLIST
- Moved unique "at least one wrong option uses distractor" rule to QUESTION STRUCTURE
- Token savings: 460 (18.7%)

### MCQ_MEDIUM_RULES
- No changes needed -- already clean (all content is unique category definitions with examples)

### Total Token Savings

| Section | Old | New | Saved | % |
|---------|-----|-----|-------|---|
| BASE_TEMPLATE + MCQ_EASY | 5,904 | 5,436 | 468 | 7.9% |
| AR_EASY | 1,316 | 1,182 | 134 | 10.2% |
| MTC_EASY | 2,644 | 2,287 | 357 | 13.5% |
| AR_MEDIUM | 1,349 | 1,127 | 222 | 16.5% |
| MTC_MEDIUM | 2,465 | 2,005 | 460 | 18.7% |
| MCQ_MEDIUM | 2,588 | 2,588 | 0 | 0.0% |
| **Total** | **16,266** | **14,625** | **1,641** | **10.1%** |

---

## Other Changes

### Excel Export (export_excel.py)
- Output saved to dedicated `output/` folder (not project root)
- Added "Time Taken" column showing generation duration in seconds
- Filename format: `{pdf_name}_{type}_{difficulty}_{count}.xlsx`

### Grammar Rule (prompts_biology.py)
- Added LANGUAGE PRECISION rule requiring all questions, options, assertions, and reasons to be grammatically correct

---

## Changes To Be Made

### Prompt Optimisation (remaining sections)
- [ ] MCQ_HARD_RULES -- remove redundancies with BASE_TEMPLATE
- [ ] AR_HARD_RULES -- remove redundancies with BASE_TEMPLATE
- [ ] MTC_HARD_RULES -- remove redundancies with BASE_TEMPLATE

### Testing Status
- [x] MCQ Easy -- tested, working
- [x] Assertion-Reason Easy -- tested, working (answer key fix verified)
- [x] Match the Column Easy -- tested, working
- [ ] MCQ Medium -- testing in progress
- [ ] Assertion-Reason Medium -- testing in progress
- [ ] Match the Column Medium -- testing in progress
- [ ] MCQ Hard -- not tested yet
- [ ] Assertion-Reason Hard -- not tested yet
- [ ] Match the Column Hard -- not tested yet

### Future Improvements
- [ ] Add correct_answer verification mapping to AR_MEDIUM_RULES and AR_HARD_RULES (currently only in AR_EASY)
- [ ] Add support for subjects beyond Biology (Physics, Chemistry)
- [ ] Add batch processing (multiple PDFs at once)
- [ ] Add question quality scoring/validation post-generation
- [ ] Web UI for non-CLI users
