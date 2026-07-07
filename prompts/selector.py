"""
NEET Test Generator — Dynamic Prompt Selector

Auto-discovers subjects by scanning prompts/ for metadata.json files.
To add a new subject: create prompts/{subject}/metadata.json and all prompt .txt files.
No code changes needed.

Each metadata.json must have:
  {
    "id":                     str,   e.g. "biology"
    "language":               str,   "en" or "hi"
    "display_name":           str,
    "aliases":                list,
    "supported_types":        list,
    "supported_difficulties": list
  }
"""

import json
from pathlib import Path

from prompts.loader import assemble_prompt

_PROMPTS_DIR = Path(__file__).parent

# Registry: (subject_id_or_alias, language) → metadata dict
_REGISTRY = {}


def _bootstrap():
    for meta_path in sorted(_PROMPTS_DIR.rglob("metadata.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"Could not read {meta_path}: {e}")

        required = {"id", "language", "supported_types", "supported_difficulties"}
        missing = required - meta.keys()
        if missing:
            raise RuntimeError(f"{meta_path} is missing fields: {missing}")

        language = meta.get("language", "en")
        subject_id = meta["id"].lower()

        _REGISTRY[(subject_id, language)] = meta
        for alias in meta.get("aliases", []):
            _REGISTRY[(alias.lower(), language)] = meta

        # Smoke test — verify at least one prompt file exists for this language
        lang_folder = {"en": "english", "hi": "hindi"}.get(language, language)
        sample_path = _PROMPTS_DIR / subject_id / lang_folder / "mcq" / "prompt_easy.txt"
        if not sample_path.exists():
            raise RuntimeError(
                f"Subject '{meta['id']}' ({language}) registered but "
                f"prompts/{subject_id}/{lang_folder}/mcq/prompt_easy.txt not found. "
                "Add the prompt .txt files before deploying."
            )


_bootstrap()


def get_prompt_with_path(question_type, difficulty, subject, question_count, language="en"):
    """
    Assemble and return (prompt_text, prompt_file_path) for logging.

    Discovery order:
      1. Exact match: (subject, language)
      2. English fallback if requested language not available
      3. ValueError if subject unknown
    """
    key = (subject.lower().strip(), language)
    meta = _REGISTRY.get(key)

    if not meta and language != "en":
        meta = _REGISTRY.get((subject.lower().strip(), "en"))

    if not meta:
        available = [m["id"] for m in list_subjects()]
        raise ValueError(
            f"Unknown subject: '{subject}'. Available: {available}"
        )

    lang_folder = {"en": "english", "hi": "hindi"}.get(language, language)

    return assemble_prompt(
        subject=meta["id"],
        language=lang_folder,
        question_type=question_type,
        difficulty=difficulty,
        subject_name=subject,
        question_count=question_count,
    )


def get_prompt(question_type, difficulty, subject, question_count, language="en"):
    """Return prompt string only (existing callers unchanged)."""
    text, _ = get_prompt_with_path(question_type, difficulty, subject, question_count, language)
    return text


def list_subjects():
    """Return one metadata dict per unique (id, language) pair."""
    seen = set()
    result = []
    for meta in _REGISTRY.values():
        key = (meta["id"], meta.get("language", "en"))
        if key not in seen:
            seen.add(key)
            result.append(meta)
    return result


def get_supported_subjects():
    """Return all registered (alias, language) keys."""
    return list(_REGISTRY.keys())
