"""
Stage 1 — Scrape LinkedIn posts and dump them raw to posts.json.

No filtering, no email extraction.  Every post that loads in the feed is
captured as-is so later stages have the full dataset to work with.
"""

import json
import hashlib
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from email_parser import extract_emails
from scraper import (
    _build_search_url,
    _handle_rate_limit,
    _human_delay,
    _is_rate_limited,
    _smooth_scroll,
    _try_expand_post,
    _try_load_more,
    _validate_config,
    create_driver,
)

logger = logging.getLogger(__name__)

POSTS_FILE = "posts.json"
_POST_SELECTOR = "[data-testid='lazy-column'] div[role='listitem']"


def _extract_post(driver: webdriver.Chrome, post_el) -> Optional[dict]:
    """Extract all available data from a single post element."""
    try:
        # Full text via the stable data-testid attribute
        text = ""
        try:
            text_el = post_el.find_element(By.CSS_SELECTOR, "[data-testid='expandable-text-box']")
            text = text_el.text.strip()
        except NoSuchElementException:
            text = post_el.text.strip()

        if not text:
            return None

        # Author name — try several stable patterns
        author = ""
        for sel in [
            "[data-testid='actor-name']",
            "[aria-label][href*='/in/']",
            "a[href*='/in/'] span[aria-hidden='true']",
            "a[href*='/in/']",
        ]:
            try:
                el = post_el.find_element(By.CSS_SELECTOR, sel)
                author = el.text.strip() or el.get_attribute("aria-label") or ""
                if author:
                    break
            except NoSuchElementException:
                continue

        # Post permalink
        url = ""
        for sel in [
            "a[href*='/feed/update/']",
            "a[href*='activity']",
        ]:
            try:
                el = post_el.find_element(By.CSS_SELECTOR, sel)
                href = el.get_attribute("href") or ""
                if href:
                    url = href.split("?")[0]
                    break
            except NoSuchElementException:
                continue

        # Unique post ID from parent element's id attribute
        post_id = driver.execute_script(
            "return arguments[0].parentElement ? arguments[0].parentElement.id : ''",
            post_el,
        ) or str(hash(text[:120]))

        emails = extract_emails(text)

        return {
            "id": post_id,
            "author": author,
            "text": text,
            "emails": emails,
            "url": url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "query": config.SEARCH_QUERY,
        }

    except StaleElementReferenceException:
        return None


