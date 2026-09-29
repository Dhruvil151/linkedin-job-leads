"""
Filter out job-seeker posts — keep only genuine hiring posts.

A post is classified as a JOB-SEEKER post (and removed) when it contains
strong "looking for work" signals with no corresponding "we are hiring" intent.
"""

import re
import logging
from typing import List

logger = logging.getLogger(__name__)

# ── Signals that indicate the POST AUTHOR is looking for a job ────────────────
_SEEKER_SIGNALS = [
    r"\bopen\s+to\s+work\b",
    r"#opentowork",
    r"\blooking\s+for\s+(a\s+)?(?:new\s+)?(?:job|opportunity|role|position|opening)",
    r"\bseeking\s+(a\s+)?(?:new\s+)?(?:job|opportunity|role|position|opening)",
    r"\bactively\s+(?:looking|seeking|searching)\b",
    r"\bcurrently\s+(?:looking|seeking|searching|exploring)\b",
    r"\bexploring\s+(?:new\s+)?opportunities\b",
    r"\bavailable\s+(?:for|immediately|now)\b",
    r"\bi(?:'m| am)\s+(?:currently\s+)?(?:looking|seeking|searching|available)\b",
    r"\bhelp\s+(?:my|a)\s+(?:friend|brother|sister|colleague|batchmate)\b",
    r"\bon\s+behalf\s+of\s+my\b",
    r"\bsharing\s+this\s+post\s+(?:to\s+help|for\s+my)\b",
    r"\bmy\s+friend\s+is\s+(?:currently\s+)?looking\b",
    r"\bmy\s+(?:friend|brother|sister)\s+is\s+seeking\b",
    r"\bjob\s+search\b",
    r"#jobsearch",
    r"#hireme",
    r"#lookingforjob",
    r"#jobseeker",
    r"\bplease\s+(?:refer|tag|connect|share)\s+(?:me|him|her)\b",
    r"\b(?:dm|message)\s+me\s+if\s+(?:you\s+)?(?:know|have|are)\b",
]

# ── Signals that indicate the poster is HIRING ────────────────────────────────
_HIRING_SIGNALS = [
    r"\bwe(?:'re| are)\s+hiring\b",
    r"\b(?:now\s+)?hiring\b",
    r"#hiring",
    r"\bjob\s+opening\b",
    r"\bopen\s+(?:position|role|vacancy|vacancies)\b",
    r"\bwe(?:'re| are)\s+looking\s+for\s+a\b",
    r"\blooking\s+for\s+(?:a\s+)?(?:talented|experienced|skilled|passionate)\b",
    r"\binterested\s+candidates?\b",
    r"\bapply\s+now\b",
    r"\bsend\s+(?:your\s+)?(?:resume|cv)\b",
    r"\bsend\s+(?:us\s+)?your\b",
    r"\bjoin\s+(?:our|the)\s+team\b",
    r"\bwe\s+(?:need|want)\s+(?:a|an)\b",
    r"\bposition\s+(?:available|open)\b",
    r"\bimmediate\s+(?:opening|vacancy|hiring|requirement|joiner)\b",
    r"\burgent(?:ly)?\s+(?:hiring|required|looking)\b",
    r"\bvacancy\b",
    r"\bexperience\s+required\b",
    r"\byears?\s+of\s+experience\b",
    r"\bctc\b",
    r"\blpa\b",
    r"\bnotice\s+period\b",
    r"\b(?:dm|message|mail|email|whatsapp)\s+(?:us|your|cv|resume)\b",
]

_SEEKER_RE  = [re.compile(p, re.IGNORECASE) for p in _SEEKER_SIGNALS]
_HIRING_RE  = [re.compile(p, re.IGNORECASE) for p in _HIRING_SIGNALS]


def _is_seeker_post(text: str) -> bool:
    """Return True if this post is someone looking for a job (not a hiring post)."""
    lower = text.lower()

    seeker_hits = sum(1 for r in _SEEKER_RE if r.search(lower))
    hiring_hits = sum(1 for r in _HIRING_RE if r.search(lower))

    # Clear hiring post — keep regardless of seeker language
    if hiring_hits >= 2:
        return False

    # Strong seeker signals with no real hiring intent → filter out
    if seeker_hits >= 2 and hiring_hits == 0:
        return True

    # Single seeker signal + no hiring signal → filter out
    if seeker_hits >= 1 and hiring_hits == 0:
        return True

    return False


def filter_posts(posts: List[dict]) -> List[dict]:
    """
    Remove job-seeker posts from the list.
    Returns (kept_posts, removed_count).
    """
    kept = []
    removed = 0
    for post in posts:
        if _is_seeker_post(post.get("text", "")):
            removed += 1
            logger.debug("Filtered (seeker): %s", post.get("text", "")[:80].replace("\n", " "))
        else:
            kept.append(post)

    logger.info(
        "Filter: %d posts kept, %d job-seeker posts removed.",
        len(kept), removed,
    )
    return kept
