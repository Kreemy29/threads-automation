"""
worker.py — runs one Threads account through its full lifecycle.
Executed as a separate process by the coordinator.

State machine:
    pending  →  setup  →  warmup  →  active  (loops 24h)
                                   ↓
                                 error  (coordinator can retry)
"""
import os
import sys
import time
import random
import logging
from datetime import datetime, timedelta

import data.db as db
from config import (
    GROK_API_KEY,
    POST_CYCLE_MIN,
    POST_CYCLE_MAX,
    SESSION_DURATION,
    OUTREACH_COMMENTS_MIN,
    OUTREACH_COMMENTS_MAX,
    FOLLOW_BATCH_SIZE,
    TEXT_POSTS_PER_CYCLE,
    ACTIVE_LIKES_MIN,
    ACTIVE_LIKES_MAX,
    WARMUP_ENABLED,
    WARMUP_DURATION,
)
from core.bot import ThreadsBot
from tasks.setup import run_setup
from tasks.warmup import run_warmup, _new_mood
from tasks.posting import run_posting_cycle, post_text, post_image, _load_lines, _generate_post
from tasks.engagement import run_outreach_comments, run_follow_batch, run_due_unfollows
from tasks.telegram_task import run_mother_repost


# ------------------------------------------------------------------
# Logging setup (per-account file)
# ------------------------------------------------------------------

def _make_logger(username):
    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"{username}.log")

    logger = logging.getLogger(username)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)

        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)  # console stays clean; DEBUG goes to file only
        ch.setFormatter(logging.Formatter(f"[{username}] %(asctime)s %(message)s", datefmt="%H:%M:%S"))
        ch.stream = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False)
        logger.addHandler(ch)

    return logger


# ------------------------------------------------------------------
# Tier-1 follow targets (loaded from content/tier1_users.txt)
# ------------------------------------------------------------------

def _load_tier1_users():
    path = os.path.join(os.path.dirname(__file__), "content", "tier1_users.txt")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().lstrip("@") for line in f if line.strip()]


# ------------------------------------------------------------------
# Main worker entry point
# ------------------------------------------------------------------

def run_account(username, adspower_id, state=None):
    """Full lifecycle for one account. Called in a child process."""
    log = _make_logger(username)
    log.info(f"Worker started — username={username}, adspower_id={adspower_id}")

    bot = ThreadsBot(username, adspower_id, log)

    try:
        bot.open_browser()

        if not bot.is_logged_in():
            log.error("Not logged in to Threads — pausing. Log in via AdsPower manually, then click Reset in the UI.")
            db.update_account(username, state="error", notes="not logged in")
            return

        # Use state passed by coordinator (pre-"running"), fall back to DB read
        if state is None or state == "running":
            account = db.get_account(username)
            state = account.get("state", "pending")

        # Treat error as pending — retry from the top
        if state == "error":
            log.info("Account was in error state — resetting to pending for retry")
            db.update_account(username, state="pending", notes="", retry_count=0,
                              warmup_done=0, setup_done=0, warmup_start=None)
            state = "pending"

        log.info(f"Account state: {state}")

        # ── SETUP ────────────────────────────────────────────────────
        if state == "pending":
            db.update_account(username, state="setup")
            log.info("Running setup...")
            ok = run_setup(bot)
            db.log_action(username, "setup", "ok" if ok else "failed")
            if ok:
                db.update_account(username, setup_done=1, state="warmup",
                                  warmup_start=datetime.utcnow().isoformat())
                state = "warmup"
            else:
                db.update_account(username, state="error", notes="setup failed")
                return

        # ── WARMUP ───────────────────────────────────────────────────
        if state == "warmup":
            account = db.get_account(username)
            follow_list_name = account.get("follow_list", "")
            follow_list = db.get_follow_list(follow_list_name) if follow_list_name else []

            if not WARMUP_ENABLED:
                log.info("Warmup disabled in settings — skipping")
            elif account.get("warmup_done"):
                log.info("Warmup already done for this account — skipping")
            else:
                db.update_account(username, warmup_start=datetime.utcnow().isoformat())
                log.info(f"Warmup starting — follow list: '{follow_list_name}' ({len(follow_list)} handles)")
                run_warmup(bot, follow_list=follow_list or None)

            db.update_account(username, warmup_done=1, state="active")
            db.log_action(username, "warmup", "ok" if WARMUP_ENABLED else "skipped")
            state = "active"

        # ── ACTIVE (time-bounded session) ────────────────────────────
        if state == "active":
            account = db.get_account(username)
            media_folder = account.get("media_folder", "")
            _run_active_loop(bot, username, log, media_folder)
            # Clean exit after session — reset to active so coordinator re-queues
            db.update_account(username, state="active")
            db.log_action(username, "session", "ok")

    except KeyboardInterrupt:
        log.info("Interrupted by user")
    except Exception as e:
        log.exception(f"Fatal error: {e}")
        err = str(e).lower()
        if "profile not found" in err or "does not exist" in err:
            db.update_account(username, state="invalid", notes=str(e))
            log.error(f"Marked {username} as invalid — fix AdsPower profile ID in accounts.txt")
        else:
            db.update_account(username, state="error", notes=str(e))
        db.log_action(username, "worker", "error", str(e))
    finally:
        try:
            bot.close_browser()
        except Exception as e:
            log.debug(f"close_browser failed: {e}")
        log.info("Worker finished")


