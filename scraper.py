"""
Core Selenium automation logic for LinkedIn post scraping.

Responsibilities:
- Launch Chrome with a persistent user profile (avoids login CAPTCHAs).
- Navigate to a LinkedIn search URL.
- Smooth-scroll through the feed, loading posts dynamically.
- Extract post metadata + email addresses from each post's text.
- Honour rate-limit walls with configurable cool-downs.
"""

import hashlib
import json
import logging
import os
import random
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Optional, Set

# pyrefly: ignore [missing-import]
from selenium import webdriver
# pyrefly: ignore [missing-import]
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
# pyrefly: ignore [missing-import]
from selenium.webdriver.chrome.options import Options
# pyrefly: ignore [missing-import]
from selenium.webdriver.chrome.service import Service
# pyrefly: ignore [missing-import]
from selenium.webdriver.common.by import By
# pyrefly: ignore [missing-import]
from selenium.webdriver.support import expected_conditions as EC
# pyrefly: ignore [missing-import]
from selenium.webdriver.support.ui import WebDriverWait

import config
from csv_writer import CSVWriter, LeadRow
from email_parser import extract_emails

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Config validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_config() -> None:
    """Fail fast with a readable message if config is wrong."""
    if config.REMOTE_DEBUGGING_PORT:
        return  # no local profile needed in attach mode
    if not os.path.isdir(config.CHROME_USER_DATA_DIR):
        raise FileNotFoundError(
            f"Chrome User Data directory not found: {config.CHROME_USER_DATA_DIR!r}\n"
            "Update CHROME_USER_DATA_DIR in config.py or pass --chrome-profile on the CLI.\n"
            "See SETUP_GUIDE.md § 2 for how to find your profile path.\n"
            "Tip: use --remote-port to attach to an already-open Chrome instead."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Seen-post persistence (dedup across runs)
# ─────────────────────────────────────────────────────────────────────────────

def _seen_ids_path() -> str:
    """Return a per-query cache file path so different queries don't collide."""
    key = (config.SEARCH_URL or config.SEARCH_QUERY).encode()
    suffix = hashlib.md5(key).hexdigest()[:8]
    return f".seen_posts_{suffix}.json"


def _load_seen_ids() -> Set[str]:
    path = _seen_ids_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_seen_ids(seen: Set[str]) -> None:
    path = _seen_ids_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(seen), f)
        logger.debug("Saved %d seen post IDs to %s", len(seen), path)
    except OSError as exc:
        logger.warning("Could not save seen-post cache: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _human_delay(lo: float = config.SCROLL_PAUSE_MIN,
                 hi: float = config.SCROLL_PAUSE_MAX) -> None:
    """Sleep for a random duration between *lo* and *hi* seconds."""
    duration = random.uniform(lo, hi)
    logger.debug("Sleeping %.1f s", duration)
    time.sleep(duration)


def _smooth_scroll(driver: webdriver.Chrome, distance: int) -> None:
    """Scroll *distance* pixels in small increments to look human."""
    scrolled = 0
    step = config.SCROLL_STEP_PX
    while scrolled < distance:
        chunk = min(step + random.randint(-50, 50), distance - scrolled)
        driver.execute_script(f"window.scrollBy(0, {chunk});")
        scrolled += chunk
        time.sleep(config.SCROLL_STEP_DELAY + random.uniform(0, 0.1))


def _build_search_url() -> str:
    """Build a LinkedIn search URL from config values."""
    if config.SEARCH_URL:
        return config.SEARCH_URL

    if not config.SEARCH_QUERY.strip():
        raise ValueError(
            "Neither SEARCH_URL nor SEARCH_QUERY is set. "
            "Edit config.py or pass --query / --url on the CLI."
        )

    base = "https://www.linkedin.com/search/results/content/"
    params = {"keywords": config.SEARCH_QUERY, "origin": "GLOBAL_SEARCH_HEADER"}

    # LinkedIn uses a specific datePosted parameter
    date_map = {
        "past-24h": "past-24h",
        "past-week": "past-week",
        "past-month": "past-month",
    }
    if config.DATE_FILTER in date_map:
        params["datePosted"] = f'"{date_map[config.DATE_FILTER]}"'

    return base + "?" + urllib.parse.urlencode(params)


