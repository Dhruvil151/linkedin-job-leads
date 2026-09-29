#!/usr/bin/env python3
"""
LinkedIn Job-Leads Scraper — Entry Point

New workflow (default):
    python main.py -q "MERN stack developer ahmedabad" -r 9222 --resume resume.pdf
    → Stage 1: scrape posts  → posts.json  (raw, all posts preserved)
    → Stage 2: parse resume  → skill list
    → Stage 3: score & sort  → results.json (all posts, sorted by match score)

Legacy email-extraction mode:
    python main.py --email-mode -q "hiring backend engineer" -r 9222
    → produces job_leads.csv
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime

import config

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def _archive_run() -> None:
    """Move posts.json and results.json into logs/ with a timestamp before a new run."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    for fname in ("posts.json", "results.json", "unique_emails.txt"):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
        if os.path.exists(src):
            dst = os.path.join(LOGS_DIR, f"{os.path.splitext(fname)[0]}_{stamp}.json")
            shutil.move(src, dst)
            logging.getLogger(__name__).info("Archived %s → logs/%s", fname, os.path.basename(dst))


def _setup_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape LinkedIn posts and match them against your resume.",
    )
    parser.add_argument("--query", "-q", help="Search query string.")
    parser.add_argument("--url", "-u", help="Full LinkedIn search URL.")
    parser.add_argument("--headless", action="store_true", default=False)
    parser.add_argument("--max-posts", "-n", type=int, help="Max posts to scrape.")
    parser.add_argument("--date-filter", "-d",
                        choices=["past-24h", "past-week", "past-month", ""],
                        help="Date filter for posts.")
    parser.add_argument("--remote-port", "-r", type=int,
                        help="Attach to Chrome on this debug port.")
    parser.add_argument("--chrome-profile", help="Chrome User Data directory path.")
    parser.add_argument("--profile-dir", help='Profile directory name (e.g. "Profile 1").')

    # New workflow
    parser.add_argument("--resume", help="Path to resume PDF for skill matching.")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip scraping; use existing posts.json and just re-score.")
    parser.add_argument("--skip-score", action="store_true",
                        help="Skip Gemini scoring; only scrape and save to posts.json.")

    # Legacy mode
    parser.add_argument("--email-mode", action="store_true",
                        help="Run the old email-extraction pipeline (outputs job_leads.csv).")
    parser.add_argument("--output", "-o", help="CSV output path (email-mode only).")

    return parser.parse_args()


def _apply_config(args: argparse.Namespace) -> None:
    if args.query:
        config.SEARCH_QUERY = args.query
    if args.url:
        config.SEARCH_URL = args.url
    if args.headless:
        config.HEADLESS = True
    if args.max_posts:
        config.MAX_POSTS_TO_SCAN = args.max_posts
    if args.date_filter is not None:
        config.DATE_FILTER = args.date_filter
    if args.remote_port:
        config.REMOTE_DEBUGGING_PORT = args.remote_port
    if args.chrome_profile:
        config.CHROME_USER_DATA_DIR = args.chrome_profile
    if args.profile_dir:
        config.CHROME_PROFILE_DIR = args.profile_dir
    if args.output:
        config.OUTPUT_CSV = args.output


def _find_latest_resume() -> str:
    """Auto-detect the newest resume PDF in ~/Downloads if --resume not given."""
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    pdfs = [
        os.path.join(downloads, f)
        for f in os.listdir(downloads)
        if f.lower().endswith(".pdf") and "resume" in f.lower()
    ]
    if not pdfs:
        return ""
    return max(pdfs, key=os.path.getmtime)


def main() -> None:
    args = _parse_args()
    _apply_config(args)
    _setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("LinkedIn Scraper — started at %s", datetime.now().isoformat())
    logger.info("Query  : %s", config.SEARCH_QUERY)
    logger.info("Filter : %s", config.DATE_FILTER or "none")
    logger.info("Max    : %d posts", config.MAX_POSTS_TO_SCAN)
    logger.info("=" * 60)

    # ── Legacy email-extraction mode ─────────────────────────────────────
    if args.email_mode:
        from csv_writer import CSVWriter
        from scraper import scrape
        with CSVWriter(config.OUTPUT_CSV) as writer:
            total = scrape(writer)
        logger.info("Done — %d lead(s) exported to %s", total, config.OUTPUT_CSV)
        return

    # ── New workflow: scrape → parse resume → score & sort ───────────────

    # Stage 1 — Scrape posts
    if not args.skip_scrape:
        # Only archive on a fresh single-run start, not mid-batch --skip-score accumulation
        if not args.skip_score:
            _archive_run()
        from scrape_posts import scrape_posts
        new_posts = scrape_posts()
        logger.info("Stage 1 complete — %d new post(s) added to posts.json", len(new_posts))
    else:
        logger.info("Stage 1 skipped — using existing posts.json")

    if args.skip_score:
        logger.info("Scoring skipped — posts saved to posts.json.")
        return

    # Stage 2 — Parse resume (extract raw text for Gemini)
    resume_path = args.resume or _find_latest_resume()
    if not resume_path or not os.path.exists(resume_path):
        logger.error(
            "Resume PDF not found. Pass --resume /path/to/resume.pdf "
            "or put a *resume*.pdf in your Downloads folder."
        )
        sys.exit(1)

    logger.info("Stage 2 — Reading resume: %s", resume_path)
    from resume_parser import extract_text
    resume_text = extract_text(resume_path)
    logger.info("Resume loaded (%d chars)", len(resume_text))

    # Stage 3 — Gemini scores and filters all posts
    logger.info("Stage 3 — Sending posts to Gemini for scoring …")
    from gemini_scorer import score_posts
    results = score_posts(resume_text)

    hiring = [r for r in results if r.get("gemini_type") == "hiring"]
    logger.info(
        "Done — %d hiring posts out of %d total. Top score: %s/10",
        len(hiring), len(results),
        hiring[0]["gemini_score"] if hiring else "—",
    )
    logger.info("All results saved to results.json")

    from extract_emails import generate_unique_emails
    generate_unique_emails()


if __name__ == "__main__":
    main()
