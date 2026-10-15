"""Convert a model context limit in tokens into the byte budgets of the bounded review.

The review protocol measures every invocation in UTF-8 bytes because bytes are
exact and tokenizer-independent.  Model limits are published in tokens.  This
tool gives a deliberately conservative conversion without any tokenizer
dependency: it never claims more bytes than the token limit can hold.

    python -m tools.review_budget --context-tokens 200000 --response-tokens 8000 --sample cases.json

The printed ``input_byte_budget`` and ``response_reserve_bytes`` are the values
for ``orchestrate_test_case_revision prepare-review``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Conservative upper bounds for tokens per character, by kind of text.  Real BPE
# tokenizers do better on prose; JSON punctuation and Cyrillic are the worst cases.
_ASCII_CHARS_PER_TOKEN = 3.0
_NON_ASCII_CHARS_PER_TOKEN = 1.0
_DEFAULT_SAFETY = 0.9


def estimate_tokens(text: str) -> int:
    """Upper-bound estimate of the tokens a model needs for ``text``."""
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    other_chars = len(text) - ascii_chars
    return int(ascii_chars / _ASCII_CHARS_PER_TOKEN + other_chars / _NON_ASCII_CHARS_PER_TOKEN) + 1


def bytes_per_token(sample: str) -> float:
    """UTF-8 bytes one token carries for text like ``sample`` (conservative, at least 1.0)."""
    if not sample:
        return _ASCII_CHARS_PER_TOKEN
    return max(1.0, len(sample.encode("utf-8")) / estimate_tokens(sample))


def review_budgets(context_tokens: int, response_tokens: int, sample: str = "", *, safety: float = _DEFAULT_SAFETY) -> dict[str, int | float]:
    """Byte budgets for one reviewer invocation of a model with ``context_tokens``."""
    if type(context_tokens) is not int or type(response_tokens) is not int or context_tokens <= 0 or response_tokens <= 0 or response_tokens >= context_tokens:
        raise ValueError("token limits must be positive and the response must fit in the context")
    if not 0 < safety <= 1:
        raise ValueError("safety must be in (0, 1]")
    ratio = bytes_per_token(sample)
    total = int(context_tokens * ratio * safety)
    reserve = int(response_tokens * ratio) + 1
    return {"input_byte_budget": total, "response_reserve_bytes": reserve, "bytes_per_token": round(ratio, 3),
            "estimated_sample_tokens": estimate_tokens(sample) if sample else 0, "safety": safety}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert token limits into review byte budgets (conservative, no tokenizer).")
    parser.add_argument("--context-tokens", type=int, required=True, help="Model context window in tokens.")
    parser.add_argument("--response-tokens", type=int, required=True, help="Tokens reserved for the reviewer answer.")
    parser.add_argument("--sample", type=Path, action="append", default=[], help="Representative review input (cases JSON, requirements); repeatable.")
    parser.add_argument("--safety", type=float, default=_DEFAULT_SAFETY)
    args = parser.parse_args(argv)
    try:
        sample = "".join(path.read_text(encoding="utf-8") for path in args.sample)
        result = review_budgets(args.context_tokens, args.response_tokens, sample, safety=args.safety)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
