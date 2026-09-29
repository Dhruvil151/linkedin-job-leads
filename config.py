"""
Configuration module for LinkedIn Job Leads Scraper.
All tuneable parameters live here — edit this file to customise behaviour.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def gemini_entries():
    raw = os.getenv("GEMINI_API_KEYS_JSON", "").strip()
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError:
            raise ValueError("GEMINI_API_KEYS_JSON must be a JSON list of key/model pairs") from None
        if not isinstance(entries, list) or not entries or any(
            not isinstance(pair, list) or len(pair) != 2
            or any(not isinstance(value, str) or not value.strip() for value in pair)
            for pair in entries
        ):
            raise ValueError("GEMINI_API_KEYS_JSON must contain nonempty key/model pairs")
        return [tuple(value.strip() for value in pair) for pair in entries]
    return []

# ─────────────────────────────────────────────────────────────────────────────
# Gemini API
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEYS: list[tuple[str, str]] = gemini_entries()
GEMINI_BATCH_SIZE: int = 20

# ─────────────────────────────────────────────────────────────────────────────
# Remote Debugging Mode  (RECOMMENDED for Chrome 109+)
# ─────────────────────────────────────────────────────────────────────────────
# Launch Chrome once with the command below, log into LinkedIn, then close.
# From then on every launch of that same command restores your session — no
# login required ever again.
#
# Recommended launch command (Windows):
#   "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
#       --remote-debugging-port=9222 ^
#       --user-data-dir="C:\path\to\linkedin-job-leads\chrome_profile"
#
# Set REMOTE_DEBUGGING_PORT to the port you used (e.g. 9222).
# Set to 0 to fall back to Selenium launching Chrome with a profile (legacy).
REMOTE_DEBUGGING_PORT: int = int(os.environ.get("REMOTE_DEBUGGING_PORT", "0"))

# ─────────────────────────────────────────────────────────────────────────────
# Scraper-dedicated Chrome profile  (used with REMOTE_DEBUGGING_PORT)
# ─────────────────────────────────────────────────────────────────────────────
# This folder stores cookies / session data for the scraper's Chrome instance.
# LinkedIn stays logged in here across all future runs.
SCRAPER_CHROME_PROFILE: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "chrome_profile",
)

# ─────────────────────────────────────────────────────────────────────────────
# Chrome Profile  (only used when REMOTE_DEBUGGING_PORT = 0)
# ─────────────────────────────────────────────────────────────────────────────
# Path to your Chrome User Data directory (the *parent* of the profile folder).
# Example Windows: r"C:\Users\YourName\AppData\Local\Google\Chrome\User Data"
# Example macOS:   "/Users/YourName/Library/Application Support/Google/Chrome"
# Example Linux:   "/home/yourname/.config/google-chrome"
CHROME_USER_DATA_DIR: str = os.environ.get(
    "CHROME_USER_DATA_DIR",
    SCRAPER_CHROME_PROFILE,
)

# Profile directory inside User Data (default is "Default"; could be "Profile 1", etc.)
CHROME_PROFILE_DIR: str = os.environ.get("CHROME_PROFILE_DIR", "Default")

# ─────────────────────────────────────────────────────────────────────────────
# Browser Settings
# ─────────────────────────────────────────────────────────────────────────────
HEADLESS: bool = False          # Set True once you're confident the flow works
WINDOW_WIDTH: int = 1366
WINDOW_HEIGHT: int = 900

# ─────────────────────────────────────────────────────────────────────────────
# Search Configuration
# ─────────────────────────────────────────────────────────────────────────────
# You can supply a full LinkedIn search URL *or* a plain-text query.
# If SEARCH_URL is set it takes priority; otherwise SEARCH_QUERY is used.
SEARCH_URL: str = ""

# Free-text query — LinkedIn search will URL-encode it automatically.
SEARCH_QUERY: str = '"hiring" AND "Backend Engineer"'

# Date filter applied to LinkedIn feed search.
# Options: "past-24h", "past-week", "past-month", or "" (no filter).
DATE_FILTER: str = "past-week"

# ─────────────────────────────────────────────────────────────────────────────
# Scrolling & Pagination
# ─────────────────────────────────────────────────────────────────────────────
MAX_POSTS_TO_SCAN: int = 100          # Stop after scanning this many posts
SCROLL_PAUSE_MIN: float = 1.0        # seconds  — human-simulation delay range
SCROLL_PAUSE_MAX: float = 2.0
SCROLL_STEP_PX: int = 900            # Pixels per smooth-scroll step (bigger = fewer steps)
SCROLL_STEP_DELAY: float = 0.06      # Delay between each scroll step (seconds)
MAX_NO_NEW_POSTS_RETRIES: int = 5    # Give up scrolling after N retries with no new content

# ─────────────────────────────────────────────────────────────────────────────
# Rate-Limit / Cool-Down
# ─────────────────────────────────────────────────────────────────────────────
COOLDOWN_MIN_SECONDS: int = 120      # 2 minutes
COOLDOWN_MAX_SECONDS: int = 300      # 5 minutes
MAX_COOLDOWN_RETRIES: int = 3        # After this many consecutive cool-downs, abort

# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────
OUTPUT_CSV: str = "job_leads.csv"

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
LOG_FILE: str = "scraper.log"
LOG_LEVEL: str = "INFO"               # DEBUG | INFO | WARNING | ERROR
