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
    GEMINI_API_KEY,
    PIC_COMMENT_RATIO,
)

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "..", "content")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


# ──────────────────────────────────────────────────────────────────────────────
# American name filter
# ──────────────────────────────────────────────────────────────────────────────

def filter_american_names(names, api_key: str) -> list:
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
            GEMINI_URL,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 300, "temperature": 0.2},
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
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


def _extract_commenter_names(bot) -> list:
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


def _pick_random_image():
    """Return a random image path from content/images/, or None if empty."""
    images_dir = os.path.join(CONTENT_DIR, "images")
    if not os.path.isdir(images_dir):
        return None
    candidates = [
        os.path.join(images_dir, f)
        for f in os.listdir(images_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]
    return random.choice(candidates) if candidates else None


# ──────────────────────────────────────────────────────────────────────────────
# AI comment generation
# ──────────────────────────────────────────────────────────────────────────────

def _extract_post_context(bot) -> str:
    """
    Extract post text + visible comments from the current post page.
    Call this BEFORE clicking the reply button, while the full post is visible.
    Returns a combined context string for the LLM.
    """
    js = """
    function getContext() {
        const result = { post: "", comments: [] };

        // --- Post body ---
        // Threads renders post text in a div[data-pressable-container] > article
        // or directly in the first article on the page.
        // Strategy: find the longest non-trivial text block not inside a nav/button.
        const NOISE = new Set([
            'cancel','reply','add a topic','like','share','follow','following',
            'see more','translate','report','copy link','embed','quote'
        ]);

        function cleanText(el) {
            return (el.innerText || el.textContent || '').trim();
        }

        // Walk articles / sections looking for the post body
        const articles = document.querySelectorAll('article, section[data-pressable-container]');
        let bestLen = 0;
        for (const article of articles) {
            // Skip obvious comment-section articles (nested deep)
            const depth = article.closest('article') ? 99 : 0;
            if (depth > 0) continue;

            const txt = cleanText(article);
            if (txt.length > bestLen && !NOISE.has(txt.toLowerCase())) {
                bestLen = txt.length;
                result.post = txt.slice(0, 500);  // cap at 500 chars
            }
        }

        // Fallback: grab longest span[dir="auto"] not in a button
        if (!result.post) {
            const spans = Array.from(document.querySelectorAll('span[dir="auto"]'))
                .filter(s => !s.closest('[role="button"]') && !s.closest('nav'))
                .map(s => s.textContent.trim())
                .filter(t => t.length > 15 && !NOISE.has(t.toLowerCase()));
            spans.sort((a, b) => b.length - a.length);
            result.post = spans[0] || "";
        }

        // --- Comments (top visible ones, excluding the post author's text) ---
        // Comments appear as reply articles after the main post
        const postText = result.post.slice(0, 60).toLowerCase();
        const commentSpans = Array.from(document.querySelectorAll('span[dir="auto"]'))
            .filter(s => !s.closest('[role="button"]') && !s.closest('nav'))
            .map(s => s.textContent.trim())
            .filter(t =>
                t.length > 5 && t.length < 200 &&
                !NOISE.has(t.toLowerCase()) &&
                !postText.includes(t.toLowerCase().slice(0, 30))
            );

        // Deduplicate and take first 5
        const seen = new Set();
        for (const c of commentSpans) {
            if (!seen.has(c)) {
                seen.add(c);
                result.comments.push(c);
                if (result.comments.length >= 5) break;
            }
        }

        return JSON.stringify(result);
    }
    return getContext();
    """
    try:
        raw = bot.driver.execute_script(js)
        data = __import__("json").loads(raw)
        post = data.get("post", "").strip()
        comments = data.get("comments", [])

        if not post:
            return ""

        context = f"Post: {post}"
        if comments:
            context += "\n\nSome comments on this post:\n" + "\n".join(f"- {c}" for c in comments[:5])
        return context
    except Exception as e:
        bot.log.warning(f"[{bot.username}] Context extraction failed: {e}")
        return ""


def _generate_ai_comment(api_key: str, context: str):
    """Call Gemini to generate a short, natural comment based on post context."""
    if not api_key or not context:
        return None

    prompt = (
        "You are a real LA girl leaving a comment on someone's Threads post. "
        "You talk like someone from Los Angeles — use natural US/LA slang: "
        "lowkey, slay, no cap, periodt, it's giving, rent free, understood the assignment, fr fr, bestie, etc. "
        "Mix in 1-3 emojis naturally (not all at the end). Keep it short — 1 sentence, 2 max. "
        "Sound like a real person hyping someone up or reacting authentically — "
        "reference something specific from the post, don't be generic. "
        "No hashtags. No quotes around your output. ONLY output the comment.\n\n"
        f"{context}\n\n"
        "Your comment:"
    )

    try:
        resp = _requests.post(
            GEMINI_URL,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 100,
                    "temperature": 1.1,
                },
            },
            timeout=25,
        )
        resp.raise_for_status()
        comment = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Strip surrounding quotes if model added them
        comment = comment.strip('"').strip("'")

        # If model returned multiple options separated by "or", take first
        or_match = re.match(r'^(.+?)(?:\s+or\s+.+)', comment, re.IGNORECASE | re.DOTALL)
        if or_match:
            comment = or_match.group(1).strip()

        return comment
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Outreach comments
# ──────────────────────────────────────────────────────────────────────────────

