"""
Enums shared across all DynamoDB tables and API/worker code.

DynamoDB has no native enum type — these are the validation layer.
Values are stored as plain strings; validate against these classes
before every write (and at the API boundary) so invalid values are
rejected early instead of silently persisted.
"""

from enum import Enum


class Subject(str, Enum):
    BIOLOGY = "biology"
    CHEMISTRY = "chemistry"


class Medium(str, Enum):
    ENGLISH = "english"
    HINDI = "hindi"


class FileType(str, Enum):
    IMAGE = "image"
    PDF = "pdf"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class QuestionType(str, Enum):
    """Per-question value stored on each NeetTestGenerator_Test question object."""
    MCQ = "MCQ"
    ASSERTION_REASON = "ASSERTION_REASON"
    MATCH_THE_COLUMN = "MATCH_THE_COLUMN"


class QuestionTypeInput(str, Enum):
    """Lowercase value used in API requests / Jobs-table components — matches
    the prompt-selection convention (prompts/<subject>/<language>/<type>/)."""
    MCQ = "mcq"
    ASSERTION_REASON = "assertion_reason"
    MATCH_THE_COLUMN = "match_the_column"


class JobStatus(str, Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In progress"
    DONE = "Done"
    FAILED = "Failed"
    PARTIALLY_FAILED = "Partially Failed"


class AnswerStatus(str, Enum):
    PENDING = "Pending"
    PROCESSING = "Processing"
    DONE = "Done"
    FAILED = "Failed"


class BatchStatus(str, Enum):
    PENDING = "Pending"
    PROCESSING = "Processing"
    DONE = "Done"
    FAILED = "Failed"


def validate(enum_cls, value: str, field_name: str) -> str:
    """Raise ValueError with a clear message if value isn't a valid member; else return it."""
    try:
        enum_cls(value)
    except ValueError:
        allowed = [m.value for m in enum_cls]
        raise ValueError(f"Invalid {field_name} '{value}'. Allowed: {allowed}")
    return value


def validate_list(enum_cls, values: list, field_name: str) -> list:
    """Same as validate(), applied to every item in a list. Returns the list unchanged."""
    for v in values:
        validate(enum_cls, v, field_name)
    return values