# ─────────────────────────────────────────────────────────────────────────────
# Browser Setup
# ─────────────────────────────────────────────────────────────────────────────

def create_driver() -> webdriver.Chrome:
    """Create and return a Chrome WebDriver.

    Preferred mode (REMOTE_DEBUGGING_PORT > 0):
        Attaches to a Chrome instance the user already opened with
        ``chrome.exe --remote-debugging-port=<PORT>``.  No profile locking,
        no anti-bot crash — Chrome runs exactly as the user left it.

    Legacy mode (REMOTE_DEBUGGING_PORT = 0):
        Selenium launches Chrome with the configured user profile.
        May fail on Chrome 109+ due to improved automation detection.
    """
    opts = Options()
    driver_path = os.getenv("CHROME_DRIVER_PATH")
    service = Service(executable_path=driver_path) if driver_path else Service()
    if os.getenv("CHROME_BINARY"):
        opts.binary_location = os.environ["CHROME_BINARY"]

    if config.REMOTE_DEBUGGING_PORT:
        # ── Attach to already-running Chrome ─────────────────────────
        opts.debugger_address = f"localhost:{config.REMOTE_DEBUGGING_PORT}"
        driver = webdriver.Chrome(service=service, options=opts)
        logger.info("Attached to existing Chrome on port %d", config.REMOTE_DEBUGGING_PORT)
    else:
        # ── Launch Chrome with user profile (legacy) ──────────────────
        opts.add_argument(f"--user-data-dir={config.CHROME_USER_DATA_DIR}")
        opts.add_argument(f"--profile-directory={config.CHROME_PROFILE_DIR}")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--disable-extensions")
        opts.add_argument(f"--window-size={config.WINDOW_WIDTH},{config.WINDOW_HEIGHT}")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--disable-restore-session-state")
        opts.add_argument("--disable-session-crashed-bubble")

        if config.HEADLESS:
            opts.add_argument("--headless=new")

        driver = webdriver.Chrome(service=service, options=opts)
        logger.info("Chrome launched with profile: %s / %s",
                    config.CHROME_USER_DATA_DIR, config.CHROME_PROFILE_DIR)

    # Remove navigator.webdriver flag (works in both modes)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    # Page readiness uses explicit waits; optional post fields should fail fast.
    driver.implicitly_wait(0)
    return driver


# ─────────────────────────────────────────────────────────────────────────────
# Rate-Limit Detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_rate_limited(driver: webdriver.Chrome) -> bool:
    """Detect LinkedIn rate-limit / auth-wall pages."""
    indicators = [
        "we've detected unusual activity",
        "you've reached the limit",
        "please verify",
        "security verification",
        "let's do a quick security check",
        "authwall",
    ]
    try:
        page_src = driver.page_source.lower()
        return any(ind in page_src for ind in indicators)
    except WebDriverException:
        return False


def _handle_rate_limit(driver: webdriver.Chrome, attempt: int) -> bool:
    """
    Cool-down and retry.  Returns True if we should continue, False to abort.
    """
    if attempt >= config.MAX_COOLDOWN_RETRIES:
        logger.error("Hit rate limit %d times — aborting.", attempt)
        return False

    wait = random.randint(config.COOLDOWN_MIN_SECONDS, config.COOLDOWN_MAX_SECONDS)
    logger.warning(
        "Rate limit detected (attempt %d/%d). Cooling down for %d s …",
        attempt + 1, config.MAX_COOLDOWN_RETRIES, wait,
    )
    time.sleep(wait)
    driver.refresh()
    _human_delay()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Post Extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_post_data(post_element) -> Optional[dict]:
    """
    Given a Selenium WebElement representing a single post container,
    return a dict with author, text, and post URL — or None on failure.
    """
    data = {"author": "", "text": "", "url": ""}

    try:
        # Author / entity name — LinkedIn uses several selectors
        for sel in [
            ".update-components-actor__name span[aria-hidden='true']",
            ".update-components-actor__title span",
            ".feed-shared-actor__name",
            ".update-components-actor__name",
        ]:
            try:
                el = post_element.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip():
                    data["author"] = el.text.strip()
                    break
            except NoSuchElementException:
                continue

        # Post text body
        for sel in [
            "[data-testid='expandable-text-box']",
            ".feed-shared-update-v2__description",
            ".update-components-text",
            ".feed-shared-text",
            ".break-words",
        ]:
            try:
                el = post_element.find_element(By.CSS_SELECTOR, sel)
                if el.text.strip():
                    data["text"] = el.text.strip()
                    break
            except NoSuchElementException:
                continue

        # Post permalink
        for sel in [
            "a.app-aware-link[href*='activity']",
            "a[href*='/feed/update/']",
            ".feed-shared-control-menu a",
        ]:
            try:
                el = post_element.find_element(By.CSS_SELECTOR, sel)
                href = el.get_attribute("href")
                if href:
                    data["url"] = href.split("?")[0]
                    break
            except NoSuchElementException:
                continue

    except StaleElementReferenceException:
        logger.debug("Stale element — post may have been removed from DOM.")
        return None

    if not data["text"]:
        return None

    return data