# ------------------------------------------------------------------
# Active loop — unified task queue
# ------------------------------------------------------------------

def _run_active_loop(bot, username, log, media_folder: str = ""):
    tier1_users = _load_tier1_users()
    account = db.get_account(username)

    # Repost/quote the mother's post once at the start (if enabled and not already done)
    from config import MOTHER_REPOST_ENABLED
    if MOTHER_REPOST_ENABLED and not account.get("telegram_posted"):
        ok = run_mother_repost(bot)
        db.update_account(username, telegram_posted=1 if ok else 0,
                          telegram_pinned=1 if ok else 0)
        db.log_action(username, "mother_repost", "ok" if ok else "failed")
        time.sleep(random.uniform(30, 60))

    session_secs = SESSION_DURATION
    session_end  = datetime.utcnow() + timedelta(seconds=session_secs)
    log.info(
        f"[{username}] Session started — {session_secs // 60}m window, "
        f"ends at {session_end.strftime('%H:%M:%S')} UTC"
    )

    # Build one task queue sized to fill the full session
    task_queue = _build_task_queue(1, tier1_users, media_folder, session_secs)
    _log_queue_plan(log, username, 1, task_queue, session_secs)

    # Always start on the home feed so scrolling has something to render
    try:
        bot.go_home()
    except Exception:
        pass

    while datetime.utcnow() < session_end:
        # ── Fire every task that is now due ────────────────────────
        while task_queue and datetime.utcnow() >= task_queue[0]["fire_at"]:
            if datetime.utcnow() >= session_end:
                break

            task = task_queue.pop(0)
            _execute_task(bot, username, log, task, media_folder)

            # After most tasks, return to the feed so we can scroll while waiting
            if task["type"] in ("post_text", "post_image", "post_ghost", "comment", "follow", "unfollow_check"):
                try:
                    bot.go_home()
                except Exception:
                    pass

            remaining = len(task_queue)
            time_left = max(0, int((session_end - datetime.utcnow()).total_seconds()))
            if remaining > 0:
                secs_until_next = max(0, int(
                    (task_queue[0]["fire_at"] - datetime.utcnow()).total_seconds()
                ))
                log.info(
                    f"[{username}] {remaining} task(s) remaining — "
                    f"next '{task_queue[0]['label']}' in ~{secs_until_next//60}m{secs_until_next%60:02d}s "
                    f"| session ends in {time_left//60}m{time_left%60:02d}s"
                )
            else:
                log.info(
                    f"[{username}] All tasks done — scrolling for remaining "
                    f"{time_left//60}m{time_left%60:02d}s"
                )

        # ── Fill any waiting time with continuous scrolling ────────
        if task_queue:
            wait_secs = max(0, int(
                (task_queue[0]["fire_at"] - datetime.utcnow()).total_seconds()
            ))
        else:
            wait_secs = max(0, int((session_end - datetime.utcnow()).total_seconds()))

        if wait_secs > 0:
            # Scroll in short bursts (~15–25s) so we re-check the queue often
            burst = min(wait_secs, random.randint(15, 25))
            _idle_scroll(bot, seconds=burst)

    log.info(f"[{username}] Session complete — exiting cleanly")


# ------------------------------------------------------------------
# Task queue builder — every account gets its own randomized queue
# ------------------------------------------------------------------

# Slot share per task type (must sum to ~1.0).  Used to allocate slots
# proportionally so every cycle has a mix of action types instead of
# being dominated by whichever type has the most candidates.
_TASK_MIX = {
    "post":     0.30,
    "like":     0.25,
    "follow":   0.18,
    "comment":  0.12,
    "scroll":   0.15,
}

# When trimming extras (count > 1), reduce in this order
_TRIM_ORDER = ["scroll", "post", "like", "follow", "comment"]


