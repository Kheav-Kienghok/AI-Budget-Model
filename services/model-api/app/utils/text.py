from __future__ import annotations

import re


def clean_text(text: str) -> str:
    """Apply the same basic cleaning used during training."""

    text = str(text).lower()
    text = re.sub(r"\b(gasoline|petrol|fuel|diesel)\b", "petrol", text)
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    return text.strip()
