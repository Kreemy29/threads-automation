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
            log.error("Not logged in to Threads — aborting. Log in manually first.")
            db.update_account(username, state="error", notes="not logged in")
            return

        # Use state passed by coordinator (pre-"running"), fall back to DB read
        if state is None or state == "running":
            account = db.get_account(username)
            state = account.get("state", "pending")
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
            warmup_start = account.get("warmup_start")
            if warmup_start:
                elapsed = (datetime.utcnow() - datetime.fromisoformat(warmup_start)).total_seconds()
                remaining = max(0, WARMUP_DURATION - elapsed)
            else:
                remaining = WARMUP_DURATION
                db.update_account(username, warmup_start=datetime.utcnow().isoformat())

            # Load the account's assigned follow list
            follow_list_name = account.get("follow_list", "")
            follow_list = db.get_follow_list(follow_list_name) if follow_list_name else []

            if remaining > 0:
                log.info(f"Warmup — {remaining/60:.0f} min remaining, "
                         f"follow list: '{follow_list_name}' ({len(follow_list)} handles)")
                from tasks.warmup import run_warmup
                run_warmup(bot, follow_list=follow_list or None)
            else:
                log.info("Warmup already completed")

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

    next_post_time = datetime.utcnow()
    next_follow_time = datetime.utcnow() + timedelta(minutes=random.randint(20, 40))
    next_unfollow_check = datetime.utcnow() + timedelta(minutes=10)
    next_comment_time = _random_comment_time()

    log.info("Entering 24-hour active SOP loop")

    while True:
        now = datetime.utcnow()

        # ── Posting cycle ──────────────────────────────────────────
        if now >= next_post_time:
            log.info("Running posting cycle...")
            results = run_posting_cycle(bot, media_folder=media_folder)
            ok_count = sum(1 for r in results if r)
            db.log_action(username, "posting_cycle", "ok", f"{ok_count}/{len(results)} posted")
            interval = random.randint(POST_CYCLE_MIN, POST_CYCLE_MAX)
            next_post_time = now + timedelta(seconds=interval)
            log.info(f"Next posting cycle in {interval//60} min")

        # ── Outreach comments ──────────────────────────────────────
        if now >= next_comment_time:
            daily = db.get_daily_counts(username)
            done = run_outreach_comments(bot, daily["comments"])  # uses GEMINI_API_KEY from config
            log.info(f"Outreach: {done} comments posted today total")
            next_comment_time = _random_comment_time()

        # ── Follow batch ───────────────────────────────────────────
        if now >= next_follow_time and tier1_users:
            run_follow_batch(bot, tier1_users)
            next_follow_time = now + timedelta(minutes=random.randint(45, 90))
            log.info(f"Next follow batch in ~{(next_follow_time - now).seconds // 60} min")

        # ── Unfollow check ─────────────────────────────────────────
        if now >= next_unfollow_check:
            run_due_unfollows(bot)
            next_unfollow_check = now + timedelta(minutes=15)

        # ── Idle scroll (keep session alive) ──────────────────────
        _idle_scroll(bot, seconds=random.randint(30, 90))


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