def _build_task_queue(cycle_num: int, tier1_users: list, media_folder: str,
                      cycle_secs: int) -> list:
    """
    Build a flat list of micro-tasks sized to fit within `cycle_secs`,
    with a balanced mix across all action types.  Each account's queue
    is independently randomized — counts, order, and timing all differ.
    """

    # ── How many task slots fit? ───────────────────────────────────
    # Tasks spaced ~2–4 min apart; at least 5 slots so we always get
    # post + like + follow + comment + something extra.
    avg_gap_secs = random.randint(120, 240)
    n_slots = max(5, cycle_secs // avg_gap_secs)

    # ── Allocate slots per task type ──────────────────────────────
    counts = {k: max(1, round(n_slots * share)) for k, share in _TASK_MIX.items()}
    if not tier1_users:
        counts["follow"] = 0

    include_unfollow = True

    # ── Trim to fit n_slots ───────────────────────────────────────
    # Drop in tiers: extras → unfollow_check → scroll → core engagement.
    # This keeps the four core actions (post, like, follow, comment)
    # whenever possible.
    def total():
        return sum(counts.values()) + (1 if include_unfollow else 0)

    while total() > n_slots:
        # 1. Trim any task type that has > 1 (extras)
        trimmed = False
        for k in _TRIM_ORDER:
            if counts.get(k, 0) > 1:
                counts[k] -= 1
                trimmed = True
                break
        if trimmed:
            continue

        # 2. Drop unfollow_check
        if include_unfollow:
            include_unfollow = False
            continue

        # 3. Drop scroll (last optional item)
        if counts.get("scroll", 0) > 0:
            counts["scroll"] = 0
            continue

        # 4. Last resort — drop one core engagement type
        for k in ["post", "follow", "like", "comment"]:
            if counts.get(k, 0) > 0:
                counts[k] = 0
                break
        else:
            break

    # ── Materialize tasks ──────────────────────────────────────────
    tasks = []

    # Posts (split between text, image, and ghost)
    from config import GHOST_POSTS_PER_CYCLE
    n_post = counts.get("post", 0)
    n_ghost = min(n_post, GHOST_POSTS_PER_CYCLE or 0)
    remaining_post = n_post - n_ghost
    n_image = 1 if remaining_post >= 2 else 0
    n_text  = min(remaining_post - n_image, TEXT_POSTS_PER_CYCLE)
    for i in range(n_text):
        tasks.append({"type": "post_text", "label": f"text post {i + 1}"})
    if n_image:
        tasks.append({"type": "post_image", "label": "image post"})
    for i in range(n_ghost):
        tasks.append({"type": "post_ghost", "label": f"ghost post {i + 1}"})

    for _ in range(counts.get("like", 0)):
        n = random.randint(ACTIVE_LIKES_MIN, max(ACTIVE_LIKES_MIN, ACTIVE_LIKES_MAX))
        tasks.append({"type": "like", "label": f"like {n} posts", "count": n})

    if counts.get("follow", 0) > 0 and tier1_users:
        n_follow = counts["follow"]
        sample_size = min(n_follow * 3, len(tier1_users))
        pool_users = random.sample(tier1_users, sample_size)
        for _ in range(n_follow):
            if not pool_users:
                break
            size = random.randint(1, min(3, len(pool_users)))
            batch = pool_users[:size]
            pool_users = pool_users[size:]
            tasks.append({"type": "follow", "label": f"follow {size} user(s)", "users": batch})

    for i in range(counts.get("comment", 0)):
        tasks.append({"type": "comment", "label": f"comment {i + 1}"})

    for _ in range(counts.get("scroll", 0)):
        secs = random.randint(20, 60)
        tasks.append({"type": "scroll", "label": f"scroll {secs}s", "seconds": secs})

    tasks.append({"type": "unfollow_check", "label": "unfollow check"})

    # ── Distribute fire_at evenly across the cycle window with jitter ──
    random.shuffle(tasks)
    now = datetime.utcnow()
    n = len(tasks)
    if n == 0:
        return []
    slot_size = cycle_secs / (n + 1)

    for i, task in enumerate(tasks):
        base = (i + 1) * slot_size
        jitter = random.uniform(-0.2, 0.2) * slot_size
        offset = max(60, min(cycle_secs - 30, int(base + jitter)))
        task["fire_at"] = now + timedelta(seconds=offset)

    tasks.sort(key=lambda t: t["fire_at"])
    return tasks


def _log_queue_plan(log, username, cycle_num, task_queue, cycle_secs):
    cycle_mins = cycle_secs // 60
    lines = [
        f"[{username}] ── Cycle {cycle_num}: {len(task_queue)} tasks "
        f"over {cycle_mins}m window ──"
    ]
    now = datetime.utcnow()
    for task in task_queue:
        fire_in = max(0, int((task["fire_at"] - now).total_seconds()))
        lines.append(f"  [{fire_in//60:>2}m{fire_in%60:02d}s]  {task['label']}")
    log.info("\n".join(lines))


# ------------------------------------------------------------------
# Task executor
# ------------------------------------------------------------------

def _execute_task(bot, username, log, task, media_folder):
    t = task["type"]

    if t == "post_text":
        log.info(f"[{username}] → {task['label']}")
        ok = _post_one_text(bot, media_folder)
        _log_step(log, username, task["label"], ok)

    elif t == "post_image":
        log.info(f"[{username}] → {task['label']}")
        ok = _post_one_image(bot, media_folder)
        _log_step(log, username, task["label"], ok)

    elif t == "post_ghost":
        log.info(f"[{username}] → {task['label']}")
        ok = _post_one_ghost(bot, media_folder)
        _log_step(log, username, task["label"], ok)

    elif t == "like":
        count = task.get("count", 2)
        log.info(f"[{username}] → {task['label']}")
        liked = _like_on_feed(bot, count)
        # Likes can leave hover preview cards open — close them
        try:
            bot.dismiss_overlays()
        except Exception:
            pass
        _log_step(log, username, f"liked {liked}/{count}", liked > 0)

    elif t == "follow":
        users = task.get("users", [])
        log.info(f"[{username}] → {task['label']}: {users}")
        run_follow_batch(bot, users)
        db.log_action(username, "follow", "ok", f"{len(users)} users")

    elif t == "comment":
        log.info(f"[{username}] → {task['label']}")
        daily = db.get_daily_counts(username)
        done = run_outreach_comments(bot, daily["comments"])
        _log_step(log, username, f"comment ({done} posted)", done > 0)

    elif t == "scroll":
        secs = task.get("seconds", 30)
        log.info(f"[{username}] → {task['label']}")
        _idle_scroll(bot, seconds=secs)

    elif t == "unfollow_check":
        log.info(f"[{username}] → {task['label']}")
        run_due_unfollows(bot)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _log_step(log, username, step, ok):
    log.info(f"[{username}]   {step}: {'✓' if ok else '✗'}")


def _post_one_text(bot, media_folder):
    from tasks.posting import post_text, _load_lines, _generate_post
    from config import GROK_API_KEY
    import os as _os, random as _r
    examples = _load_lines(_os.path.join(_os.path.dirname(__file__), "content", "text_posts.txt"))
    if not examples:
        return False
    example = random.choice(examples)
    text = _generate_post(GROK_API_KEY, example) or example
    return post_text(bot, text)


def _post_one_image(bot, media_folder):
    try:
        return post_image(bot, media_folder=media_folder)
    except Exception as e:
        # Surface upload/compose failures instead of silently returning False.
        bot.log.warning(f"[{bot.username}] image post raised: {e}")
        return False


def _post_one_ghost(bot, media_folder):
    from tasks.posting import post_ghost
    try:
        return post_ghost(bot, media_folder=media_folder)
    except Exception:
        return False


def _like_on_feed(bot, count: int) -> int:
    """Navigate home and like `count` posts from the feed."""
    from tasks.warmup import _like_visible_posts
    try:
        bot.go_home()
        time.sleep(random.uniform(1.5, 3))
        # Scroll a little so the feed has loaded posts
        bot.driver.execute_script(f"window.scrollBy(0, {random.randint(300, 600)});")
        time.sleep(random.uniform(1, 2))
        return _like_visible_posts(bot, count)
    except Exception:
        return 0


def _idle_scroll(bot, seconds=60):
    """Mood-based feed scroll to keep the session alive.

    Picks a mood (casual / engaged / fast / distracted) and scrolls with that
    mood's pacing — variable scroll distance, read pauses, occasional likes.
    Does NOT navigate home — caller is responsible for being on the feed
    so successive bursts feel continuous.
    """
    from tasks.warmup import _new_mood, _like_visible_posts

    mood = _new_mood()
    end  = time.time() + seconds
    try:
        # If we ended up off the feed (post detail / profile), come back
        cur = (bot.driver.current_url or "").lower()
        if "/post/" in cur or "/t/" in cur or "/@" in cur:
            bot.go_home()

        while time.time() < end:
            scroll_px = random.randint(mood["scroll_min"], mood["scroll_max"])
            bot.driver.execute_script(f"window.scrollBy(0, {scroll_px});")

            if random.random() < mood["read_chance"]:
                time.sleep(random.uniform(mood["read_min"], mood["read_max"]))
            else:
                time.sleep(random.uniform(mood["gap_min"], mood["gap_max"]))

            # Occasional opportunistic like while scrolling
            if random.random() < mood["like_chance"]:
                _like_visible_posts(bot, 1)
    except Exception:
        pass



# ------------------------------------------------------------------
# Process entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python worker.py <username> <adspower_id>")
        sys.exit(1)
    db.init_db()
    run_account(sys.argv[1], sys.argv[2])
