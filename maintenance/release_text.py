"""Conservative names from publisher headlines, without shortening model variants."""

import re
import unicodedata


def normalized_text(value):
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(str.maketrans({c: "-" for c in "‐‑‒–−﹘﹣－"}))
    return " ".join(value.replace("\u200b", "").split())


PREFIXES = {
    "OpenAI": r"(?:GPT(?:[- ]|\d)|o\d+\b)",
    "Google": r"(?:Gemini|Gemma)\b",
    "DeepSeek": r"DeepSeek[- ]",
    "Meta": r"(?:Muse|Llama)\b",
    "Inception Labs": r"Mercury\b",
    "Amazon": r"(?:Amazon )?(?:Nova|Titan|Olympus)\b",
    "Cohere": r"Command\b",
}


def headline_model(title, org):
    """Use a complete naming phrase or decline to infer a canonical model.

    Unknown suffixes belong to the name. An unrecognized headline remains its
    own evidence item; it must never collapse into a guessed model family.
    """
    prefix = PREFIXES.get(org)
    if not prefix:
        return ""
    value = normalized_text(title)
    value = re.sub(r"^(?:Introducing|Meet|Announcing|Unveiling)\s+(?:our\s+|the\s+)?", "", value, flags=re.I)
    # Some headlines introduce the name after a colon (for example Muse Ember).
    if not re.match(prefix, value, re.I) and ":" in value:
        value = value.rsplit(":", 1)[1].strip()
        value = re.sub(r"^(?:Introducing|Meet|Announcing|Unveiling)\s+", "", value, flags=re.I)
    if not re.match(prefix, value, re.I):
        return ""
    value = re.split(r":|\s+—\s+|\s+(?:is|are|now)\s+", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"\s+(?:Release|Update|for developers)$", "", value, flags=re.I).strip(" .!")
    # Do not turn a multi-model announcement or an application feature into a
    # single-model event. The full headline/URL still identifies its evidence.
    if re.search(r"\b(?:and|with|for|gets?|helps?|brings?|available|benchmark|API|Code|Search)\b", value, re.I):
        return ""
    if not re.fullmatch(r"[\w.+ -]{2,100}", value):
        return ""
    return value