def run_outreach_comments(bot, already_done_today: int, api_key: str = None):
    log = bot.log
    if api_key is None:
        api_key = GEMINI_API_KEY  # use Gemini by default
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


def _get_recent_post_url(bot, profile_url: str):
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
    time.sleep(random.uniform(2.5, 4))

    # Extract post context BEFORE clicking reply — full post text is visible here
    context = _extract_post_context(bot)
    log.info(f"[{bot.username}] Post context ({len(context)} chars): {context[:120]}")

    # Generate AI comment while we can still see the full post
    comment_text = None
    if api_key and context:
        comment_text = _generate_ai_comment(api_key, context)
        if comment_text:
            log.info(f"[{bot.username}] AI comment: {comment_text[:80]}")

    if not comment_text:
        if fallback_comments:
            comment_text = random.choice(fallback_comments)
        else:
            comment_text = "Love this 🔥"
        log.info(f"[{bot.username}] Fallback comment: {comment_text[:60]}")

    # Click the first (main post) reply button
    reply_btns = bot.driver.find_elements(By.CSS_SELECTOR, "svg[aria-label='Reply']")
    if not reply_btns:
        log.warning(f"[{bot.username}] No reply buttons found on {post_url}")
        return False

    # Use the first reply button (top-level post, not a comment reply)
    target_btn = reply_btns[0]

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

    # Optionally attach an image based on pic_comment_ratio setting
    use_pic = random.randint(1, 100) <= PIC_COMMENT_RATIO
    if use_pic:
        img_path = _pick_random_image()
        if img_path:
            try:
                file_input = WebDriverWait(bot.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
                )
                file_input.send_keys(os.path.abspath(img_path))
                log.info(f"[{bot.username}] Pic comment: attached {os.path.basename(img_path)}")
                time.sleep(3)  # wait for upload preview
            except Exception as e:
                log.warning(f"[{bot.username}] Pic comment upload failed: {e}")
        else:
            log.info(f"[{bot.username}] Pic comment skipped — no images in content/images/")

    # Re-find textbox after potential image upload
    try:
        textbox = WebDriverWait(bot.driver, 5).until(
            EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
        )
    except Exception:
        pass

    if not bot.paste_text(textbox, comment_text):
        log.warning(f"[{bot.username}] Could not type comment")
        return False

    # Fire React events so the Post button becomes enabled
    try:
        bot.driver.execute_script("""
            arguments[0].dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
            arguments[0].dispatchEvent(new Event('change', {bubbles: true}));
        """, textbox)
    except Exception:
        pass

    time.sleep(random.uniform(2.5, 4))  # give React time to enable the submit button
    return _click_comment_post_button(bot, textbox)


