"""
Email extraction and filtering utilities.

Uses a robust regex to pull email-like patterns from raw text and then
applies heuristics to discard system / generic addresses that are unlikely
to be HR contacts.
"""

import re
from typing import List

# ── Robust email regex ──────────────────────────────────────────────────────
# Branch 1: obfuscated "at" with either a "dot" keyword or a literal dot.
#   Handles: "user at domain dot com", "user [at] domain.com", etc.
# Branch 2: standard email with @.
_EMAIL_RE = re.compile(
    r"""
    # Branch 1 — obfuscated "at" (dot keyword OR literal dot)
    [a-zA-Z0-9._%+\-]+                     # local part
    \s*[\[(]?\s*at\s*[\])]?\s*             # [at] / (at) / at
    [a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)*   # domain labels
    (?:
        \s*[\[(]?\s*dot\s*[\])]?\s*        # dot keyword: [dot] / (dot) / dot
        |
        \.                                  # literal dot
    )
    [a-zA-Z]{2,}                           # TLD
    |
    # Branch 2 — standard email
    [a-zA-Z0-9._%+\-]+                     # local part
    @
    [a-zA-Z0-9.\-]+                        # domain
    \.
    [a-zA-Z]{2,}                           # TLD
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Final sanity check applied after normalisation — rejects malformed results
# like "@domain.com", "user@@domain.com", or "user@.com".
_VALID_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# ── Domains & patterns to IGNORE ────────────────────────────────────────────
_IGNORE_DOMAINS = {
    "example.com",
    "test.com",
    "localhost",
    "sentry.io",
    "github.com",
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "instagram.com",
}

_IGNORE_LOCAL_PREFIXES = {
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
    "webmaster",
    "support",
    "info",
    "notifications",
    "newsletter",
    "unsubscribe",
    "admin",
    "abuse",
}


def _normalise(email: str) -> str:
    """Lower-case and strip whitespace."""
    return email.strip().lower()


def _is_generic(email: str) -> bool:
    """Return True if the email looks like an automated / system address."""
    local, _, domain = email.partition("@")
    # Check exact match and subdomains (e.g. mail.linkedin.com)
    if any(domain == d or domain.endswith("." + d) for d in _IGNORE_DOMAINS):
        return True
    if local in _IGNORE_LOCAL_PREFIXES:
        return True
    return False


def extract_emails(text: str) -> List[str]:
    """
    Extract email addresses from *text*, filtering out system/generic ones.

    Returns a de-duplicated list of clean, lower-cased email strings.
    """
    raw_matches = _EMAIL_RE.findall(text)
    seen: set = set()
    results: List[str] = []
    for raw in raw_matches:
        email = _normalise(raw)
        # Normalise obfuscated separators
        email = re.sub(r"\s*[\[(]?\s*at\s*[\])]?\s*", "@", email)
        email = re.sub(r"\s*[\[(]?\s*dot\s*[\])]?\s*", ".", email)
        # Reject anything that doesn't look like a well-formed email
        if not _VALID_EMAIL_RE.match(email):
            continue
        if _is_generic(email):
            continue
        if email not in seen:
            seen.add(email)
            results.append(email)
    return results
