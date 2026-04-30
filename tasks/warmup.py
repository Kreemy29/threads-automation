"""
Warmup phase — human-like browsing before the account starts posting.

Every account gets a different randomized sequence of micro-actions so no two
accounts ever follow the same pattern. Actions are weighted and shuffled each
run, with random delays, pauses, and "mood" shifts built in.
"""
import time
import random
import logging

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import WARMUP_TARGETS, WARMUP_DURATION, WARMUP_LIKES_MIN, WARMUP_LIKES_MAX, WARMUP_MAX_FOLLOWS


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_warmup(bot, follow_list: list = None):
    log = bot.log
    log.info(f"[{bot.username}] Warmup starting ({WARMUP_DURATION // 60} min)")

    targets = _resolve_targets(follow_list) or list(WARMUP_TARGETS)
    random.shuffle(targets)  # each account visits targets in a different order

    end_time = time.time() + WARMUP_DURATION
    _run_randomized_session(bot, targets, end_time)

    log.info(f"[{bot.username}] Warmup complete")


# ---------------------------------------------------------------------------
# Core randomized session loop
# ---------------------------------------------------------------------------

def _run_randomized_session(bot, targets, end_time):
    """
    Runs a randomized mix of actions until end_time.
    Each iteration picks a random action from a weighted pool that changes
    based on what was just done — so the bot never repeats the same
    action back-to-back.
    """
    log = bot.log
    last_action = None
    targets_remaining = list(targets)
    liked_total = 0
    followed_total = 0

    # "Mood" controls pace — shifts randomly every few actions
    mood = _new_mood()
    mood_counter = 0

    while time.time() < end_time:
        # Shift mood every 3-7 actions
        mood_counter += 1
        if mood_counter >= random.randint(3, 7):
            mood = _new_mood()
            mood_counter = 0
            log.info(f"[{bot.username}] Mood → {mood['name']}")

        # Build action pool — exclude last action to avoid repetition
        pool = _build_action_pool(
            last_action, targets_remaining,
            liked_total, followed_total, end_time
        )
        if not pool:
            _idle_scroll(bot, mood, seconds=random.randint(20, 40))
            continue

        action = random.choices(pool["actions"], weights=pool["weights"], k=1)[0]
        last_action = action

        try:
            if action == "scroll_feed":
                _scroll_feed(bot, mood, seconds=random.randint(25, 70))

            elif action == "visit_profile":
                if targets_remaining:
                    url = targets_remaining.pop(0)
                    _visit_profile(bot, url, mood)
                else:
                    _scroll_feed(bot, mood, seconds=20)

            elif action == "like_on_profile":
                if targets_remaining:
                    url = targets_remaining[0]
                    n = _like_visible_posts(bot, random.randint(WARMUP_LIKES_MIN, WARMUP_LIKES_MAX))
                    liked_total += n
                    log.info(f"[{bot.username}] Liked {n} posts (total {liked_total})")

            elif action == "follow_profile":
                if targets_remaining:
                    url = targets_remaining.pop(0)
                    bot.go(url)
                    time.sleep(random.uniform(2, 4))
                    if _click_follow(bot):
                        followed_total += 1
                        log.info(f"[{bot.username}] Followed {url} (total {followed_total})")
                    _read_pause(bot, mood)

            elif action == "open_post":
                _open_random_post(bot, mood)

            elif action == "go_home_browse":
                bot.go_home()
                time.sleep(random.uniform(1.5, 3))
                _scroll_feed(bot, mood, seconds=random.randint(15, 35))

            elif action == "long_pause":
                pause = random.uniform(15, 45)
                log.info(f"[{bot.username}] Long pause {pause:.0f}s")
                time.sleep(pause)

            elif action == "scroll_to_top_then_down":
                bot.scroll_to_top()
                time.sleep(random.uniform(1, 2))
                _scroll_feed(bot, mood, seconds=random.randint(10, 25))

        except Exception as e:
            log.warning(f"[{bot.username}] Warmup action '{action}' error: {e}")
            time.sleep(3)

        # Inter-action gap — varies by mood
        _inter_action_gap(mood)


# ---------------------------------------------------------------------------
# Action pool builder
# ---------------------------------------------------------------------------

def _build_action_pool(last_action, targets_remaining, liked, followed, end_time):
    remaining_secs = end_time - time.time()
    has_targets = bool(targets_remaining)

    actions, weights = [], []

    def add(name, weight):
        if name != last_action:  # never repeat last action
            actions.append(name)
            weights.append(weight)

    add("scroll_feed",            40)
    add("go_home_browse",         15)
    add("long_pause",             10)
    add("scroll_to_top_then_down", 10)
    add("open_post",              20)

    if has_targets:
        add("visit_profile",   20)
        add("like_on_profile", 25)
        if followed < WARMUP_MAX_FOLLOWS:
            add("follow_profile", 15)

    if not actions:
        # Fallback — last_action exclusion blocked everything
        actions = ["scroll_feed", "long_pause"]
        weights = [70, 30]

    return {"actions": actions, "weights": weights}


