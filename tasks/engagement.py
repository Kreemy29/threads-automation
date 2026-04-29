"""
Engagement tasks:
  - AI-powered outreach comments (reads post + comments context → DeepSeek)
  - Follow tier-1 country users throughout the day
  - Unfollow previously followed users on schedule
"""
import os
import re
import random
import time

import requests as _requests

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import data.db as db
from config import (
    OUTREACH_TARGETS,
    OUTREACH_COMMENTS_MIN,
    OUTREACH_COMMENTS_MAX,
    FOLLOW_BATCH_SIZE,
    UNFOLLOW_AFTER_SECONDS,
)

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "..", "content")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


# ──────────────────────────────────────────────────────────────────────────────
# American name filter
# ──────────────────────────────────────────────────────────────────────────────

def filter_american_names(names: list[str], api_key: str) -> list[str]:
    """
    Send a batch of display names / handles to DeepSeek and return only those
    that are likely American. Falls back to returning all names if API fails.
    """
    if not api_key or not names:
        return names

    names_list = "\n".join(f"- {n}" for n in names)
    prompt = (
        "You are given a list of social media display names or usernames. "
        "Return ONLY the ones that are very likely to belong to an American (US) person "
        "based on the name itself — common US first names, American-style usernames, English names, etc. "
        "Ignore names that are clearly non-American (Arabic, Spanish, Asian, etc.). "
        "If unsure, exclude it. Reply with just the matching names, one per line, no extras.\n\n"
        f"{names_list}"
    )

    try:
        resp = _requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.2,
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Parse returned names — strip bullets/dashes
        filtered = [
            line.lstrip("-• ").strip()
            for line in raw.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        # Only keep names that were in the original list (LLM sometimes hallucinates)
        names_lower = {n.lower(): n for n in names}
        result = [names_lower[f.lower()] for f in filtered if f.lower() in names_lower]
        return result if result else names  # fallback if nothing matched
    except Exception:
        return names  # on any error, don't filter


def _extract_commenter_names(bot) -> list[tuple[str, str]]:
    """
    Returns list of (display_name, username) from visible comments on the current page.
    """
    js = """
    const results = [];
    const articles = document.querySelectorAll('article, div[role="article"]');
    for (const a of articles) {
        const nameEl = a.querySelector('a[href*="/@"] span, a[href*="/t/"] ~ span');
        const hrefEl = a.querySelector('a[href*="/@"]');
        if (hrefEl) {
            const href = hrefEl.getAttribute('href') || '';
            const match = href.match(/\\/@([^/]+)/);
            const username = match ? match[1] : '';
            const displayName = nameEl ? nameEl.textContent.trim() : username;
            if (username) results.push([displayName, username]);
        }
    }
    return JSON.stringify(results);
    """
    try:
        raw = bot.driver.execute_script(js)
        pairs = __import__("json").loads(raw)
        return [(p[0], p[1]) for p in pairs if p[0] and p[1]]
    except Exception:
        return []


def _load_lines(filename):
    path = os.path.join(CONTENT_DIR, filename)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# ──────────────────────────────────────────────────────────────────────────────
# AI comment generation
# ──────────────────────────────────────────────────────────────────────────────

def _extract_post_context(bot) -> str:
    """
    Extract post text + visible comments from the current open reply dialog.
    Returns a combined context string for the LLM.
    """
    js = """
    function getContext() {
        const dialog = document.querySelector('div[role="dialog"]');
        const root   = dialog || document;

        // Grab all visible text spans, filter noise
        const spans = Array.from(root.querySelectorAll('span[dir="auto"]'))
            .filter(s => !s.closest('div[role="button"]'))
            .map(s => s.textContent.trim())
            .filter(t => t.length > 8 && !['Cancel','Reply','Add a topic','Like','Share'].includes(t));

        // Sort longest first — post body is usually the longest span
        spans.sort((a, b) => b.length - a.length);

        // First item = post body, rest = comments (cap at 3)
        const postBody   = spans[0] || "";
        const comments   = spans.slice(1, 4);
        return JSON.stringify({ post: postBody, comments });
    }
    return getContext();
    """
    try:
        raw = bot.driver.execute_script(js)
        data = __import__("json").loads(raw)
        post = data.get("post", "")
        comments = data.get("comments", [])
        context = f"Post: {post}"
        if comments:
            context += "\n\nTop comments:\n" + "\n".join(f"- {c}" for c in comments)
        return context
    except Exception as e:
        bot.log.warning(f"[{bot.username}] Context extraction failed: {e}")
        return ""


def _generate_ai_comment(api_key: str, context: str) -> str | None:
    """Call DeepSeek to generate a short, natural comment based on post context."""
    if not api_key or not context:
        return None

    system = (
        "You are a real girl on social media leaving short, genuine comments on posts. "
        "Keep it 1 sentence max, casual and human. Use 1 emoji if it fits naturally. "
        "No hashtags. No 'as an AI'. Never mention you're a bot. "
        "ONLY output the comment itself, nothing else."
    )
    user = (
        f"Here is the post and some comments:\n\n{context}\n\n"
        "Write ONE short, natural reply comment."
    )

    try:
        resp = _requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                "max_tokens": 60,
                "temperature": 0.85,
            },
            timeout=25,
        )
        resp.raise_for_status()
        comment = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip surrounding quotes if LLM added them
        comment = comment.strip('"').strip("'")

        # If model returned multiple options separated by "or", take first
        or_match = re.match(r'^(.+?)(?:\s+or\s+.+)', comment, re.IGNORECASE | re.DOTALL)
        if or_match:
            comment = or_match.group(1).strip()

        return comment
    except Exception as e:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Outreach comments
