"""Rough token counts, for fitting prompts into a model's context.

Every model splits text into tokens its own way, so an exact count would need
each model's tokenizer. An estimate from the text length is enough to decide
what fits, as long as it errs on the high side: about 4 characters make a token
in English, fewer in French and most other languages, so 3.5 is used, rounded up.
"""
from __future__ import annotations

import math

CHARS_PER_TOKEN = 3.5
MESSAGE_OVERHEAD = 4  # tokens each message costs on top of its text (role markers and such)


def estimate(text: str) -> int:
    """About how many tokens `text` takes (rounded up)."""
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0
