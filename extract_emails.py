#!/usr/bin/env python3
"""
Extracts unique apply_email addresses from results.json, excluding any
emails already seen in previous runs stored in logs/.

Auto-called by main.py after scoring. Can also be run manually:
    python extract_emails.py
"""

import glob
import json
import logging
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(SCRIPT_DIR, "results.json")
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "unique_emails.txt")
MIN_MATCH_SCORE = 6.5

logger = logging.getLogger(__name__)


def _emails_from_file(path: str) -> set[str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {
            p["apply_email"].strip().lower()
            for p in data
            if p.get("apply_email") and isinstance(p["apply_email"], str)
        }
    except Exception:
        return set()


def generate_unique_emails() -> list[str]:
    """
    Returns a sorted list of new apply emails from results.json,
    excluding duplicates and emails from any previous run in logs/.
    Also writes them to unique_emails.txt.
    """
    if not os.path.exists(RESULTS_FILE):
        logger.warning("results.json not found — skipping email extraction.")
        return []

    # Collect all emails seen in past runs
    past_emails: set[str] = set()
    for log_file in glob.glob(os.path.join(LOGS_DIR, "results_*.json")):
        past_emails |= _emails_from_file(log_file)

    with open(RESULTS_FILE, encoding="utf-8") as f:
        results = json.load(f)

    seen: set[str] = set()
    emails: list[str] = []

    results = [
        r for r in results
        if r.get("gemini_type") == "hiring"
        and isinstance(r.get("gemini_score"), (int, float))
        and r["gemini_score"] > MIN_MATCH_SCORE
    ]

    for post in sorted(results, key=lambda p: p.get("gemini_score") or 0, reverse=True):
        raw = post.get("apply_email")
        if not raw or not isinstance(raw, str):
            continue
        normalized = raw.strip().lower()
        if normalized in past_emails or normalized in seen:
            continue
        seen.add(normalized)
        emails.append(raw.strip())

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(emails) + ("\n" if emails else ""))

    past_count = len(glob.glob(os.path.join(LOGS_DIR, "results_*.json")))
    logger.info(
        "unique_emails.txt — %d new email(s) | %d skipped (already in %d past run(s))",
        len(emails), len(past_emails), past_count,
    )
    return emails


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    emails = generate_unique_emails()
    print(f"\nSaved {len(emails)} unique email(s) to unique_emails.txt")
    for e in emails:
        print(f"  {e}")