# ─────────────────────────────────────────────────────────────────────────────
# "See more" Expansion
# ─────────────────────────────────────────────────────────────────────────────

def _try_load_more(driver: webdriver.Chrome) -> bool:
    """
    Click LinkedIn's 'Show more results' / 'Load more' button if present.
    Returns True if the button was clicked, False if not found.
    LinkedIn renders this button when infinite-scroll is exhausted but more pages exist.
    """
    selectors = [
        # data-testid variants (stable)
        "[data-testid='load-more-button']",
        "[data-testid='show-more-button']",
        # aria-label variants
        "button[aria-label*='more result' i]",
        "button[aria-label*='Load more' i]",
        # old class-based (may still exist in some rollouts)
        "button.scaffold-finite-scroll__load-button",
    ]
    for sel in selectors:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.3)
            driver.execute_script("arguments[0].click();", btn)
            logger.info("Clicked 'Load more' button (%s).", sel)
            _human_delay(1, 2)
            return True
        except (NoSuchElementException, StaleElementReferenceException):
            continue

    # XPath fallback: match by visible text
    for phrase in ["Show more results", "Load more", "See more"]:
        try:
            btn = driver.find_element(
                By.XPATH,
                f"//button[normalize-space()='{phrase}']",
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.3)
            driver.execute_script("arguments[0].click();", btn)
            logger.info("Clicked '%s' button (XPath).", phrase)
            _human_delay(2, 4)  # longer wait so LinkedIn starts rendering new posts
            return True
        except (NoSuchElementException, StaleElementReferenceException):
            continue

    return False


def _try_expand_post(driver: webdriver.Chrome, post_element) -> None:
    """Click 'See more' / '…more' buttons inside a post to reveal full text."""
    for sel in [
        "[data-testid='expandable-text-button']",
        "button.feed-shared-inline-show-more-text__see-more-less-toggle",
        "button[aria-label*='see more']",
        ".see-more",
        "button.feed-shared-inline-show-more-text",
    ]:
        try:
            btn = post_element.find_element(By.CSS_SELECTOR, sel)
            driver.execute_script("arguments[0].click();", btn)
            try:
                WebDriverWait(driver, 1.5).until(EC.staleness_of(btn))
            except TimeoutException:
                time.sleep(random.uniform(0.3, 0.6))
            return
        except (NoSuchElementException, StaleElementReferenceException):
            continue


# ─────────────────────────────────────────────────────────────────────────────
# Main Scraping Loop
# ─────────────────────────────────────────────────────────────────────────────

