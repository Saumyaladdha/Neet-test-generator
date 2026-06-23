"""
Generate the prompt that will be passed to the LLM and save it to a file.
Usage: python generate_prompt.py
"""

import os
import sys
import prompts_biology
import prompts_chemistry

CATEGORIES = {
    "1": ("mcq",             "MCQ"),
    "2": ("assertion_reason","Assertion-Reason"),
    "3": ("match_the_column","Match the Column"),
}

DIFFICULTIES = {
    "1": ("easy",   "Easy"),
    "2": ("medium", "Medium"),
    "3": ("hard",   "Hard"),
}

SUBJECTS = {
    "1": ("biology",   "Biology",   prompts_biology),
    "2": ("chemistry", "Chemistry", prompts_chemistry),
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt")


def ask(prompt_text: str, choices: dict) -> str:
    """Print numbered menu and return the selected key value (first element of tuple)."""
    print(prompt_text)
    for k, v in choices.items():
        print(f"  {k}. {v[1]}")
    while True:
        choice = input("Enter choice: ").strip()
        if choice in choices:
            return choices[choice][0]
        print(f"  Invalid — enter one of: {', '.join(choices)}")


def main():
    print("\n=== Prompt Generator ===\n")

    subject_key = ask("Select subject:", SUBJECTS)
    subject_module = SUBJECTS[next(k for k, v in SUBJECTS.items() if v[0] == subject_key)][2]

    q_type = ask("\nSelect category:", CATEGORIES)
    difficulty = ask("\nSelect difficulty:", DIFFICULTIES)

    question_count = 10  # fixed count for prompt preview

    default_name = f"{subject_key}_{q_type}_{difficulty}.txt"
    name_input = input(f"\nFile name (default: {default_name}): ").strip()
    file_name = name_input if name_input else default_name
    if not file_name.endswith(".txt"):
        file_name += ".txt"

    try:
        prompt = subject_module.get_prompt(q_type, difficulty, subject_key, question_count)
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, file_name)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    print(f"\nSaved → {output_path}")
    print(f"Size  → {len(prompt):,} chars")


if __name__ == "__main__":
    main()
