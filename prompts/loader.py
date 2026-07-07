"""
Generic prompt assembler for NEET Test Generator.

Every piece of prompt content lives in .txt files — no Python constants needed.

Directory layout per subject:
  prompts/{subject}/base_template.txt          — outer template wrapper (shared/English default)
  prompts/{subject}/latex_block.txt            — {latex_block} content (shared/English default)
  prompts/{subject}/difficulty_extras.txt      — injected for medium/hard non-MTC (shared/English default)
  prompts/{subject}/{language}/{type}/prompt_{difficulty}.txt
  prompts/{subject}/{language}/{type}/checklist_{difficulty}.txt

  Optional per-language override of the three boilerplate files above:
  prompts/{subject}/{language}/base_template.txt
  prompts/{subject}/{language}/latex_block.txt
  prompts/{subject}/{language}/difficulty_extras.txt
  If present, these are used instead of the shared subject-root file — this is
  how a language gets a fully-native prompt with no English boilerplate mixed
  in. If absent, the shared file is used (current behavior, unchanged).

Shared schema files:
  prompts/schemas/mcq.txt
  prompts/schemas/mcq_hard.txt
  prompts/schemas/ar.txt
  prompts/schemas/mtc.txt
  prompts/schemas/mtc_hard.txt
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent
_SCHEMAS_DIR = _PROMPTS_DIR / "schemas"

_SCHEMA_MAP = {
    ("mcq",              "easy"):   "mcq",
    ("mcq",              "medium"): "mcq",
    ("mcq",              "hard"):   "mcq_hard",
    ("assertion_reason", "easy"):   "ar",
    ("assertion_reason", "medium"): "ar",
    ("assertion_reason", "hard"):   "ar",
    ("match_the_column", "easy"):   "mtc",
    ("match_the_column", "medium"): "mtc",
    ("match_the_column", "hard"):   "mtc_hard",
}


def _read(path, label):
    if not path.exists():
        raise FileNotFoundError(
            f"Required prompt file not found: {path}  ({label})"
        )
    return path.read_text(encoding="utf-8")


def _read_localized(subj_dir, language, filename, label):
    """
    Prefer a language-specific boilerplate file (e.g. hindi/base_template.txt)
    over the shared subject-root one. Falls back to the shared file so
    subjects/languages without a dedicated version keep working unchanged.
    """
    localized_path = subj_dir / language.lower() / filename
    if localized_path.exists():
        return localized_path.read_text(encoding="utf-8")
    return _read(subj_dir / filename, label)


def assemble_prompt(subject, language, question_type, difficulty,
                    subject_name, question_count):
    """
    Assemble and return the full prompt string.

    Args:
        subject:        subject folder name, e.g. 'biology'
        language:       language folder name, e.g. 'english'
        question_type:  'mcq', 'assertion_reason', or 'match_the_column'
        difficulty:     'easy', 'medium', or 'hard'
        subject_name:   display name injected into the prompt, e.g. 'Botany'
        question_count: number of questions to generate
    """
    qt = question_type.lower().strip()
    diff = difficulty.lower().strip()
    subj_dir = _PROMPTS_DIR / subject.lower()

    base_template = _read_localized(subj_dir, language, "base_template.txt", "base_template")
    latex_block   = _read_localized(subj_dir, language, "latex_block.txt",   "latex_block")

    if diff in ("medium", "hard") and qt != "match_the_column":
        difficulty_extras = _read_localized(subj_dir, language, "difficulty_extras.txt", "difficulty_extras")
    else:
        difficulty_extras = ""

    type_dir = subj_dir / language.lower() / qt
    rules    = _read(type_dir / f"prompt_{diff}.txt", f"{qt}/{diff} rules")

    checklist_path = type_dir / f"checklist_{diff}.txt"
    checklist = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else ""

    schema_key = (qt, diff)
    if schema_key not in _SCHEMA_MAP:
        raise ValueError(
            f"No schema mapping for question_type={qt!r}, difficulty={diff!r}"
        )
    output_schema = _read(
        _SCHEMAS_DIR / f"{_SCHEMA_MAP[schema_key]}.txt", "output_schema"
    )

    prompt_text = base_template.format(
        subject=subject_name,
        question_count=question_count,
        difficulty=difficulty,
        question_type=question_type,
        latex_block=latex_block,
        difficulty_extras=difficulty_extras,
        question_type_rules=rules,
        output_schema=output_schema,
        type_checklist=checklist,
    )
    return prompt_text, str(type_dir / f"prompt_{diff}.txt")
