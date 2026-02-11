"""
NEET Test Generator - Prompt Selector
Selects the appropriate prompt configuration based on subject
"""

from typing import Callable

# Import subject-specific prompt modules
import prompts_biology
import prompts_chemistry


# Subject to module mapping
SUBJECT_MODULES = {
    # Biology subjects
    "biology": prompts_biology,
    "botany": prompts_biology,
    "zoology": prompts_biology,
    "cell biology": prompts_biology,
    "genetics": prompts_biology,
    "ecology": prompts_biology,
    "human physiology": prompts_biology,
    "plant physiology": prompts_biology,
    "microbiology": prompts_biology,

    # Chemistry subjects
    "chemistry": prompts_chemistry,
    "organic chemistry": prompts_chemistry,
    "inorganic chemistry": prompts_chemistry,
    "physical chemistry": prompts_chemistry,
    "general chemistry": prompts_chemistry,
    "biochemistry": prompts_chemistry,
}


def get_prompt_module(subject: str):
    """
    Get the appropriate prompt module based on subject.

    Args:
        subject: Subject name (case-insensitive)

    Returns:
        The prompt module for the given subject

    Raises:
        ValueError: If subject is not recognized
    """
    subject_lower = subject.lower().strip()

    if subject_lower in SUBJECT_MODULES:
        return SUBJECT_MODULES[subject_lower]

    # Check for partial matches
    for key, module in SUBJECT_MODULES.items():
        if key in subject_lower or subject_lower in key:
            return module

    raise ValueError(
        f"Unknown subject: '{subject}'. "
        f"Supported subjects: {list(SUBJECT_MODULES.keys())}"
    )


def get_prompt(question_type: str, difficulty: str, subject: str, question_count: int) -> str:
    """
    Get the formatted prompt for a specific question type, difficulty, and subject.

    This is the main function to use - it automatically selects the right prompt
    module based on the subject.

    Args:
        question_type: 'mcq', 'assertion_reason', or 'match_the_column'
        difficulty: 'easy', 'medium', or 'hard'
        subject: Subject name (e.g., 'biology', 'chemistry', 'organic chemistry')
        question_count: Number of questions to generate

    Returns:
        Formatted prompt string

    Example:
        >>> prompt = get_prompt("mcq", "medium", "biology", 5)
        >>> prompt = get_prompt("assertion_reason", "hard", "chemistry", 3)
    """
    module = get_prompt_module(subject)
    return module.get_prompt(question_type, difficulty, subject, question_count)


def get_supported_subjects() -> list:
    """Get list of all supported subjects."""
    return list(SUBJECT_MODULES.keys())


def get_subject_category(subject: str) -> str:
    """
    Get the category (biology/chemistry) for a subject.

    Args:
        subject: Subject name

    Returns:
        'biology' or 'chemistry'
    """
    module = get_prompt_module(subject)
    if module == prompts_biology:
        return "biology"
    elif module == prompts_chemistry:
        return "chemistry"
    return "unknown"


def get_all_prompt_keys(subject: str) -> list:
    """Get all available prompt configuration keys for a subject."""
    module = get_prompt_module(subject)
    return module.get_all_prompt_keys()


def get_prompt_description(question_type: str, difficulty: str, subject: str) -> str:
    """Get description for a prompt configuration."""
    module = get_prompt_module(subject)
    return module.get_prompt_description(question_type, difficulty)


# Convenience functions
def get_biology_prompt(question_type: str, difficulty: str, question_count: int) -> str:
    """Shortcut for getting biology prompts."""
    return prompts_biology.get_prompt(question_type, difficulty, "biology", question_count)


def get_chemistry_prompt(question_type: str, difficulty: str, question_count: int) -> str:
    """Shortcut for getting chemistry prompts."""
    return prompts_chemistry.get_prompt(question_type, difficulty, "chemistry", question_count)
