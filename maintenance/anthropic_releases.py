"""Extract the model being introduced from Anthropic's headlines and platform notes."""

import re

FAMILY = r"(?:Opus|Sonnet|Haiku|Fable|Mythos)"
VERSION = r"(?:\d+(?:\.\d+)*|Preview)"
# Allow future explicitly Claude-branded series with numbered releases, while
# excluding the separately versioned developer tools that share the brand.
NAME = (
    rf"(?:(?:Claude\s+(?!(?:API|Code|Console|Platform|SDK)\b)[A-Za-z]+|{FAMILY})"
    rf"\s+{VERSION}|Claude\s+\d+(?:\.\d+)*(?:\s+{FAMILY})?)\b"
)
PAIR = rf"(?P<first>{NAME})(?:\s*(?:and|&|/)\s*(?P<second>{NAME}))?"
LAUNCH = (
    r"^(?:Today[, ]+)?(?:we(?:['’]ve|['’]re| are| have)?\s+(?:also\s+)?)?"
    r"(?:launch(?:ed|ing)?|releas(?:e|ed|ing)|introduc(?:e|ed|ing)|"
    r"announc(?:e|ed|ing)|meet)\s+(?:(?:the|our)\s+)?"
)
INTRO = re.compile(LAUNCH + rf"(?:new\s+|next generation of\s+)?{PAIR}", re.I)
GENERIC = re.compile(
    LAUNCH + r"(?:next generation of Claude(?=$|[.:,])|"
    r"(?:new |latest )?Claude (?:model(?:s| family)?|family of models)(?=$|[.:,])|"
    r"Claude\s+[\w-]+,\s+(?:our|a|an)\s+[^.!?]{0,70}\bmodel\b)", re.I
)
SUBJECT = re.compile(rf"^{PAIR}", re.I)
AVAILABILITY = re.compile(
    r"^(?:\s*\([^)]*\))?(?:,\s*our [^,]+,)?\s*"
    r"(?:(?:is|are)\s+)?(?:now\s+)?(?:generally\s+)?available\b", re.I
)
UPGRADE = re.compile(
    r"^(?:,\s*our [^,]+,\s*)?(?:(?:just |has )?got an upgrade|"
    r"(?:was|has been) upgraded)\b", re.I
)
COMPANION = re.compile(
    rf"(?:\balongside\s+|(?<=[.!?])\s+(?={NAME}\s*(?:\([^)]*\)\s*)?"
    rf"offers the same capabilities\b))(?P<name>{NAME})", re.I
)
PREVIEW = re.compile(
    r"(?:(?:is|are)\s+)?(?:now\s+)?(?:available\s+)?(?:in|as)\s+(?:a\s+)?"
    r"(?:(?:gated|research|limited|public)\s+)*(?:preview|beta)\b", re.I
)


def catalog_name(name):
    name = re.sub(r"^Claude \d+[：:]\s*", "Claude ", name)
    return re.sub(
        rf"^Claude (\d+(?:\.\d+)*) ({FAMILY})\b", r"Claude \2 \1", name, flags=re.I
    )


def release(text):
    """Return a canonical name and proposed stage only for a model launch claim."""
    match = INTRO.match(text)
    if not match:
        match = SUBJECT.match(text)
        if not match:
            # An explicit release can precede disclosure of a version number.
            # Return an empty model field and let the Issue retain the headline.
            return ("", "发布") if GENERIC.match(text) else None
        tail = text[match.end():].strip()
        if tail and not (AVAILABILITY.match(tail) or PREVIEW.match(tail) or UPGRADE.match(tail)):
            return None
    else:
        tail = text[match.end():].strip()
        # The version can modify a newly launched feature instead of naming
        # the new artifact ("Claude Opus 5.5 fast mode").
        if tail and tail[0].isalpha() and not re.match(
            r"(?:is|are|today|in|as|to|on|for|with|alongside)\b", tail, re.I
        ):
            return None
    names = [match["first"]]
    if match["second"]:
        names.append(match["second"])
    if companion := COMPANION.search(tail):
        names.append(companion["name"])
    names = sorted({
        re.sub(r"^Claude\s+", "", catalog_name(n.title())) for n in names
    })
    name = "Claude " + " / ".join(names)
    if UPGRADE.match(tail):
        return name, "版本更新"
    # Prices, API clients and beta features later in the same release note
    # do not define the stage of the model being launched.
    if any(n.endswith(" Preview") for n in names) or PREVIEW.match(tail):
        return name, "预览"
    if AVAILABILITY.match(tail) and re.search(
        r"\b(?:on|across|via|through|in)\s+(?:the\s+)?(?:Claude\s+)?API\b", tail, re.I
    ):
        return name, "开放 API"
    return name, "发布"
