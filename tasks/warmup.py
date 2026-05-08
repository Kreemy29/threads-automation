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

            // Strategy 1: SVG with aria-label containing "like" (case-insensitive)
            const svgs = document.querySelectorAll('svg[aria-label]');
            for (const svg of svgs) {
                const label = (svg.getAttribute('aria-label') || '').toLowerCase();
                if (!label.includes('like')) continue;
                // Walk up to find the clickable button ancestor
                let el = svg;
                for (let i = 0; i < 8; i++) {
                    if (!el) break;
                    const role = el.getAttribute && el.getAttribute('role');
                    const tag  = el.tagName && el.tagName.toLowerCase();
                    if (role === 'button' || tag === 'button') break;
                    el = el.parentElement;
                }
                if (el && el.offsetWidth > 0) results.push(el);
            }

            // Strategy 2: role=button with aria-label containing "like"
            if (results.length === 0) {
                const btns = document.querySelectorAll('[role="button"][aria-label]');
                for (const b of btns) {
                    const label = (b.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('like') && b.offsetWidth > 0) results.push(b);
                }
            }

            // Strategy 3: any button containing an SVG (post action buttons)
            if (results.length === 0) {
                const containers = document.querySelectorAll('article, [data-pressable-container]');
                for (const c of containers) {
                    const btns = c.querySelectorAll('[role="button"]');
                    for (const b of btns) {
                        if (b.querySelector('svg') && b.offsetWidth > 0) results.push(b);
                    }
                    if (results.length >= 10) break;
                }
            }

            return results;
        """)

        if not btns:
            # Log what SVG labels exist on the page for debugging
            labels = bot.driver.execute_script("""
                return Array.from(document.querySelectorAll('svg[aria-label]'))
                    .map(s => s.getAttribute('aria-label')).filter(Boolean).slice(0, 20);
            """)
            bot.log.debug(f"[{bot.username}] No like buttons found. SVG labels on page: {labels}")

        random.shuffle(btns)
        for btn in btns:
            if liked >= count:
                break
            try:
                bot.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(random.uniform(0.4, 1.0))
                bot.driver.execute_script("arguments[0].click();", btn)
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
    # Close any hover preview cards or modals so we don't click their Follow
    bot.dismiss_overlays()
    time.sleep(0.5)

    # XPath strategies — ordered from most to least specific
    for xpath in [
        '//*[@role="button" and normalize-space(.)="Follow"]',
        '//button[normalize-space()="Follow"]',
        '//*[@role="button" and .//span[normalize-space(.)="Follow"]]',
        '//*[@role="button" and contains(@aria-label,"Follow")]',
        '//div[@role="button" and contains(.,"Follow") and not(contains(.,"Following"))]',
    ]:
        try:
            el = WebDriverWait(bot.driver, 4).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            bot.smart_click(el)
            time.sleep(random.uniform(1.0, 2.0))
            return True
        except Exception:
            continue

    # JS fallback — skips overlays, tries both "Follow" text and aria-label
    try:
        result = bot.driver.execute_script("""
            function inOverlay(el) {
                let n = el;
                while (n && n !== document.body) {
                    const role = n.getAttribute && n.getAttribute('role');
                    if (role === 'dialog' || role === 'tooltip') return true;
                    const cs = window.getComputedStyle(n);
                    if (cs && (cs.position === 'fixed' || cs.position === 'absolute')) {
                        const z = parseInt(cs.zIndex || '0', 10);
                        if (z >= 100) return true;
                    }
                    n = n.parentElement;
                }
                return false;
            }
            const btns = document.querySelectorAll('[role="button"], button');
            for (const b of btns) {
                if (b.offsetWidth === 0) continue;
                if (inOverlay(b)) continue;
                const txt   = (b.innerText || b.textContent || '').trim();
                const label = (b.getAttribute('aria-label') || '').toLowerCase();
                const isFollow = (txt === 'Follow') ||
                                 (label === 'follow') ||
                                 (txt.startsWith('Follow') && !txt.includes('Following'));
                if (!isFollow) continue;
                b.click();
                return 'clicked:' + txt;
            }
            // Debug: return all visible button texts
            const visible = Array.from(btns)
                .filter(b => b.offsetWidth > 0)
                .map(b => (b.innerText || b.textContent || '').trim().slice(0, 30))
                .filter(t => t.length > 0);
            return 'not_found:' + JSON.stringify(visible.slice(0, 15));
        """)
        if result and result.startswith('clicked:'):
            bot.log.debug(f"[{bot.username}]   Follow clicked via JS ({result})")
            time.sleep(random.uniform(1.0, 2.0))
            return True
        else:
            bot.log.debug(f"[{bot.username}]   Follow JS result: {result}")
    except Exception as e:
        bot.log.debug(f"[{bot.username}]   Follow JS error: {e}")

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
