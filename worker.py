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
    POST_CYCLE_MIN,
    POST_CYCLE_MAX,
    OUTREACH_COMMENTS_MIN,
    OUTREACH_COMMENTS_MAX,
    FOLLOW_BATCH_SIZE,
    TEXT_POSTS_PER_CYCLE,
    WARMUP_DURATION,
)
from core.bot import ThreadsBot
from tasks.setup import run_setup
from tasks.warmup import run_warmup
from tasks.posting import run_posting_cycle
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

            if not account.get("warmup_done"):
                # Always reset warmup_start so the full duration runs from now.
                # (A stale warmup_start from a previous crash would cause elapsed >>
                # WARMUP_DURATION, making remaining=0 and silently skipping warmup.)
                db.update_account(username, warmup_start=datetime.utcnow().isoformat())
                log.info(f"Warmup starting — follow list: '{follow_list_name}' ({len(follow_list)} handles)")
                from tasks.warmup import run_warmup
                run_warmup(bot, follow_list=follow_list or None)
            else:
                log.info("Warmup already done for this account — skipping")

            db.update_account(username, warmup_done=1, state="active")
            db.log_action(username, "warmup", "ok")
            state = "active"

        # ── ACTIVE (24-hour SOP loop) ─────────────────────────────────
        if state == "active":
            account = db.get_account(username)
            media_folder = account.get("media_folder", "")
            _run_active_loop(bot, username, log, media_folder)

    except KeyboardInterrupt:
        log.info("Interrupted by user")
    except Exception as e:
        log.exception(f"Fatal error: {e}")
        err = str(e).lower()
        # Permanent failures — don't retry automatically
        if "profile not found" in err or "does not exist" in err:
            db.update_account(username, state="invalid", notes=str(e))
            log.error(f"Marked {username} as invalid — fix AdsPower profile ID in accounts.txt")
        else:
            db.update_account(username, state="error", notes=str(e))
        db.log_action(username, "worker", "error", str(e))
    finally:
        try:
            bot.close_browser()
        except Exception:
            pass
        log.info("Worker finished")


# ------------------------------------------------------------------
# Active SOP loop
# ------------------------------------------------------------------

def _run_active_loop(bot, username, log, media_folder: str = ""):
    tier1_users = _load_tier1_users()
    account = db.get_account(username)

    # Repost/quote the mother's post once, then pin it
    if not account.get("telegram_posted"):
        ok = run_mother_repost(bot)
        db.update_account(username, telegram_posted=1 if ok else 0,
                          telegram_pinned=1 if ok else 0)
        db.log_action(username, "mother_repost", "ok" if ok else "failed")
        time.sleep(random.uniform(30, 60))

    next_cycle_time      = datetime.utcnow()
    next_follow_time     = datetime.utcnow() + timedelta(minutes=random.randint(20, 40))
    next_unfollow_check  = datetime.utcnow() + timedelta(minutes=10)
    next_comment_time    = _random_comment_time()
    cycle_num            = 0

    # Post queue: individual tasks fired with gaps instead of all at once
    # Each item: ("text"|"image", task_label)
    post_queue: list = []
    next_queued_post_time = datetime.utcnow()

    log.info("Entering 24-hour active SOP loop")

    while True:
        now = datetime.utcnow()

        # ── Refill post queue when cycle window opens ──────────────
        if now >= next_cycle_time and not post_queue:
            cycle_num += 1
            plan = _build_cycle_plan(cycle_num)
            _log_cycle_plan(log, username, plan)

            tasks = [("text", i + 1) for i in range(plan["text_posts"])]
            tasks.append(("image", plan["text_posts"] + 1))
            random.shuffle(tasks)  # unpredictable ordering each cycle
            post_queue = tasks
            next_queued_post_time = now  # start firing immediately

            interval = random.randint(POST_CYCLE_MIN, POST_CYCLE_MAX)
            next_cycle_time = now + timedelta(seconds=interval)
            log.info(f"[{username}] Cycle {cycle_num}: {len(post_queue)} tasks queued — next cycle in {interval//60}m")

        # ── Fire one queued post ───────────────────────────────────
        if post_queue and now >= next_queued_post_time:
            task_type, task_num = post_queue.pop(0)

            if task_type == "text":
                log.info(f"[{username}] Queued post {task_num} (text)…")
                ok = _post_one_text(bot, media_folder)
                _log_step(log, username, f"text post {task_num}", ok)
            else:
                log.info(f"[{username}] Queued post {task_num} (image)…")
                ok = _post_one_image(bot, media_folder)
                _log_step(log, username, "image post", ok)

            if post_queue:
                # Space remaining posts 5–25 minutes apart
                gap = random.randint(5, 25) * 60
                next_queued_post_time = datetime.utcnow() + timedelta(seconds=gap)
                log.info(f"[{username}] {len(post_queue)} post(s) remaining — next in {gap//60}m")

        # ── Outreach comments ──────────────────────────────────────
        if now >= next_comment_time:
            daily   = db.get_daily_counts(username)
            planned = random.randint(OUTREACH_COMMENTS_MIN, OUTREACH_COMMENTS_MAX)
            log.info(f"[{username}] Outreach plan: {planned} comments (done today: {daily['comments']})")
            done = run_outreach_comments(bot, daily["comments"])
            _log_step(log, username, f"outreach comments ({done}/{planned})", done > 0)
            next_comment_time = _random_comment_time()

        # ── Follow mini-batch (1-3 at a time, not the whole batch) ─
        if now >= next_follow_time and tier1_users:
            mini_size = random.randint(1, min(3, len(tier1_users)))
            mini_batch = random.sample(tier1_users, mini_size)
            log.info(f"[{username}] Mini-follow: {mini_size} tier-1 user(s)")
            run_follow_batch(bot, mini_batch)
            gap = random.randint(20, 50)
            next_follow_time = now + timedelta(minutes=gap)
            log.info(f"[{username}] Next follow check in {gap}m")

        # ── Unfollow check ─────────────────────────────────────────
        if now >= next_unfollow_check:
            run_due_unfollows(bot)
            next_unfollow_check = now + timedelta(minutes=15)

        # ── Idle scroll (keep session alive) ──────────────────────
        _idle_scroll(bot, seconds=random.randint(30, 90))