def _click_comment_post_button(bot, textbox) -> bool:
    """Multi-strategy Post button click for comment dialogs."""

    # Wait up to 5s for a non-disabled Post/Reply button to appear
    try:
        btn = WebDriverWait(bot.driver, 5).until(lambda d: _find_enabled_submit_btn(d))
        btn.click()
        time.sleep(3)
        bot.log.info(f"[{bot.username}] Comment submitted")
        return True
    except Exception:
        pass

    # JS fallback — also checks aria-label for icon-only buttons
    try:
        result = bot.driver.execute_script("""
            const labels = ['post','reply','share','send','comment'];
            const els = document.querySelectorAll('div[role="button"], button');
            for (const el of els) {
                const t = (el.textContent || '').trim().toLowerCase();
                const label = (el.getAttribute('aria-label') || '').toLowerCase();
                const disabled = el.getAttribute('aria-disabled') === 'true'
                               || el.disabled
                               || el.classList.contains('disabled');
                if (!disabled && el.offsetWidth > 0 &&
                    (labels.includes(t) || labels.some(l => label.includes(l)))) {
                    el.click(); return true;
                }
            }
            return false;
        """)
        if result:
            time.sleep(3)
            bot.log.info(f"[{bot.username}] Comment submitted via JS")
            return True
    except Exception:
        pass

    # Enter key last resort
    try:
        ActionChains(bot.driver).move_to_element(textbox).click().send_keys(Keys.RETURN).perform()
        time.sleep(2)
        bot.log.info(f"[{bot.username}] Comment submitted via Enter key")
        return True
    except Exception:
        pass

    bot.log.warning(f"[{bot.username}] Could not find submit button for comment")
    return False


def _find_enabled_submit_btn(driver):
    labels = ["post", "reply", "share", "send", "comment"]
    els = driver.find_elements(By.XPATH, '//*[@role="button" or self::button]')
    for el in els:
        try:
            if not el.is_displayed():
                continue
            text = (el.text or "").strip().lower()
            aria = (el.get_attribute("aria-label") or "").lower()
            disabled = (el.get_attribute("aria-disabled") == "true"
                        or el.get_attribute("disabled") is not None)
            if disabled:
                continue
            if any(l == text or l in aria for l in labels):
                return el
        except Exception:
            continue

    # Fallback: find any non-disabled button near a textbox (Threads uses icon-only send buttons)
    try:
        result = driver.execute_script("""
            const textbox = document.querySelector('[role="textbox"]');
            if (!textbox) return null;
            // Walk up to find a form-like container, then look for any enabled button
            let container = textbox;
            for (let i = 0; i < 8; i++) {
                container = container.parentElement;
                if (!container) break;
                const btns = container.querySelectorAll('[role="button"], button');
                for (const b of btns) {
                    const disabled = b.getAttribute('aria-disabled') === 'true' || b.disabled;
                    const visible = b.offsetWidth > 0 && b.offsetHeight > 0;
                    // Skip if it looks like a cancel/close button
                    const txt = (b.textContent || '').trim().toLowerCase();
                    if (!disabled && visible && txt !== 'cancel' && txt !== 'close') {
                        return b;
                    }
                }
            }
            return null;
        """)
        return result
    except Exception:
        return None


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
        '//*[@role="button" and normalize-space(.)="Follow"]',
        '//button[normalize-space()="Follow"]',
        '//*[@role="button" and .//span[normalize-space(.)="Follow"]]',
    ]:
        try:
            el = WebDriverWait(bot.driver, 6).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            bot.smart_click(el)
            time.sleep(random.uniform(1.0, 2.0))
            return True
        except Exception:
            continue
    # JS fallback
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
        '//*[@role="button" and normalize-space(.)="Following"]',
        '//*[@role="button" and .//span[normalize-space(.)="Following"]]',
    ]:
        try:
            el = WebDriverWait(bot.driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            bot.smart_click(el)
            time.sleep(1.5)
            for confirm in [
                '//*[@role="button" and normalize-space(.)="Unfollow"]',
                '//button[normalize-space()="Unfollow"]',
            ]:
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