# ---------------------------------------------------------------------------
# Individual actions
# ---------------------------------------------------------------------------

def _scroll_feed(bot, mood, seconds=40):
    bot.go_home()
    start = time.time()
    while time.time() - start < seconds:
        scroll_px = random.randint(mood["scroll_min"], mood["scroll_max"])
        bot.driver.execute_script(f"window.scrollBy(0, {scroll_px});")

        # Occasionally stop to "read"
        if random.random() < mood["read_chance"]:
            time.sleep(random.uniform(mood["read_min"], mood["read_max"]))
        else:
            time.sleep(random.uniform(0.4, 1.4))

        # Occasionally like something
        if random.random() < mood["like_chance"]:
            _like_visible_posts(bot, 1)


def _idle_scroll(bot, mood, seconds=30):
    start = time.time()
    while time.time() - start < seconds:
        bot.driver.execute_script(
            f"window.scrollBy(0, {random.randint(200, 600)});"
        )
        time.sleep(random.uniform(0.8, 2.5))


def _visit_profile(bot, url, mood):
    bot.go(url)
    time.sleep(random.uniform(2, 4))
    # Scroll through their posts
    for _ in range(random.randint(2, 5)):
        bot.driver.execute_script(
            f"window.scrollBy(0, {random.randint(300, 700)});"
        )
        time.sleep(random.uniform(mood["read_min"], mood["read_max"]))


def _open_random_post(bot, mood):
    """Click on a random post from the current feed to open it."""
    try:
        links = bot.driver.find_elements(
            By.XPATH, '//a[contains(@href,"/post/") or contains(@href,"/t/")]'
        )
        if not links:
            return
        link = random.choice(links[:8])
        href = link.get_attribute("href")
        if href:
            bot.go(href)
            _read_pause(bot, mood)
            bot.go_home()
    except Exception:
        pass


def _read_pause(bot, mood):
    time.sleep(random.uniform(mood["read_min"], mood["read_max"]))
    # Sometimes scroll down a bit while "reading"
    if random.random() < 0.5:
        for _ in range(random.randint(1, 3)):
            bot.driver.execute_script(
                f"window.scrollBy(0, {random.randint(150, 400)});"
            )
            time.sleep(random.uniform(0.8, 2))


def _like_visible_posts(bot, count):
    liked = 0
    try:
        hearts = bot.driver.find_elements(By.XPATH, '//svg[@aria-label="Like"]')
        random.shuffle(hearts)
        for heart in hearts:
            if liked >= count:
                break
            try:
                if heart.rect["height"] == 0:
                    continue
                el = heart
                for _ in range(5):
                    el = bot.driver.execute_script("return arguments[0].parentElement;", el)
                    if el and el.get_attribute("role") == "button":
                        break
                bot.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                time.sleep(random.uniform(0.3, 0.8))
                el.click()
                liked += 1
                time.sleep(random.uniform(1.2, 3))
            except Exception:
                continue
    except Exception:
        pass
    return liked


def _click_follow(bot) -> bool:
    for xpath in [
        '//div[@role="button" and text()="Follow"]',
        '//span[text()="Follow"]/ancestor::div[@role="button"]',
        '//button[normalize-space()="Follow"]',
    ]:
        try:
            el = WebDriverWait(bot.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            el.click()
            time.sleep(random.uniform(0.8, 1.5))
            return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Mood system
# ---------------------------------------------------------------------------

_MOODS = [
    {
        "name": "casual",
        "scroll_min": 250, "scroll_max": 600,
        "read_chance": 0.25, "read_min": 2, "read_max": 7,
        "like_chance": 0.12,
        "gap_min": 1.0, "gap_max": 3.5,
    },
    {
        "name": "engaged",
        "scroll_min": 150, "scroll_max": 400,
        "read_chance": 0.45, "read_min": 4, "read_max": 12,
        "like_chance": 0.25,
        "gap_min": 2.0, "gap_max": 6.0,
    },
    {
        "name": "fast",
        "scroll_min": 500, "scroll_max": 1000,
        "read_chance": 0.08, "read_min": 1, "read_max": 3,
        "like_chance": 0.06,
        "gap_min": 0.3, "gap_max": 1.5,
    },
    {
        "name": "distracted",   # long random pauses between scrolls
        "scroll_min": 300, "scroll_max": 700,
        "read_chance": 0.15, "read_min": 8, "read_max": 25,
        "like_chance": 0.10,
        "gap_min": 3.0, "gap_max": 10.0,
    },
]

def _new_mood():
    return random.choice(_MOODS)


def _inter_action_gap(mood):
    time.sleep(random.uniform(mood["gap_min"], mood["gap_max"]))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_targets(follow_list) -> list:
    if not follow_list:
        return []
    resolved = []
    for entry in follow_list:
        entry = entry.strip().lstrip("@")
        if entry.startswith("http"):
            resolved.append(entry)
        else:
            resolved.append(f"https://www.threads.com/@{entry}")
    return resolved