def scrape(writer: CSVWriter) -> int:
    """
    Run the full scraping flow.  Returns the number of leads exported.
    """
    _validate_config()

    driver = create_driver()
    leads_found = 0
    seen_post_ids: Set[str] = _load_seen_ids()   # persisted across runs
    seen_emails: Set[str] = set()                # dedup emails within this session
    cooldown_count = 0

    logger.info("Resuming with %d previously seen post IDs.", len(seen_post_ids))

    try:
        # ── Navigate to search results ───────────────────────────────
        url = _build_search_url()
        logger.info("Navigating to: %s", url)
        driver.get(url)
        _human_delay(5, 10)

        # ── Wait for at least one post container to appear ───────────
        # LinkedIn now uses role='listitem' inside a lazy-column as post containers.
        _POST_SELECTOR = "[data-testid='lazy-column'] div[role='listitem']"
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _POST_SELECTOR))
            )
        except TimeoutException:
            logger.warning("Timed out waiting for posts — page may be slow or empty.")

        # ── Rate-limit check right after load ────────────────────────
        if _is_rate_limited(driver):
            if not _handle_rate_limit(driver, cooldown_count):
                return leads_found
            cooldown_count += 1

        no_new_retries = 0
        posts_scanned = 0
        scroll_count = 0
        last_height = driver.execute_script("return document.body.scrollHeight")

        while posts_scanned < config.MAX_POSTS_TO_SCAN:
            # ── Gather post containers currently in the DOM ──────────
            post_containers = driver.find_elements(
                By.CSS_SELECTOR,
                "[data-testid='lazy-column'] div[role='listitem']"
            )

            new_posts_this_round = 0

            for post_el in post_containers:
                if posts_scanned >= config.MAX_POSTS_TO_SCAN:
                    break

                # Generate a lightweight ID for dedup.
                # Use the parent div's unique id (contains activity URN hash) if available.
                try:
                    outer = driver.execute_script(
                        "return arguments[0].parentElement ? arguments[0].parentElement.id : ''",
                        post_el,
                    ) or ""
                    text_preview = (post_el.text or "")[:120]
                except StaleElementReferenceException:
                    continue

                # Post not yet rendered — skip without marking as seen so
                # a later scroll iteration can pick it up once it has content.
                if not outer and not text_preview:
                    continue

                post_id = outer or str(hash(text_preview))
                if post_id in seen_post_ids:
                    continue
                seen_post_ids.add(post_id)
                new_posts_this_round += 1
                posts_scanned += 1

                # Expand truncated text
                _try_expand_post(driver, post_el)

                data = _extract_post_data(post_el)
                if data is None:
                    continue

                emails = extract_emails(data["text"])
                if not emails:
                    continue

                # Write one row per email found
                timestamp = datetime.now(timezone.utc).isoformat()
                snippet = data["text"][:300].replace("\n", " ")

                for email_addr in emails:
                    if email_addr in seen_emails:
                        logger.debug("Skipping duplicate email: %s", email_addr)
                        continue
                    seen_emails.add(email_addr)
                    lead = LeadRow(
                        author_name=data["author"],
                        post_text_snippet=snippet,
                        email=email_addr,
                        timestamp=timestamp,
                        post_url=data["url"],
                    )
                    writer.write_lead(lead)
                    leads_found += 1
                    logger.info(
                        "✅  Lead #%d — %s | %s",
                        leads_found, email_addr, data["author"],
                    )

            # ── Scroll down to load more posts ───────────────────────
            _smooth_scroll(driver, random.randint(800, 1400))
            _human_delay()

            new_height = driver.execute_script("return document.body.scrollHeight")

            if new_posts_this_round > 0:
                # Found new scrapable posts — reset both counters.
                no_new_retries = 0
                cooldown_count = 0
            elif new_height == last_height:
                # Height didn't grow — try "Load more" button before giving up.
                if _try_load_more(driver):
                    # Button found and clicked — reset retry counter and continue.
                    no_new_retries = 0
                else:
                    no_new_retries += 1
                    logger.info(
                        "No new posts loaded (retry %d/%d).",
                        no_new_retries, config.MAX_NO_NEW_POSTS_RETRIES,
                    )
                    if no_new_retries >= config.MAX_NO_NEW_POSTS_RETRIES:
                        logger.info("Reached end of feed — stopping.")
                        break
            # else: page grew but no new scrapable posts yet — keep scrolling.

            last_height = new_height
            scroll_count += 1

            # ── Rate-limit check every 5 scrolls (page_source is expensive) ──
            if scroll_count % 5 == 0 and _is_rate_limited(driver):
                if not _handle_rate_limit(driver, cooldown_count):
                    break
                cooldown_count += 1

            logger.info("Posts scanned so far: %d / %d", posts_scanned, config.MAX_POSTS_TO_SCAN)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user — saving progress.")
    except WebDriverException as exc:
        logger.error("WebDriver error: %s", exc, exc_info=True)
    finally:
        _save_seen_ids(seen_post_ids)
        if not config.REMOTE_DEBUGGING_PORT:
            # Only quit when we launched Chrome ourselves; never close a user-attached session.
            try:
                driver.quit()
            except Exception:
                pass

    return leads_found