# ──────────────────────────────────────────────────────────────────────────────

def run_outreach_comments(bot, already_done_today: int, api_key: str = ""):
    log = bot.log
    target_count = random.randint(OUTREACH_COMMENTS_MIN, OUTREACH_COMMENTS_MAX)
    remaining = target_count - already_done_today
    if remaining <= 0:
        log.info(f"[{bot.username}] Outreach quota met ({already_done_today})")
        return 0

    fallback_comments = _load_lines("comments.txt")
    done = 0

    targets = random.sample(OUTREACH_TARGETS, min(remaining, len(OUTREACH_TARGETS)))
    for target_url in targets:
        if done >= remaining:
            break
        try:
            post_url = _get_recent_post_url(bot, target_url)
            if not post_url:
                log.warning(f"[{bot.username}] No recent post at {target_url}")
                continue

            # Scan commenters and follow American-looking ones
            _follow_american_commenters(bot, post_url, api_key, log)

            ok = _post_comment_on(bot, post_url, api_key, fallback_comments)
            if ok:
                done += 1
                db.log_action(bot.username, "outreach_comment", "ok", post_url)
                log.info(f"[{bot.username}] Comment posted ({done}/{remaining})")

            time.sleep(random.uniform(60, 180))
        except Exception as e:
            log.warning(f"[{bot.username}] Outreach error for {target_url}: {e}")

    return done


def _follow_american_commenters(bot, post_url: str, api_key: str, log):
    """
    Visit a post, extract commenter names, filter for American-sounding ones,
    follow up to 5 of them.
    """
    bot.go(post_url)
    time.sleep(2)

    pairs = _extract_commenter_names(bot)
    if not pairs:
        return

    display_names = [p[0] for p in pairs]
    username_map  = {p[0]: p[1] for p in pairs}

    if api_key:
        american_names = filter_american_names(display_names, api_key)
        log.info(f"[{bot.username}] American commenters: {american_names[:5]} / {len(display_names)} total")
    else:
        american_names = display_names  # no filter without API key

    to_follow = [username_map[n] for n in american_names[:5] if n in username_map]

    for username in to_follow:
        try:
            bot.go(f"https://www.threads.net/@{username}")
            time.sleep(1.5)
            if _click_follow_button(bot):
                log.info(f"[{bot.username}] Followed American commenter @{username}")
                db.add_to_follow_queue(bot.username, [username], UNFOLLOW_AFTER_SECONDS)
            time.sleep(random.uniform(2, 5))
        except Exception as e:
            log.warning(f"[{bot.username}] Could not follow @{username}: {e}")


def _get_recent_post_url(bot, profile_url: str) -> str | None:
    bot.go(profile_url)
    time.sleep(2)
    try:
        links = bot.driver.find_elements(
            By.XPATH, '//a[contains(@href,"/post/") or contains(@href,"/t/")]'
        )
        if links:
            return links[0].get_attribute("href")
    except Exception:
        pass
    return None


