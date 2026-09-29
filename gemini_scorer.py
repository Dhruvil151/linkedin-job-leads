"""
Gemini-powered post scoring via HTTP.

Sends LinkedIn posts + resume to Gemini and asks it to:
  1. Classify each post: "hiring" | "seeker" | "other"
  2. Score hiring posts 0-10 against the resume
  3. Return structured JSON

All posts are preserved in results.json — nothing is dropped.
Hiring posts are ranked first (by score desc), seeker/other last.
"""

import json
import logging
import re
import time
from typing import List

import requests

import config

logger = logging.getLogger(__name__)

RESULTS_FILE = "results.json"
POSTS_FILE   = "posts.json"

_SYSTEM_PROMPT = """You are a job-matching assistant helping a software developer find relevant hiring posts on LinkedIn.

Your job for each post:

1. CLASSIFY as one of:
   - "hiring"  → a company, recruiter, or HR person is advertising a job opening
   - "seeker"  → an individual is looking for a job, OR someone is posting on behalf of a friend/colleague who is looking for work (e.g. "help my brother find a job", "open to work", "I am currently seeking")
   - "other"   → unrelated content

2. SCORE (only for "hiring" posts, 0 for everything else):
   Score 0-10 how well the job requirements match the candidate's resume.
   - 9-10: Perfect match — same tech stack, matching experience level, relevant role
   - 7-8 : Strong match — core skills align, minor gaps
   - 4-6 : Partial match — some overlap but significant differences
   - 1-3 : Weak match — one or two shared skills, mostly different stack
   - 0   : No match or not a hiring post

3. EXTRACT:
   - experience_required: e.g. "0-1 years", "3-5 years", "5+ years", or "not specified"
   - key_skills: up to 5 most important skills mentioned in the post

4. FIND APPLY EMAIL:
   - apply_email: the email address explicitly mentioned for sending applications or CVs (e.g. "send resume to hr@company.com", "apply at careers@xyz.com").
   - Set to null if no such email is present. Do NOT guess or infer — only include if the post text clearly provides it for applications.

Return ONLY a raw JSON array (no markdown, no code fences). Each element:
{
  "id": "<post id>",
  "type": "hiring" | "seeker" | "other",
  "score": <integer 0-10>,
  "reason": "<one sentence explaining the score>",
  "experience_required": "<string>",
  "key_skills": ["skill1", "skill2"],
  "apply_email": "<email address or null>"
}"""


def _build_user_message(resume_text: str, posts: List[dict]) -> str:
    posts_section = ""
    for i, post in enumerate(posts, 1):
        posts_section += f"\n--- POST {i} (id: {post['id']}) ---\n{post['text']}\n"

    return f"""CANDIDATE RESUME:
{resume_text}

LINKEDIN POSTS TO EVALUATE:
{posts_section}
"""


_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class _KeyRotator:
    """Cycles through GEMINI_API_KEYS; marks keys exhausted when daily quota is hit.
    Each entry is a (key, model) tuple. Uses direct HTTP (requests) so both
    AIza... and AQ... key formats work reliably.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[str, str]] = list(config.GEMINI_API_KEYS)
        if not self._entries:
            raise ValueError("Set GEMINI_API_KEYS_JSON before scoring")
        self._exhausted: set[str] = set()
        self._idx: int = 0

    @property
    def current_key(self) -> str:
        return self._entries[self._idx][0]

    @property
    def current_model(self) -> str:
        return self._entries[self._idx][1]

    def call(self, body: dict) -> dict:
        """POST to the current key's model endpoint; returns the parsed JSON response."""
        url = f"{_API_BASE}/{self.current_model}:generateContent"
        r = requests.post(
            url,
            headers={"x-goog-api-key": self.current_key},
            json=body,
            timeout=120,
        )
        return r.status_code, r.json()

    def rotate(self) -> bool:
        self._exhausted.add(self.current_key)
        logger.warning("Key #%d quota exhausted.", self._idx + 1)
        for i, (key, _) in enumerate(self._entries):
            if key not in self._exhausted:
                self._idx = i
                logger.info("Switched to key #%d / %s", self._idx + 1, self.current_model)
                return True
        logger.error("All %d Gemini API keys are exhausted for today.", len(self._entries))
        return False


