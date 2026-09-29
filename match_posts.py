"""
Stage 3 — Score every post against resume skills and write results.json.

Scoring formula (all components 0–1, final score 0–1):
  skill_score      = weighted skill overlap (core skills weighted higher)
  experience_score = how well the post's required experience fits the user's level
  recency_score    = how recent the post is (today=1.0, 30 days ago≈0.7)

  final_score = 0.60 * skill_score
              + 0.25 * experience_score
              + 0.15 * recency_score

ALL posts are written to results.json — nothing is dropped.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

POSTS_FILE   = "posts.json"
RESULTS_FILE = "results.json"

# ── Core skill groups with weights ────────────────────────────────────────────
# Higher weight = this skill matters more in the final score.
SKILL_GROUPS = {
    "mern_core": {
        "skills": {
            "mongodb", "express", "express.js", "react", "react.js", "node.js",
        },
        "weight": 3.0,
    },
    "backend": {
        "skills": {
            "python", "django", "fastapi", "postgresql", "mysql", "redis",
            "rest", "restful", "api", "sql", "microservices", "kafka",
            "rabbitmq", "graphql", "spring", "java", "php", "laravel",
        },
        "weight": 2.0,
    },
    "frontend": {
        "skills": {
            "typescript", "javascript", "redux", "next.js", "nextjs",
            "vue", "angular", "html", "css", "sass", "tailwind", "ui",
            "bootstrap", "webpack", "vite",
        },
        "weight": 2.0,
    },
    "devops": {
        "skills": {
            "docker", "aws", "kubernetes", "k8s", "ci/cd", "github actions",
            "terraform", "ansible", "linux", "ecs", "lambda", "sqs", "rds",
            "gcp", "azure", "nginx",
        },
        "weight": 1.5,
    },
    "tools": {
        "skills": {
            "git", "github", "gitlab", "elasticsearch", "tdd", "agile",
            "scrum", "jira", "jwt", "oauth", "websocket",
        },
        "weight": 1.0,
    },
}

# Build a flat skill → weight lookup
_SKILL_WEIGHT: dict = {}
for _group in SKILL_GROUPS.values():
    for _s in _group["skills"]:
        _SKILL_WEIGHT[_s] = _group["weight"]


def _get_weight(skill: str) -> float:
    """Return the weight for a skill (default 1.0 if not in any group)."""
    return _SKILL_WEIGHT.get(skill, 1.0)


# ── Skill scoring ─────────────────────────────────────────────────────────────

def _score_skills(post_text: str, skills: List[str]) -> Tuple[float, List[str]]:
    """
    Weighted skill overlap score.
    Returns (score 0-1, list of matched skills).
    """
    if not skills:
        return 0.0, []

    lower_text = post_text.lower()
    matched = []
    total_weight = sum(_get_weight(s) for s in skills)

    for skill in skills:
        pattern = r"\b" + re.escape(skill) + r"\b"
        if re.search(pattern, lower_text):
            matched.append(skill)

    matched_weight = sum(_get_weight(s) for s in matched)
    score = round(matched_weight / total_weight, 4) if total_weight else 0.0
    return score, matched


# ── Experience scoring ────────────────────────────────────────────────────────

_EXP_PATTERNS = [
    re.compile(r'(\d+)\s*[-–to]+\s*(\d+)\s*years?', re.IGNORECASE),   # 2-5 years
    re.compile(r'(\d+)\+\s*years?\s+(?:of\s+)?experience', re.IGNORECASE),  # 3+ years exp
    re.compile(r'experience\s+(?:of\s+)?(\d+)\+?\s*years?', re.IGNORECASE), # exp of 3 years
    re.compile(r'(\d+)\s*years?\s+(?:of\s+)?experience', re.IGNORECASE),    # 3 years exp
    re.compile(r'minimum\s+(\d+)\s*years?', re.IGNORECASE),
    re.compile(r'atleast\s+(\d+)\s*years?', re.IGNORECASE),
    re.compile(r'at\s+least\s+(\d+)\s*years?', re.IGNORECASE),
]


def _extract_experience_required(text: str) -> Optional[Tuple[int, int]]:
    """
    Return (min_years, max_years) required by the post, or None if not found.
    For "3+ years" → (3, 99).  For "2-5 years" → (2, 5). For "3 years" → (3, 3).
    """
    for pat in _EXP_PATTERNS:
        m = pat.search(text)
        if m:
            groups = [g for g in m.groups() if g is not None]
            if len(groups) == 2:
                lo, hi = int(groups[0]), int(groups[1])
                return (min(lo, hi), max(lo, hi))
            elif len(groups) == 1:
                val = int(groups[0])
                # "X+ years" pattern — treat upper bound as open
                if "+" in m.group(0):
                    return (val, 99)
                return (val, val)
    return None


def _score_experience(post_text: str, user_years: int) -> float:
    """
    Compare post's required experience range with the user's experience.
    Returns a score 0–1:
      1.0 — user fits perfectly in the range
      0.8 — user is slightly above (overqualified by 1-2 years) — still OK
      0.6 — user is above range by more than 2 years (overqualified)
      0.7 — user is 1 year below minimum
      0.4 — user is 2+ years below minimum (underqualified)
      0.5 — no experience info in post (neutral)
    """
    req = _extract_experience_required(post_text)
    if req is None:
        return 0.5  # neutral — no info

    lo, hi = req

    if lo <= user_years <= hi:
        return 1.0                          # perfect fit
    if user_years > hi and hi != 99:
        gap = user_years - hi
        return 0.8 if gap <= 2 else 0.6    # overqualified
    if user_years < lo:
        gap = lo - user_years
        return 0.7 if gap == 1 else 0.4    # underqualified

    return 0.5


# ── Recency scoring ───────────────────────────────────────────────────────────

def _score_recency(scraped_at: str, max_days: int = 30) -> float:
    """
    Score based on how recently the post was scraped.
    Today → 1.0, max_days ago → 0.7  (linear decay, floor at 0.7).
    """
    try:
        ts = datetime.fromisoformat(scraped_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        score = 1.0 - (min(age_days, max_days) / max_days) * 0.3
        return round(score, 4)
    except Exception:
        return 0.7  # fallback if timestamp is missing/malformed


# ── Main ranking function ─────────────────────────────────────────────────────

def match_and_rank(skills: List[str], user_years: int = 0) -> List[dict]:
    """
    Load posts.json, score every post, sort descending by final_score,
    write results.json, and return the sorted list.

    Args:
        skills:     list of skills extracted from resume
        user_years: user's total years of experience (from resume)
    """
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
    except FileNotFoundError:
        logger.error("%s not found — run the scraper first.", POSTS_FILE)
        return []
    except json.JSONDecodeError as exc:
        logger.error("Could not parse %s: %s", POSTS_FILE, exc)
        return []

    logger.info(
        "Scoring %d posts | %d skills | user_experience=%d years",
        len(posts), len(skills), user_years,
    )

    results = []
    for post in posts:
        text = post.get("text", "")

        skill_score, matched = _score_skills(text, skills)
        exp_score            = _score_experience(text, user_years)
        recency_score        = _score_recency(post.get("scraped_at", ""))

        exp_req = _extract_experience_required(text)

        final_score = round(
            0.60 * skill_score
            + 0.25 * exp_score
            + 0.15 * recency_score,
            4,
        )

        results.append({
            **post,
            "final_score":     final_score,
            "skill_score":     skill_score,
            "experience_score": exp_score,
            "recency_score":   recency_score,
            "matched_skills":  matched,
            "matched_count":   len(matched),
            "experience_required": f"{exp_req[0]}-{exp_req[1]}" if exp_req else "not specified",
        })

    results.sort(key=lambda p: p["final_score"], reverse=True)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    matched_any = [r for r in results if r["skill_score"] > 0]
    logger.info(
        "Done — %d / %d posts have skill matches. Top score: %.3f | Saved to %s",
        len(matched_any), len(results), results[0]["final_score"] if results else 0,
        RESULTS_FILE,
    )
    return results