def _post_comment_on(bot, post_url: str, api_key: str, fallback_comments: list) -> bool:
    log = bot.log
    bot.go(post_url)
    time.sleep(random.uniform(2, 3.5))

    # Click the middle reply button (like original project)
    reply_btns = bot.driver.find_elements(By.CSS_SELECTOR, "svg[aria-label='Reply']")
    if not reply_btns:
        log.warning(f"[{bot.username}] No reply buttons found on {post_url}")
        return False

    target_btn = reply_btns[len(reply_btns) // 2]  # middle button

    # Scroll to post and "read" it naturally before clicking
    bot.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target_btn)
    time.sleep(random.uniform(2, 4))

    for click_fn in [
        lambda: target_btn.click(),
        lambda: bot.driver.execute_script("arguments[0].click();", target_btn),
        lambda: target_btn.find_element(By.XPATH, "..").click(),
    ]:
        try:
            click_fn()
            time.sleep(1.5)
            break
        except Exception:
            continue

    # Extract context for AI
    context = _extract_post_context(bot)

    # Generate AI comment or fall back to static
    comment_text = None
    if api_key and context:
        comment_text = _generate_ai_comment(api_key, context)
        if comment_text:
            log.info(f"[{bot.username}] AI comment: {comment_text[:60]}")

    if not comment_text:
        comment_text = random.choice(fallback_comments) if fallback_comments else "Love this 🔥"
        log.info(f"[{bot.username}] Fallback comment: {comment_text[:60]}")

    # Find comment field
    textbox = None
    for xpath in [
        '//div[@role="textbox"]',
        '//div[@aria-label="Write a comment..."]',
        '//div[@aria-label="Reply..."]',
    ]:
        try:
            textbox = WebDriverWait(bot.driver, 6).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            break
        except Exception:
            continue

    if not textbox:
        log.warning(f"[{bot.username}] Comment textbox not found")
        try:
            ActionChains(bot.driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
        return False

    if not bot.paste_text(textbox, comment_text):
        log.warning(f"[{bot.username}] Could not type comment")
        return False

    time.sleep(random.uniform(1.5, 3))
    return _click_comment_post_button(bot, textbox)


def _click_comment_post_button(bot, textbox) -> bool:
    """Multi-strategy Post button click for comment dialogs."""
    for btn_text in ["Post", "Reply", "Share", "Send", "Comment"]:
        for xpath in [
            f"//div[text()='{btn_text}' or ./span[text()='{btn_text}']]",
            f"//button[text()='{btn_text}' or ./span[text()='{btn_text}']]",
            f"//div[contains(text(),'{btn_text}')][@role='button']",
        ]:
            try:
                btns = bot.driver.find_elements(By.XPATH, xpath)
                for b in btns:
                    if b.is_displayed() and b.is_enabled():
                        b.click()
                        time.sleep(3)
                        bot.log.info(f"[{bot.username}] Comment submitted")
                        return True
            except Exception:
                continue

    # JS fallback
    try:
        result = bot.driver.execute_script("""
            const els = document.querySelectorAll('div[role="button"], button');
            for (const el of els) {
                const t = el.textContent.trim().toLowerCase();
                if (['post','reply','share','send','comment'].includes(t) &&
                    el.getAttribute('aria-disabled') !== 'true' &&
                    el.offsetWidth > 0) {
                    el.click(); return true;
                }
            }
            return false;
        """)
        if result:
            time.sleep(3)
            return True
    except Exception:
        pass

    # Enter key last resort
    try:
        bot.driver.execute_script("""
            arguments[0].dispatchEvent(new KeyboardEvent('keydown',
                {key:'Enter',code:'Enter',keyCode:13,bubbles:true,cancelable:true}));
        """, textbox)
        time.sleep(2)
        return True
    except Exception:
        pass

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Follow tier-1 users
# ──────────────────────────────────────────────────────────────────────────────

def run_follow_batch(bot, tier1_users: list):
    log = bot.log
    if not tier1_users:
        return
    batch = random.sample(tier1_users, min(FOLLOW_BATCH_SIZE, len(tier1_users)))
    followed = []
    for username in batch:
        try:
            bot.go(f"{bot.BASE_URL}/@{username}")
            if _click_follow_button(bot):
                followed.append(username)
                log.info(f"[{bot.username}] Followed @{username}")
            time.sleep(random.uniform(3, 8))
        except Exception as e:
            log.warning(f"[{bot.username}] Follow error @{username}: {e}")

    if followed:
        db.add_to_follow_queue(bot.username, followed, UNFOLLOW_AFTER_SECONDS)
        db.log_action(bot.username, "follow_batch", "ok", f"{len(followed)} users")


def _click_follow_button(bot) -> bool:
    for xpath in [
        '//div[@role="button" and text()="Follow"]',
        '//span[text()="Follow"]/ancestor::div[@role="button"]',
        '//button[normalize-space()="Follow"]',
    ]:
        try:
            el = WebDriverWait(bot.driver, 6).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            bot.smart_click(el)
            time.sleep(1)
            return True
        except Exception:
            continue
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Unfollow due users
# ──────────────────────────────────────────────────────────────────────────────

def run_due_unfollows(bot):
    log = bot.log
    due = db.get_due_unfollows(bot.username)
    if not due:
        return
    log.info(f"[{bot.username}] Unfollowing {len(due)} users")
    for row in due:
        try:
            bot.go(f"{bot.BASE_URL}/@{row['target']}")
            if _click_unfollow_button(bot):
                db.mark_unfollowed(row["id"])
                log.info(f"[{bot.username}] Unfollowed @{row['target']}")
            time.sleep(random.uniform(2, 5))
        except Exception as e:
            log.warning(f"[{bot.username}] Unfollow error: {e}")


def _click_unfollow_button(bot) -> bool:
    for xpath in [
        '//div[@role="button" and text()="Following"]',
        '//div[@role="button" and text()="Unfollow"]',
        '//span[text()="Following"]/ancestor::div[@role="button"]',
    ]:
        try:
            el = WebDriverWait(bot.driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            bot.smart_click(el)
            time.sleep(1.5)
            for confirm in ['//div[@role="button" and text()="Unfollow"]', '//button[normalize-space()="Unfollow"]']:
                try:
                    c = WebDriverWait(bot.driver, 3).until(EC.element_to_be_clickable((By.XPATH, confirm)))
                    c.click()
                    time.sleep(1)
                    break
                except Exception:
                    pass
            return True
        except Exception:
            continue
    return False
