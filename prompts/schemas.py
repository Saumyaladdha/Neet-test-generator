"""
Shared output JSON schemas for all question types, subjects, and languages.
Import from here in every subject file — never define schemas inline.
"""

MCQ_OUTPUT_SCHEMA = """{
      "question_id": 1,
      "question_type": "MCQ",
      "question_text": "[Question - use LaTeX: $H_2O$, $CO_2$, $\\\\textit{species name}$ etc.]",
      "options": {
        "a": "[Option with LaTeX notation where needed]",
        "b": "[Option with LaTeX notation where needed]",
        "c": "[Option with LaTeX notation where needed]",
        "d": "[Option with LaTeX notation where needed]"
      }
    }"""

MCQ_HARD_OUTPUT_SCHEMA = """CRITICAL FORMATTING RULE: Each numbered statement MUST be on its own line. Use \\n to separate the stem from statement 1, and between every pair of consecutive statements. The question_text field MUST follow this exact pattern:

"[Stem sentence ending with colon]:\\n1. [First statement.]\\n2. [Second statement.]\\n3. [Third statement.]\\n4. [Fourth statement.]\\n5. [Fifth statement.]"

BAD (all on one line — HARD FAILURE):
"Which of the following are correct? 1. Statement one. 2. Statement two. 3. Statement three. 4. Statement four. 5. Statement five."

GOOD (each statement on its own line):
"Which of the following statements about Arthropoda are correct?\\n1. The body is covered by a chitinous exoskeleton shed periodically during growth.\\n2. Respiration occurs exclusively through lungs in all arthropod classes.\\n3. The body is segmented and the appendages are jointed.\\n4. The circulatory system is of the open type with haemolymph as the circulatory fluid.\\n5. Excretion is carried out through nephridia located in each body segment."

{
      "question_id": 1,
      "question_type": "MCQ",
      "question_category": "multiple_correct | identify_incorrect | sequence_order | true_false",
      "question_text": "[Stem sentence]:\\n1. [Statement one.]\\n2. [Statement two.]\\n3. [Statement three.]\\n4. [Statement four.]\\n5. [Statement five.]",
      "options": {
        "a": "[MAX 7 WORDS - combination/sequence/T-F only]",
        "b": "[MAX 7 WORDS - combination/sequence/T-F only]",
        "c": "[MAX 7 WORDS - combination/sequence/T-F only]",
        "d": "[MAX 7 WORDS - combination/sequence/T-F only]"
      }
    }"""

AR_OUTPUT_SCHEMA = """{
      "question_id": 1,
      "question_type": "ASSERTION_REASON",
      "question_text": "Assertion (A): [Statement with LaTeX: $H_2O$, $\\\\alpha$]\\n\\nReason (R): [Statement with LaTeX notation]",
      "options": {
        "a": "Both Assertion and Reason are true and Reason is the correct explanation of Assertion",
        "b": "Both Assertion and Reason are true but Reason is NOT the correct explanation of Assertion",
        "c": "Assertion is true but Reason is false",
        "d": "Assertion is false but Reason is true"
      }
    }"""

MTC_OUTPUT_SCHEMA = """{
      "question_id": 1,
      "question_type": "MATCH_THE_COLUMN",
      "question_text": "Match the following:\\n\\n\\\\begin{tabular}{|l|l|}\\n\\\\hline\\nColumn I & Column II \\\\\\\\\\n\\\\hline\\n1. [Item] & i. [Item] \\\\\\\\\\n2. [Item] & ii. [Item] \\\\\\\\\\n3. [Item] & iii. [Item] \\\\\\\\\\n4. [Item] & iv. [Item] \\\\\\\\\\n\\\\hline\\n\\\\end{tabular}",
      "options": {
        "a": "1-iii, 2-i, 3-iv, 4-ii",
        "b": "1-iv, 2-iii, 3-ii, 4-i",
        "c": "1-i, 2-ii, 3-iii, 4-iv",
        "d": "1-ii, 2-iv, 3-i, 4-iii"
      }
    }"""

MTC_HARD_OUTPUT_SCHEMA = """IMPORTANT: Column II has 5 items (i, ii, iii, iv, v). In option values, ALWAYS use roman numerals — NEVER use letters a/b/c/d for Column II references.

{
      "question_id": 1,
      "question_type": "MATCH_THE_COLUMN",
      "question_text": "Match the following:\\n\\n\\\\begin{tabular}{|l|l|}\\n\\\\hline\\nColumn I & Column II \\\\\\\\\\n\\\\hline\\n1. [Item] & i. [Item] \\\\\\\\\\n2. [Item] & ii. [Item] \\\\\\\\\\n3. [Item] & iii. [Item] \\\\\\\\\\n4. [Item] & iv. [Item] \\\\\\\\\\n & v. [Distractor item] \\\\\\\\\\n\\\\hline\\n\\\\end{tabular}",
      "options": {
        "a": "1-iii, 2-i, 3-iv, 4-ii",
        "b": "1-ii, 2-i, 3-iv, 4-iii",
        "c": "1-iii, 2-v, 3-iv, 4-ii",
        "d": "1-iii, 2-i, 3-ii, 4-iv"
      }
    }

Option values use roman numerals ONLY: i, ii, iii, iv, v. Write "1-iii" not "1-c". Write "2-v" not "2-e"."""

