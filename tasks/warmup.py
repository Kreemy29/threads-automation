"""
Warmup phase — interleaved browsing + follows.

Execution model:
  1. Split follow targets into small batches (1–3 each).
  2. Distribute those batches as checkpoints spread randomly across
     the warmup window.
  3. Browse feed between checkpoints with mood shifts.
  4. At each checkpoint: visit profiles, like + follow, return to feed.

Warmup always runs to completion before the active cycle starts.
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

    follows_planned = min(WARMUP_MAX_FOLLOWS, len(targets))
    likes_per_visit = random.randint(WARMUP_LIKES_MIN, WARMUP_LIKES_MAX)
    total_window    = WARMUP_DURATION  # seconds

    plan = {
        "follows": {"planned": follows_planned, "done": 0, "failed": 0},
        "likes":   {"planned": follows_planned * likes_per_visit, "done": 0, "failed": 0},
    }

    # Split targets into small random batches of 1–3
    batches = _split_batches(targets[:follows_planned], min_size=1, max_size=3)

    log.info(
        f"[{bot.username}] Warmup plan: "
        f"follow {follows_planned} accounts in {len(batches)} batch(es) | "
        f"like ~{likes_per_visit}/profile | "
        f"total window ~{total_window//60}m (follows interleaved with feed browsing)"
    )

    # Place each follow batch at a random checkpoint inside the window.
    # Reserve ~45s per profile for visiting + liking + following.
    profile_cost = follows_planned * 45
    browse_budget = max(60, total_window - profile_cost)

    # Generate sorted checkpoint times (seconds from now)
    checkpoints = sorted(random.sample(
        range(30, browse_budget, max(1, browse_budget // (len(batches) + 1))),
        min(len(batches), browse_budget // 60)
    )) if len(batches) > 0 else []

    # Pad/trim to match number of batches
    while len(checkpoints) < len(batches):
        checkpoints.append(random.randint(checkpoints[-1] + 30 if checkpoints else 30, browse_budget))
    checkpoints = sorted(checkpoints[:len(batches)])

    log.info(
        f"[{bot.username}] Follow checkpoints at: "
        + ", ".join(f"{s//60}m{s%60:02d}s" for s in checkpoints)
    )

    # ── Main warmup loop ────────────────────────────────────────────────────
    start_time     = time.time()
    end_time       = start_time + total_window
    batch_index    = 0
    checkpoint_abs = [start_time + s for s in checkpoints]

    bot.go_home()
    mood = _new_mood()
    mood_ticks     = 0
    mood_shift_at  = random.randint(4, 8)

    log.info(f"[{bot.username}] ── Starting interleaved warmup browse (mood: {mood['name']}) ──")

    while time.time() < end_time:
        now = time.time()

        # ── Fire next follow batch when its checkpoint arrives ──────────────
        if batch_index < len(checkpoint_abs) and now >= checkpoint_abs[batch_index]:
            batch = batches[batch_index]
            batch_index += 1
            log.info(
                f"[{bot.username}] ── Follow checkpoint {batch_index}/{len(batches)}: "
                f"{len(batch)} profile(s)"
            )
            for j, url in enumerate(batch, 1):
                log.info(f"[{bot.username}]   [{j}/{len(batch)}] {url}")
                try:
                    bot.go(url)
                    time.sleep(random.uniform(2, 4))

                    n = _like_visible_posts(bot, likes_per_visit)
                    plan["likes"]["done"] += n
                    if n < likes_per_visit:
                        plan["likes"]["failed"] += likes_per_visit - n
                    log.info(f"[{bot.username}]   Liked {n}/{likes_per_visit} {'✓' if n else '✗'}")

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

            # Return to feed after batch
            bot.go_home()
            mood = _new_mood()
            log.info(f"[{bot.username}]   Back to feed (mood: {mood['name']})")
            continue  # skip the scroll step this tick

        # ── Regular feed scroll ─────────────────────────────────────────────
        try:
            mood_ticks += 1
            if mood_ticks >= mood_shift_at:
                mood = _new_mood()
                mood_ticks = 0
                mood_shift_at = random.randint(4, 8)
                log.info(f"[{bot.username}]   Mood → {mood['name']}")

            scroll_px = random.randint(mood["scroll_min"], mood["scroll_max"])
            bot.driver.execute_script(f"window.scrollBy(0, {scroll_px});")

            if random.random() < mood["read_chance"]:
                time.sleep(random.uniform(mood["read_min"], mood["read_max"]))
            else:
                time.sleep(random.uniform(mood["gap_min"], mood["gap_max"]))

            # Low-chance opportunistic like while scrolling
            if random.random() < mood["like_chance"]:
                _like_visible_posts(bot, 1)

        except Exception as e:
            err = str(e)
            if "invalid session id" in err or "not connected to DevTools" in err:
                raise
            time.sleep(3)

    # Fire any remaining batches that didn't hit their checkpoint (shouldn't happen, but safety net)
    while batch_index < len(batches):
        batch = batches[batch_index]
        batch_index += 1
        log.info(f"[{bot.username}] ── Late follow batch {batch_index}/{len(batches)}: {len(batch)} profile(s)")
        for url in batch:
            try:
                bot.go(url)
                time.sleep(random.uniform(2, 4))
                n = _like_visible_posts(bot, likes_per_visit)
                plan["likes"]["done"] += n
                if _click_follow(bot):
                    plan["follows"]["done"] += 1
                else:
                    plan["follows"]["failed"] += 1
                time.sleep(random.uniform(3, 7))
            except Exception as e:
                plan["follows"]["failed"] += 1
                log.warning(f"[{bot.username}]   Error on {url}: {e}")

    _log_plan_summary(bot, plan)
    log.info(f"[{bot.username}] Warmup complete ✓")


# ---------------------------------------------------------------------------
# Batch splitting
# ---------------------------------------------------------------------------

def _split_batches(targets: list, min_size=1, max_size=3) -> list:
    """Split a flat list into sub-lists of random size [min_size, max_size]."""
    batches = []
    i = 0
    while i < len(targets):
        size = random.randint(min_size, min(max_size, len(targets) - i))
        batches.append(targets[i:i + size])
        i += size
    return batches


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
# Like posts (top-level only, no comment likes)
# ---------------------------------------------------------------------------

def _like_visible_posts(bot, count):
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
            bot.smart_click(el)
            time.sleep(random.uniform(1.0, 2.0))
            return True
        except Exception:
            continue
    try:
        result = bot.driver.execute_script("""
            const btns = document.querySelectorAll('[role="button"], button');
            for (const b of btns) {
                const txt = (b.innerText || b.textContent || '').trim();
                if (txt === 'Follow' && b.offsetWidth > 0) { b.click(); return true; }
            }
            return false;
        """)
        if result:
            time.sleep(random.uniform(1.0, 2.0))
            return True
    except Exception:
        pass
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
        f"[{bot.username}] '{phase_name}': {done}/{planned} done, {failed} failed {status}"
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
