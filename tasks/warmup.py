"""
Warmup phase (~1 hour):
  1. Follow WARMUP_TARGETS
  2. Like their recent posts
  3. Browse the feed naturally
"""
import time
import random

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import WARMUP_TARGETS, WARMUP_DURATION


def run_warmup(bot, follow_list: list = None):
    """
    follow_list: list of Threads profile URLs (or handles).
    Falls back to WARMUP_TARGETS from config if not provided.
    """
    log = bot.log
    log.info(f"[{bot.username}] Starting warmup ({WARMUP_DURATION//60} min)...")

    targets = _resolve_targets(follow_list) or WARMUP_TARGETS

    follow_targets(bot, targets)
    like_target_posts(bot, targets)
    warm_feed(bot, WARMUP_DURATION)

    log.info(f"[{bot.username}] Warmup complete")


def _resolve_targets(follow_list: list | None) -> list:
    """Convert bare handles to full URLs if needed."""
    if not follow_list:
        return []
    resolved = []
    for entry in follow_list:
        entry = entry.strip().lstrip("@")
        if entry.startswith("http"):
            resolved.append(entry)
        else:
            resolved.append(f"https://www.threads.net/@{entry}")
    return resolved


def follow_targets(bot, targets):
    log = bot.log
    for url in targets:
        try:
            bot.go(url)
            _click_follow_button(bot)
            log.info(f"[{bot.username}] Followed: {url}")
            time.sleep(random.uniform(3, 7))
        except Exception as e:
            log.warning(f"[{bot.username}] Could not follow {url}: {e}")


def _click_follow_button(bot):
    for xpath in [
        '//div[@role="button" and text()="Follow"]',
        '//span[text()="Follow"]/ancestor::div[@role="button"]',
        '//button[contains(text(),"Follow")]',
    ]:
        try:
            el = WebDriverWait(bot.driver, 6).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            bot.smart_click(el)
            time.sleep(1)
            return True
        except Exception:
            continue
    return False


def like_target_posts(bot, targets, posts_per_target=3):
    log = bot.log
    for url in targets:
        try:
            bot.go(url)
            time.sleep(2)
            liked = _like_visible_posts(bot, posts_per_target)
            log.info(f"[{bot.username}] Liked {liked} posts on {url}")
            time.sleep(random.uniform(4, 8))
        except Exception as e:
            log.warning(f"[{bot.username}] Error liking posts on {url}: {e}")


def _like_visible_posts(bot, count):
    liked = 0
    attempts = 0
    max_attempts = count * 5

    while liked < count and attempts < max_attempts:
        attempts += 1
        bot.scroll_down(random.randint(300, 700))

        try:
            hearts = bot.driver.find_elements(By.XPATH, '//svg[@aria-label="Like"]')
            for heart in hearts:
                try:
                    rect = heart.rect
                    if rect["height"] == 0:
                        continue
                    # Walk up to find the button
                    el = heart
                    for _ in range(5):
                        el = bot.driver.execute_script("return arguments[0].parentElement;", el)
                        if el and el.get_attribute("role") == "button":
                            break
                    bot.smart_click(el)
                    liked += 1
                    time.sleep(random.uniform(1.5, 3.5))
                    if liked >= count:
                        break
                except Exception:
                    continue
        except Exception:
            pass

    return liked


def warm_feed(bot, duration_seconds):
    log = bot.log
    bot.go_home()
    start = time.time()
    log.info(f"[{bot.username}] Browsing feed for {duration_seconds//60} minutes...")

    while time.time() - start < duration_seconds:
        bot.scroll_down(random.randint(300, 900))

        if random.random() < 0.05:
            pause = random.uniform(5, 15)
            log.info(f"[{bot.username}] Feed pause {pause:.0f}s")
            time.sleep(pause)

        # Occasionally like a post during warmup browsing
        if random.random() < 0.15:
            _like_visible_posts(bot, 1)