def _call_gemini(rotator: _KeyRotator, resume_text: str, posts: List[dict],
                 retries: int = 3) -> List[dict]:
    """Send a batch of posts to Gemini via direct HTTP and return parsed scoring results."""
    body = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": _build_user_message(resume_text, posts)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }

    raw = ""
    attempt = 0
    while attempt < retries:
        attempt += 1
        try:
            status, data = rotator.call(body)
        except Exception as exc:
            logger.warning("HTTP error on attempt %d (%s)", attempt, type(exc).__name__)
            time.sleep(10 * attempt)
            continue

        if status == 200:
            try:
                cand = data["candidates"][0]
                parts = cand.get("content", {}).get("parts", [])
                raw = next((p["text"] for p in parts if p.get("text")), "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                scored = json.loads(raw)
                if isinstance(scored, dict):
                    scored = list(scored.values())
                return scored
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                logger.error("Bad Gemini response: %s\nRaw: %.500s", exc, raw)
                return []

        err = data.get("error", {})
        code = err.get("code", status)
        msg  = err.get("message", "")

        if code == 429 or "RESOURCE_EXHAUSTED" in msg:
            # Check if it's a per-minute rate limit (has retry delay) or daily quota
            m = re.search(r"retry[^\d]*(\d+(?:\.\d+)?)\s*s", msg, re.I)
            if m:
                if attempt >= retries:
                    if rotator.rotate():
                        attempt = 0
                        continue
                    return []
                wait = int(float(m.group(1))) + 2
                logger.warning("Rate-limited — waiting %ds (attempt %d/%d) …", wait, attempt, retries)
                time.sleep(wait)
            else:
                # Daily quota hit — rotate key
                if rotator.rotate():
                    attempt -= 1
                else:
                    return []

        elif code == 503 or "UNAVAILABLE" in msg:
            wait = 15 * attempt
            logger.warning("Gemini 503 — retrying in %ds …", wait)
            time.sleep(wait)

        else:
            logger.error("Gemini API error %s for model %s; stopping batch", code, rotator.current_model)
            return []

    logger.error("Gemini batch failed after %d attempts.", retries)
    return []


def score_posts(resume_text: str) -> List[dict]:
    """
    Load posts.json, send all posts to Gemini in batches,
    merge scores, sort, write results.json, return sorted list.
    """
    rotator = _KeyRotator()
    logger.info("Gemini key pool: %d key(s) available.", len(config.GEMINI_API_KEYS))

    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
    except FileNotFoundError:
        logger.error("%s not found — run the scraper first.", POSTS_FILE)
        return []

    # Deduplicate by text fingerprint (first 400 chars normalised) before scoring.
    # Keeps the first occurrence; maps duplicate IDs → canonical ID so scores propagate.
    seen_fingerprints: dict = {}   # fingerprint → canonical post id
    dup_to_canonical: dict = {}    # duplicate post id → canonical post id
    unique_posts: list = []
    for p in posts:
        fp = " ".join(p.get("text", "")[:400].lower().split())
        if fp and fp in seen_fingerprints:
            dup_to_canonical[p["id"]] = seen_fingerprints[fp]
        else:
            if fp:
                seen_fingerprints[fp] = p["id"]
            unique_posts.append(p)

    dup_count = len(posts) - len(unique_posts)
    if dup_count:
        logger.info("Removed %d duplicate post(s) — scoring %d unique posts.", dup_count, len(unique_posts))

    logger.info("Sending %d posts to Gemini in batches of %d …",
                len(unique_posts), config.GEMINI_BATCH_SIZE)

    post_map = {p["id"]: p for p in posts}
    scores_map: dict = {}

    # Resume: load previously scored results so we don't re-score them
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            prev_results = json.load(f)
        for r in prev_results:
            if r.get("gemini_type", "unknown") != "unknown":
                scores_map[r["id"]] = {
                    "type": r.get("gemini_type"),
                    "score": r.get("gemini_score", 0),
                    "reason": r.get("gemini_reason", ""),
                    "experience_required": r.get("experience_required", "not specified"),
                    "key_skills": r.get("key_skills", []),
                    "apply_email": r.get("apply_email"),
                }
        if scores_map:
            logger.info("Resuming — %d post(s) already scored, skipping them.", len(scores_map))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Only send unscored posts to Gemini
    unscored = [p for p in unique_posts if p["id"] not in scores_map]
    if len(unscored) < len(unique_posts):
        logger.info("Skipping %d already-scored post(s) — sending %d to Gemini.",
                    len(unique_posts) - len(unscored), len(unscored))
    unique_posts = unscored

    batch_size = config.GEMINI_BATCH_SIZE
    batches = [unique_posts[i:i+batch_size] for i in range(0, len(unique_posts), batch_size)]

    _default_scoring = {
        "type": "unknown",
        "score": 0,
        "reason": "Not scored",
        "experience_required": "not specified",
        "key_skills": [],
        "apply_email": None,
    }

    def _flush_results() -> list:
        """Merge current scores_map into results_list and write results.json."""
        # Propagate scores from canonical post to its duplicates
        for dup_id, canon_id in dup_to_canonical.items():
            if canon_id in scores_map:
                scores_map[dup_id] = scores_map[canon_id]
        out = []
        for post in posts:
            pid = post["id"]
            scoring = scores_map.get(pid, _default_scoring)
            out.append({
                **post,
                "gemini_type":         scoring.get("type", "unknown"),
                "gemini_score":        scoring.get("score", 0),
                "gemini_reason":       scoring.get("reason", ""),
                "experience_required": scoring.get("experience_required", "not specified"),
                "key_skills":          scoring.get("key_skills", []),
                "apply_email":         scoring.get("apply_email") or None,
            })
        type_order = {"hiring": 0, "seeker": 1, "other": 2, "unknown": 3}
        out.sort(key=lambda p: (type_order.get(p["gemini_type"], 3), -p["gemini_score"]))
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return out

    for batch_num, batch in enumerate(batches, 1):
        logger.info("Batch %d/%d (%d posts) …", batch_num, len(batches), len(batch))
        results = _call_gemini(rotator, resume_text, batch)

        for item in results:
            pid = item.get("id", "")
            if pid in post_map:
                scores_map[pid] = item
            else:
                # Gemini may shorten the id — try partial match
                for known_id in post_map:
                    if pid and (pid in known_id or known_id in pid):
                        scores_map[known_id] = item
                        break

        # Save incrementally so resume works if interrupted
        _flush_results()

        if batch_num < len(batches):
            time.sleep(1)

    # Final merge with duplicates propagated
    results_list = _flush_results()

    hiring = [r for r in results_list if r["gemini_type"] == "hiring"]
    seeker = [r for r in results_list if r["gemini_type"] == "seeker"]
    other  = [r for r in results_list if r["gemini_type"] not in ("hiring", "seeker")]

    logger.info(
        "Done — %d hiring | %d seeker | %d other | Top score: %s/10 | Saved to %s",
        len(hiring), len(seeker), len(other),
        hiring[0]["gemini_score"] if hiring else "—",
        RESULTS_FILE,
    )
    return results_list