def _load_existing_posts() -> list:
    """Load existing posts.json so we can append without losing old data."""
    if not os.path.exists(POSTS_FILE):
        return []
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_posts(posts: list) -> None:
    with open(POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    logger.info("Saved %d total posts to %s", len(posts), POSTS_FILE)


def scrape_posts(strict: bool = False) -> list:
    """
    Run the scraping loop.  Returns the list of newly captured posts.
    All posts (new + existing) are persisted to posts.json.
    """
    _validate_config()

    existing = _load_existing_posts()
    existing_ids = {p["id"] for p in existing}
    existing_texts = {p.get("text", "").strip() for p in existing if p.get("text")}
    logger.info("Loaded %d existing posts from %s", len(existing), POSTS_FILE)

    driver = create_driver()
    new_posts: list = []
    cooldown_count = 0

    try:
        url = _build_search_url()
        logger.info("Navigating to: %s", url)
        driver.get(url)
        _human_delay(3, 5)

        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _POST_SELECTOR))
            )
        except TimeoutException:
            if strict:
                body = driver.find_element(By.TAG_NAME, "body").text.lower()
                if "no results found" not in body:
                    if _is_rate_limited(driver):
                        raise RuntimeError("LinkedIn requires verification or has limited access")
                    logger.warning("Search cards did not load; retrying the page once.")
                    driver.refresh()
                    WebDriverWait(driver, 45).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, _POST_SELECTOR))
                    )
                else:
                    return []
            logger.warning("Timed out waiting for posts — page may be slow or empty.")

        if _is_rate_limited(driver):
            if strict:
                raise RuntimeError("LinkedIn requires verification or has limited access")
            if not _handle_rate_limit(driver, cooldown_count):
                return new_posts
            cooldown_count += 1

        no_new_retries = 0
        posts_scanned = 0
        scroll_count = 0
        load_more_cooldown = 0       # iterations to skip before clicking Load More again
        load_more_fruitless = 0      # consecutive Load More clicks that produced 0 new posts
        MAX_LOAD_MORE_FRUITLESS = 4  # give up when feed is truly exhausted
        last_container_count = 0
        last_height = driver.execute_script("return document.body.scrollHeight")

        while posts_scanned < config.MAX_POSTS_TO_SCAN:
            containers = driver.execute_script(
                "return Array.from(document.querySelectorAll(arguments[0]), "
                "el => ({element: el, text: el.innerText || ''}));",
                _POST_SELECTOR,
            )
            current_container_count = len(containers)

            new_this_round = 0

            # Revisit loaded cards because content can arrive after its container.
            for entry in containers:
                if posts_scanned >= config.MAX_POSTS_TO_SCAN:
                    break

                post_el = entry["element"]
                raw_text = entry["text"].strip()
                text_preview = raw_text[:120]

                if not text_preview:
                    continue

                if raw_text in existing_texts:
                    continue

                # Temporary ID check before full extraction
                if strict:
                    temp_id = hashlib.sha256(raw_text.encode()).hexdigest()
                else:
                    temp_id = driver.execute_script(
                        "return arguments[0].parentElement ? arguments[0].parentElement.id : ''",
                        post_el,
                    ) or str(hash(text_preview))

                if temp_id in existing_ids:
                    continue

                existing_ids.add(temp_id)
                posts_scanned += 1

                # Expand "see more" before extracting full text
                _try_expand_post(driver, post_el)

                post = _extract_post(driver, post_el)
                if post is None:
                    continue
                if post["text"].strip() in existing_texts:
                    continue
                if strict:
                    post["id"] = hashlib.sha256(" ".join(post["text"].lower().split()).encode()).hexdigest()

                new_posts.append(post)
                existing_texts.add(post["text"].strip())
                new_this_round += 1
                logger.info(
                    "Post #%d captured — %s",
                    len(existing) + len(new_posts),
                    (post["text"][:60].replace("\n", " ")),
                )

            if new_this_round:
                _save_posts(existing + new_posts)

            _smooth_scroll(driver, random.randint(900, 1500))
            _human_delay()

            new_height = driver.execute_script("return document.body.scrollHeight")
            if load_more_cooldown > 0:
                load_more_cooldown -= 1

            # Page grew OR new containers appeared → LinkedIn loaded content
            page_grew = new_height > last_height
            containers_grew = current_container_count > last_container_count

            if new_this_round > 0:
                no_new_retries = 0
                cooldown_count = 0
                load_more_fruitless = 0  # a click eventually paid off
                load_more_cooldown = 0
            elif page_grew or containers_grew:
                # Content arrived but not processed yet — keep scrolling, don't penalize
                load_more_fruitless = 0
            elif load_more_cooldown == 0:
                # Stop if we've clicked Load More many times with zero new posts each time
                if load_more_fruitless >= MAX_LOAD_MORE_FRUITLESS:
                    logger.info(
                        "Load More clicked %d times with no new posts — feed exhausted, stopping.",
                        load_more_fruitless,
                    )
                    break
                if _try_load_more(driver):
                    no_new_retries = 0
                    load_more_cooldown = 1
                    load_more_fruitless += 1
                    time.sleep(2)
                else:
                    no_new_retries += 1
                    logger.info("No new posts (retry %d/%d).", no_new_retries, config.MAX_NO_NEW_POSTS_RETRIES)
                    if no_new_retries >= config.MAX_NO_NEW_POSTS_RETRIES:
                        logger.info("Reached end of feed — stopping.")
                        break
            # else: cooldown active — keep scrolling and waiting

            last_height = new_height
            last_container_count = current_container_count
            scroll_count += 1

            if scroll_count % 5 == 0 and _is_rate_limited(driver):
                if strict:
                    raise RuntimeError("LinkedIn requires verification or has limited access")
                if not _handle_rate_limit(driver, cooldown_count):
                    break
                cooldown_count += 1
                # Page was refreshed — reset height/container baselines so the
                # loop doesn't think page shrunk and spam Load More immediately.
                last_height = driver.execute_script("return document.body.scrollHeight")
                last_container_count = len(driver.find_elements(By.CSS_SELECTOR, _POST_SELECTOR))
                load_more_cooldown = 0
                load_more_fruitless = 0
                no_new_retries = 0

            logger.info("Posts scanned: %d / %s", posts_scanned, config.MAX_POSTS_TO_SCAN)

    except KeyboardInterrupt:
        logger.warning("Interrupted — saving progress.")
    except WebDriverException as exc:
        if strict:
            raise
        logger.error("WebDriver error: %s", exc, exc_info=True)
    finally:
        # Save all posts (existing + new) to posts.json
        _save_posts(existing + new_posts)
        if not config.REMOTE_DEBUGGING_PORT:
            try:
                driver.quit()
            except Exception:
                pass

    logger.info("Done — %d new post(s) captured.", len(new_posts))
    return new_posts
