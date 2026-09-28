"""AgentCore Platform v1.0"""

# Domain-aware protected-attribute redaction/flagging layer (CMN-C2-686 §2b).
#
# The framework's built-in S-2 PII gate (shared.security.pii_detector) targets
# structured personal-identifier patterns (email, phone, national ID, etc.) on
# a fixed set of AgentState fields — it does NOT scan resume_batch, and it is
# not bias-oriented even where it does scan. Real resume free text can carry
# protected-attribute *proxy* signals that a PII scanner has no reason to
# catch: a graduation year (age proxy), a gendered honorific tied to a name,
# a disability/accommodation disclosure, a parental-leave mention, a
# religious-affiliation org name, or a national-origin-coded phrase (visa
# status, "international student", study-abroad country).
#
# This module is a deliberate, separate, unit-testable redaction/flagging
# layer that:
#   1. Redacts/neutralizes these signals from the text handed to the
#      scoring/LLM path (never fed back into any tool_call or think prompt
#      unredacted).
#   2. Reports which categories were detected per candidate, so downstream
#      scoring (src/services/scoring.py) can raise a non-suppressible
#      human-review escalation when a protected signal coincides with a
#      borderline match (see docs/02_design.md §2b).
#
# This is a best-effort, keyword/regex heuristic layer — it supplements, it
# does not replace, human judgment. Limitations are documented in
# docs/07_operation_guide.md.

from __future__ import annotations

import re

# Ordered so redaction is deterministic regardless of dict iteration order
# across Python versions (dicts are insertion-ordered since 3.7, but keeping
# an explicit tuple documents the intended scan order for reviewers).
_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "age_proxy",
        re.compile(
            r"\b(?:class of|graduated?(?: in)?|expected graduation(?: date)?|"
            r"graduation year)\s*[:\-]?\s*((?:19|20)\d{2})\b"
            r"|\bdate of birth\b|\bDOB\b|\bborn in (?:19|20)\d{2}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "gender_proxy",
        re.compile(
            # Trailing \b after "Mr." etc. would never match — "." and the
            # following space are both non-word characters, so there is no
            # word/non-word boundary there. Anchor only on the leading \b.
            r"\b(?:Mr|Mrs|Ms)\." r"|\b(?:Miss|Sir|Madam)\b" r"|\b(?:he/him|she/her|his/him|her/hers)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "disability_disclosure",
        re.compile(
            r"\b(disabilit\w*|wheelchair(?:[- ](?:user|bound))?|"
            r"hearing[- ]impaired|visually impaired|blind(?:ness)?|"
            r"ADA accommodation|reasonable accommodation)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "parental_leave_proxy",
        re.compile(
            r"\b(maternity leave|paternity leave|parental leave|pregnan\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "religious_affiliation_proxy",
        re.compile(
            r"\b(Church of [A-Z][\w' -]*|Diocese(?: of [A-Z][\w' -]*)?|"
            r"Synagogue|Mosque|Temple(?: of [A-Z][\w' -]*)?|"
            r"Catholic (?:Charities|School|Church)|Islamic Center|"
            r"Jewish Community Center|Youth Ministry|Bible Study)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "national_origin_proxy",
        re.compile(
            r"\b(international student|visa sponsorship(?: required)?|"
            r"F-1 visa|H-1B|OPT (?:visa|status)|"
            r"studied abroad in [A-Z][\w' -]*|overseas Chinese)\b",
            re.IGNORECASE,
        ),
    ),
)


def redact_protected_attributes(text: str) -> tuple[str, list[str]]:
    """Redact protected-attribute proxy signals from resume free text.

    Returns (redacted_text, categories) where categories is the sorted list
    of distinct category codes that matched at least once. Never raises —
    absence of a match is a normal, common case (returns the text unchanged).
    """
    if not isinstance(text, str) or not text:
        return text or "", []

    redacted = text
    categories: set[str] = set()
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(redacted):
            categories.add(category)
            redacted = pattern.sub(f"[REDACTED:{category.upper()}]", redacted)

    return redacted, sorted(categories)
