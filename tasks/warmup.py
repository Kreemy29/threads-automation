"""
Warmup phase — structured plan-based execution.

At the start of each warmup session the bot builds a concrete plan from settings,
announces it, then executes each phase and validates success/failure.
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
    targets = _resolve_targets(follow_list) or list(WARMUP_TARGETS)
    random.shuffle(targets)

    # ── Build the plan ──────────────────────────────────────────────────────
    follows_planned  = min(WARMUP_MAX_FOLLOWS, len(targets))
    likes_per_visit  = random.randint(WARMUP_LIKES_MIN, WARMUP_LIKES_MAX)
    likes_planned    = follows_planned * likes_per_visit
    browse_minutes   = max(1, (WARMUP_DURATION - follows_planned * 45) // 60)

    plan = {
        "follows":  {"planned": follows_planned,  "done": 0, "failed": 0},
        "likes":    {"planned": likes_planned,     "done": 0, "failed": 0},
        "browse_minutes": browse_minutes,
    }

    log.info(
        f"[{bot.username}] Warmup plan: "
        f"follow {follows_planned} accounts | "
        f"like ~{likes_planned} posts ({likes_per_visit}/profile) | "
        f"browse feed ~{browse_minutes} min (with mood shifts)"
    )

    # ── Phase 1: Visit each target with mood-based browsing between visits ──
    log.info(f"[{bot.username}] ── Phase 1: visiting & following {follows_planned} profiles")
    for i, url in enumerate(targets[:follows_planned], 1):
        # Brief mood-based feed browse before each profile (except the first)
        if i > 1:
            mood = _new_mood()
            browse_secs = random.randint(20, 50)
            log.info(f"[{bot.username}]   → feed browse ({mood['name']} mood, {browse_secs}s)")
            _browse_feed(bot, time.time() + browse_secs, mood)

        log.info(f"[{bot.username}]   [{i}/{follows_planned}] {url}")
        try:
            bot.go(url)
            time.sleep(random.uniform(2, 4))

            # Like posts on this profile
            n = _like_visible_posts(bot, likes_per_visit)
            plan["likes"]["done"] += n
            if n < likes_per_visit:
                plan["likes"]["failed"] += likes_per_visit - n
            log.info(f"[{bot.username}]   Liked {n}/{likes_per_visit} posts {'✓' if n else '✗'}")

            # Follow
            if _click_follow(bot):
                plan["follows"]["done"] += 1
                log.info(f"[{bot.username}]   Followed ✓")
            else:
                plan["follows"]["failed"] += 1
                log.info(f"[{bot.username}]   Follow failed ✗")

            time.sleep(random.uniform(3, 7))

        except Exception as e:
            err = str(e)
            if "invalid session id" in err or "not connected to DevTools" in err or "disconnected" in err:
                log.error(f"[{bot.username}] Browser session lost — aborting warmup")
                _log_plan_summary(bot, plan)
                raise
            plan["follows"]["failed"] += 1
            log.warning(f"[{bot.username}]   Error on {url}: {e}")

    _log_phase_result(bot, "follows", plan["follows"])

    # ── Phase 2: Extended mood-based feed browsing for remaining time ────────
    log.info(f"[{bot.username}] ── Phase 2: browsing feed for ~{browse_minutes} min")
    end_time = time.time() + browse_minutes * 60
    _browse_feed(bot, end_time)
    log.info(f"[{bot.username}]   Feed browse complete ✓")

    # ── Summary ─────────────────────────────────────────────────────────────
    _log_plan_summary(bot, plan)
    log.info(f"[{bot.username}] Warmup complete")


# ---------------------------------------------------------------------------
# Mood system
# ---------------------------------------------------------------------------

_MOODS = [
    {
        "name": "casual",
        "scroll_min": 250, "scroll_max": 600,
        "read_chance": 0.25, "read_min": 2, "read_max": 7,
        "like_chance": 0.08,
        "gap_min": 1.0,  "gap_max": 3.5,
    },
    {
        "name": "engaged",
        "scroll_min": 150, "scroll_max": 400,
        "read_chance": 0.45, "read_min": 4, "read_max": 12,
        "like_chance": 0.15,
        "gap_min": 2.0,  "gap_max": 6.0,
    },
    {
        "name": "fast",
        "scroll_min": 500, "scroll_max": 1000,
        "read_chance": 0.08, "read_min": 1, "read_max": 3,
        "like_chance": 0.04,
        "gap_min": 0.3,  "gap_max": 1.5,
    },
    {
        "name": "distracted",
        "scroll_min": 300, "scroll_max": 700,
        "read_chance": 0.15, "read_min": 8, "read_max": 25,
        "like_chance": 0.06,
        "gap_min": 3.0,  "gap_max": 10.0,
    },
]

def _new_mood():
    return random.choice(_MOODS)


# ---------------------------------------------------------------------------
# Feed browsing
# ---------------------------------------------------------------------------

def _browse_feed(bot, end_time, mood=None):
    bot.go_home()
    if mood is None:
        mood = _new_mood()
    mood_ticks = 0
    mood_shift_at = random.randint(4, 8)

    while time.time() < end_time:
        try:
            # Shift mood every few ticks
            mood_ticks += 1
            if mood_ticks >= mood_shift_at:
                mood = _new_mood()
                mood_ticks = 0
                mood_shift_at = random.randint(4, 8)
                bot.log.info(f"[{bot.username}]   Mood → {mood['name']}")

            scroll_px = random.randint(mood["scroll_min"], mood["scroll_max"])
            bot.driver.execute_script(f"window.scrollBy(0, {scroll_px});")

            if random.random() < mood["read_chance"]:
                time.sleep(random.uniform(mood["read_min"], mood["read_max"]))
            else:
                time.sleep(random.uniform(mood["gap_min"], mood["gap_max"]))

            # Low-chance like while browsing (main likes done in phase 1)
            if random.random() < mood["like_chance"]:
                _like_visible_posts(bot, 1)

        except Exception as e:
            err = str(e)
            if "invalid session id" in err or "not connected to DevTools" in err:
                raise
            time.sleep(3)


# ---------------------------------------------------------------------------
# Like posts (top-level only, no comment likes)
# ---------------------------------------------------------------------------

def _like_visible_posts(bot, count):
    """Like up to `count` top-level posts only — skips comment likes."""
    liked = 0
    try:
        btns = bot.driver.execute_script("""
            const results = [];
            const articles = document.querySelectorAll('article, div[data-pressable-container="true"]');
            for (const article of articles) {
                const btn = article.querySelector(
                    '[role="button"] svg[aria-label="Like"], svg[aria-label="Like"]'
                );
                if (btn) {
                    let el = btn;
                    for (let i = 0; i < 6; i++) {
                        if (el && el.getAttribute && el.getAttribute('role') === 'button') break;
                        el = el.parentElement;
                    }
                    if (el && el.offsetWidth > 0) results.push(el);
                }
            }
            return results;
        """)
        random.shuffle(btns)
        for btn in btns:
            if liked >= count:
                break
            try:
                bot.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(random.uniform(0.4, 1.0))
                btn.click()
                liked += 1
                time.sleep(random.uniform(1.2, 3))
            except Exception:
                continue
    except Exception:
        pass
    return liked


# ---------------------------------------------------------------------------
# Follow button
# ---------------------------------------------------------------------------

def _click_follow(bot) -> bool:
    for xpath in [
        '//*[@role="button" and normalize-space(.)="Follow"]',
        '//button[normalize-space()="Follow"]',
        '//*[@role="button" and .//span[normalize-space(.)="Follow"]]',
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
# Logging helpers
# ---------------------------------------------------------------------------

def _log_phase_result(bot, phase_name, result):
    planned = result["planned"]
    done    = result["done"]
    failed  = result["failed"]
    status  = "✓" if failed == 0 else ("~" if done > 0 else "✗")
    bot.log.info(
        f"[{bot.username}] Phase '{phase_name}': "
        f"{done}/{planned} done, {failed} failed {status}"
    )


def _log_plan_summary(bot, plan):
    bot.log.info(f"[{bot.username}] ── Warmup summary ──────────────────")
    for key, val in plan.items():
        if isinstance(val, dict):
            _log_phase_result(bot, key, val)
    bot.log.info(f"[{bot.username}] ────────────────────────────────────")


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
