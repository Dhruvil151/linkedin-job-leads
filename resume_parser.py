"""
Stage 2 helper — Extract skills and keywords from a resume PDF.

Uses pdfplumber for text extraction, then applies a curated skill list
plus simple frequency filtering to pull out relevant terms.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List

import pdfplumber

logger = logging.getLogger(__name__)

# ── Known tech skills to look for (case-insensitive) ──────────────────────────
_KNOWN_SKILLS = [
    # Languages
    "python", "javascript", "typescript", "java", "c++", "c#", "go", "rust",
    "kotlin", "swift", "php", "ruby", "scala", "r", "dart", "bash",
    # Frontend
    "react", "vue", "angular", "next.js", "nextjs", "nuxt", "svelte",
    "html", "css", "sass", "tailwind", "bootstrap", "redux", "webpack",
    "vite", "graphql", "rest", "restful",
    # Backend
    "node.js", "nodejs", "express", "fastapi", "django", "flask", "spring",
    "laravel", "rails", "nest.js", "nestjs", "asp.net",
    # MERN / MEAN stack
    "mern", "mean", "mevn", "mongodb", "mongoose",
    # Databases
    "mysql", "postgresql", "postgres", "sqlite", "redis", "elasticsearch",
    "cassandra", "dynamodb", "oracle", "firebase", "supabase",
    # Cloud & DevOps
    "aws", "azure", "gcp", "docker", "kubernetes", "k8s", "terraform",
    "ansible", "jenkins", "ci/cd", "github actions", "gitlab ci",
    "nginx", "apache", "linux",
    # Mobile
    "react native", "flutter", "android", "ios", "xamarin",
    # Data / ML
    "machine learning", "deep learning", "tensorflow", "pytorch", "keras",
    "pandas", "numpy", "scikit-learn", "sql", "spark", "hadoop",
    # Tools & practices
    "git", "github", "gitlab", "jira", "agile", "scrum", "microservices",
    "api", "websocket", "oauth", "jwt", "kafka", "rabbitmq",
    # Job titles / roles (useful for matching hiring posts)
    "full stack", "fullstack", "full-stack", "backend", "frontend",
    "software engineer", "software developer", "web developer",
    "devops", "data engineer", "data scientist", "ml engineer",
    "mern stack", "mean stack",
]

# Compile once — longest phrases first so multi-word matches take priority
_KNOWN_SKILLS_SORTED = sorted(_KNOWN_SKILLS, key=len, reverse=True)


def extract_text(pdf_path: str) -> str:
    """Return all text from a PDF file as a single string."""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n".join(text_parts)


def extract_experience_years(pdf_path: str) -> int:
    """
    Try to calculate total years of experience from the resume PDF.
    Looks for date ranges like "Jul 2022 – Present" or "2021 - 2023".
    Returns total years as an int (0 if not determinable).
    """
    raw_text = extract_text(pdf_path)
    now = datetime.now()

    # Match "Month Year – Month Year" or "Month Year – Present"
    date_range_re = re.compile(
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
        r'\s+(\d{4})\s*[-–—to]+\s*'
        r'(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})|Present|Current|Now)',
        re.IGNORECASE,
    )

    total_months = 0
    for m in date_range_re.finditer(raw_text):
        try:
            start_year = int(m.group(1))
            end_str = m.group(2)
            end_year = now.year if end_str is None else int(end_str)
            total_months += max(0, (end_year - start_year) * 12)
        except (ValueError, TypeError):
            continue

    if total_months:
        years = round(total_months / 12)
        logger.info("Estimated experience from resume: ~%d year(s)", years)
        return years

    # Fallback: look for explicit "X years of experience" in resume text
    exp_re = re.compile(r'(\d+)\+?\s*years?\s+(?:of\s+)?experience', re.IGNORECASE)
    m = exp_re.search(raw_text)
    if m:
        years = int(m.group(1))
        logger.info("Found explicit experience statement: %d year(s)", years)
        return years

    logger.warning("Could not determine experience from resume — defaulting to 0.")
    return 0


def extract_skills(pdf_path: str) -> List[str]:
    """
    Parse the resume PDF and return a deduplicated list of skills/keywords found.
    Two sources are combined:
      1. Known skill dictionary matches (reliable).
      2. Capitalized tokens that look like tech terms (heuristic for unlisted tools).
    """
    logger.info("Parsing resume: %s", pdf_path)
    raw_text = extract_text(pdf_path)
    lower_text = raw_text.lower()

    found = set()

    # Source 1 — known skill list
    for skill in _KNOWN_SKILLS_SORTED:
        pattern = r"\b" + re.escape(skill) + r"\b"
        if re.search(pattern, lower_text):
            found.add(skill)

    # Source 2 — CamelCase / ALLCAPS tokens not already covered (e.g. "Prisma", "Zod")
    # Exclude common resume noise words (cities, names, generic words)
    _NOISE = {
        "india", "ahmedabad", "gujarat", "gandhinagar", "chandkheda", "bangalore",
        "mumbai", "pune", "delhi", "hyderabad", "chennai", "kolkata",
        "bachelor", "master", "science", "technology", "engineering", "computer",
        "university", "college", "institute", "education", "degree",
        "january", "february", "march", "april", "june", "july", "august",
        "september", "october", "november", "december", "jan", "feb", "mar",
        "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
        "present", "current", "previously", "worked", "working",
        "summary", "skills", "projects", "experience", "languages",
        "professional", "achievements", "career", "profile",
        "strong", "built", "designed", "developed", "implemented", "improved",
        "contributed", "established", "progressed", "architected",
        "planning", "management", "leadership", "technical", "business",
        "senior", "junior", "lead", "team", "first", "third", "party",
        "multi", "custom", "clean", "driven", "real", "estate",
        "news", "article", "publishing", "search", "review", "scale",
        "growth", "innovation", "integration", "platform", "marketplace",
        "automation", "utility", "bulk", "prompt", "sprint", "spec", "task",
        "code", "system", "design", "architecture", "cloud", "backend",
        "frontend", "software", "engineer", "development", "devops",
        "australia", "google", "apple", "government",
        "hq",
        "currently", "experienced", "drive", "actions", "agents",
        "estimation", "event", "integrations", "responsive",
        "databases", "admin", "copilot", "key", "cd", "ci",
    }
    tokens = re.findall(r"\b[A-Z][a-z]{2,}(?:\.[a-z]{2,})?\b|\b[A-Z]{2,}\b", raw_text)
    for tok in tokens:
        lower = tok.lower()
        if lower not in found and lower not in _NOISE and len(tok) > 1:
            found.add(lower)

    skills = sorted(found)
    logger.info("Extracted %d skills from resume.", len(skills))
    logger.debug("Skills: %s", skills)
    return skills
