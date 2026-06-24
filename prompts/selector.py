"""
NEET Test Generator - Dynamic Prompt Selector

Auto-discovers every file in prompts/subjects/ that exports a METADATA dict.
To add a new subject or language: drop a new file in subjects/ — nothing else changes.

File contract (every subject file must export):
    METADATA = {
        "id":           str,   # subject family e.g. "biology"
        "language":     str,   # "en" (default) or "hi" etc.
        "display_name": str,
        "aliases":      list,  # all strings that should route to this file
        "supported_types":        list,
        "supported_difficulties": list,
    }
    def get_prompt(question_type, difficulty, subject, question_count) -> str: ...
"""

import importlib
import pkgutil
from pathlib import Path

_REGISTRY: dict = {}


def _bootstrap():
    subjects_path = Path(__file__).parent / "subjects"
    for _, name, _ in pkgutil.iter_modules([str(subjects_path)]):
        mod = importlib.import_module(f"prompts.subjects.{name}")
        if not hasattr(mod, "METADATA"):
            continue
        meta = mod.METADATA

        # Smoke test: verify get_prompt works at startup
        try:
            result = mod.get_prompt("mcq", "easy", meta["id"], 1)
            assert isinstance(result, str) and len(result) > 100
        except Exception as e:
            raise RuntimeError(
                f"Subject '{name}' failed startup smoke test: {e}. "
                "Fix the subject file before deploying."
            )

        language = meta.get("language", "en")
        _REGISTRY[(meta["id"].lower(), language)] = mod
        for alias in meta.get("aliases", []):
            _REGISTRY[(alias.lower(), language)] = mod


_bootstrap()


def get_prompt(question_type: str, difficulty: str, subject: str,
               question_count: int, language: str = "en") -> str:
    """
    Get the formatted prompt for a subject + language.
    Falls back to English if the requested language file does not exist yet.
    """
    key = (subject.lower().strip(), language)
    mod = _REGISTRY.get(key)

    if not mod and language != "en":
        mod = _REGISTRY.get((subject.lower().strip(), "en"))

    if not mod:
        available = list_subjects()
        raise ValueError(
            f"Unknown subject: '{subject}'. "
            f"Available: {[s['id'] for s in available]}"
        )

    return mod.get_prompt(question_type, difficulty, subject, question_count)


def list_subjects() -> list:
    """Return unique subjects (one entry per id+language pair)."""
    seen = set()
    result = []
    for mod in _REGISTRY.values():
        meta = mod.METADATA
        key = (meta["id"], meta.get("language", "en"))
        if key not in seen:
            seen.add(key)
            result.append(meta)
    return result


def get_supported_subjects() -> list:
    """Return flat list of all registered (alias, language) keys."""
    return list(_REGISTRY.keys())