def _build_cycle_plan(cycle_num):
    return {
        "cycle":      cycle_num,
        "text_posts": TEXT_POSTS_PER_CYCLE,
        "image_post": 1,
    }


def _log_cycle_plan(log, username, plan):
    log.info(
        f"[{username}] ── Cycle {plan['cycle']} plan: "
        f"{plan['text_posts']} text + {plan['image_post']} image (shuffled, spaced 5–25m apart)"
    )


def _log_step(log, username, step, ok):
    status = "✓" if ok else "✗"
    log.info(f"[{username}]   {step}: {status}")


def _post_one_text(bot, media_folder):
    from tasks.posting import post_text, _load_lines, _generate_post
    from config import GEMINI_API_KEY
    import os, random as _r
    examples = _load_lines(os.path.join(
        os.path.dirname(__file__), "content", "text_posts.txt"
    ))
    if not examples:
        return False
    example = _r.choice(examples)
    text = _generate_post(GEMINI_API_KEY, example) or example
    return post_text(bot, text)


def _post_one_image(bot, media_folder):
    from tasks.posting import post_image
    try:
        return post_image(bot, media_folder=media_folder)
    except Exception:
        return False


def _active_browse(bot, username, log, seconds=60):
    """Short mood-based feed browse used between active SOP actions."""
    from tasks.warmup import _new_mood
    mood = _new_mood()
    log.info(f"[{username}]   browsing feed ({mood['name']} mood, {seconds}s)")
    end = time.time() + seconds
    try:
        bot.go_home()
        while time.time() < end:
            bot.driver.execute_script(
                f"window.scrollBy(0, {random.randint(mood['scroll_min'], mood['scroll_max'])});"
            )
            if random.random() < mood["read_chance"]:
                time.sleep(random.uniform(mood["read_min"], mood["read_max"]))
            else:
                time.sleep(random.uniform(mood["gap_min"], mood["gap_max"]))
    except Exception:
        pass


def _random_comment_time():
    """Return a future datetime for the next outreach comment."""
    hours_from_now = random.uniform(2, 6)
    return datetime.utcnow() + timedelta(hours=hours_from_now)


def _idle_scroll(bot, seconds=60):
    """Light scrolling to keep the session alive between tasks."""
    import time as _time
    end = _time.time() + seconds
    bot.go_home()
    while _time.time() < end:
        bot.scroll_down(random.randint(300, 800))
        _time.sleep(random.uniform(1.5, 4))


# ------------------------------------------------------------------
# Process entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    # Called directly: python worker.py <username> <adspower_id>
    if len(sys.argv) != 3:
        print("Usage: python worker.py <username> <adspower_id>")
        sys.exit(1)
    db.init_db()
    run_account(sys.argv[1], sys.argv[2])
