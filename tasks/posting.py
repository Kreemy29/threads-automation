"""
Posting tasks:
  - 4 text posts per cycle (from text_posts.txt)
  - 1 image post per cycle (random image + caption from captions.txt)
"""
import os
import random
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "..", "content")


_PLACEHOLDER_MARKERS = (
    "add your", "replace with", "delete this", "make sure you have",
    "each post will", "one per line", "posting cycles",
)

def _load_lines(filename):
    path = os.path.join(CONTENT_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        lines = []
        for line in f:
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if any(marker in low for marker in _PLACEHOLDER_MARKERS):
                continue  # skip template/instruction lines
            lines.append(line)
    return lines


def _pick_image(media_folder: str = "") -> str:
    """Pick a random image from the account's media folder, or shared content/images/ as fallback."""
    candidates = []

    # Per-account media folder first
    if media_folder and os.path.isdir(media_folder):
        candidates = [
            os.path.join(media_folder, f)
            for f in os.listdir(media_folder)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".mp4"))
        ]

    # Fallback to shared images folder
    if not candidates:
        shared = os.path.join(CONTENT_DIR, "images")
        if os.path.isdir(shared):
            candidates = [
                os.path.join(shared, f)
                for f in os.listdir(shared)
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".mp4"))
            ]

    if not candidates:
        raise FileNotFoundError(
            f"No images found in media_folder='{media_folder}' or content/images/"
        )
    return random.choice(candidates)


# ------------------------------------------------------------------
# Text post
# ------------------------------------------------------------------

def post_text(bot, text=None):
    log = bot.log
    if text is None:
        text = random.choice(_load_lines("text_posts.txt"))

    log.info(f"[{bot.username}] Text post: {text[:60]}...")

    for attempt in range(3):
        textbox = bot.open_compose()
        if not textbox:
            log.warning(f"[{bot.username}] Could not open compose (attempt {attempt+1})")
            time.sleep(3)
            continue

        if not bot.paste_text(textbox, text):
            log.warning(f"[{bot.username}] paste_text failed (attempt {attempt+1})")
            time.sleep(2)
            continue

        time.sleep(1)
        if bot.click_post_button():
            log.info(f"[{bot.username}] Text post published")
            return True

        log.warning(f"[{bot.username}] click_post_button failed (attempt {attempt+1})")
        time.sleep(3)

    log.error(f"[{bot.username}] Text post failed after 3 attempts")
    return False


# ------------------------------------------------------------------
# Image post
# ------------------------------------------------------------------

def post_image(bot, image_path=None, caption=None, media_folder: str = ""):
    log = bot.log
    if image_path is None:
        image_path = _pick_image(media_folder)
    if caption is None:
        caption = random.choice(_load_lines("captions.txt"))

    log.info(f"[{bot.username}] Image post: {os.path.basename(image_path)} | {caption[:40]}...")

    for attempt in range(3):
        textbox = bot.open_compose()
        if not textbox:
            log.warning(f"[{bot.username}] Could not open compose (attempt {attempt+1})")
            time.sleep(3)
            continue

        # Upload the image via file input
        try:
            file_input = WebDriverWait(bot.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
            )
            file_input.send_keys(os.path.abspath(image_path))
            log.info(f"[{bot.username}] Image selected")
            time.sleep(5)
        except Exception as e:
            log.warning(f"[{bot.username}] File upload failed (attempt {attempt+1}): {e}")
            time.sleep(3)
            continue

        # Re-find textbox after upload
        try:
            textbox = WebDriverWait(bot.driver, 8).until(
                EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
            )
        except Exception:
            log.warning(f"[{bot.username}] Textbox gone after upload")
            time.sleep(2)
            continue

        if not bot.paste_text(textbox, caption):
            log.warning(f"[{bot.username}] Caption paste failed (attempt {attempt+1})")

        time.sleep(1)
        if bot.click_post_button():
            log.info(f"[{bot.username}] Image post published")
            return True

        log.warning(f"[{bot.username}] click_post_button failed (attempt {attempt+1})")
        time.sleep(3)

    log.error(f"[{bot.username}] Image post failed after 3 attempts")
    return False


# ------------------------------------------------------------------
# Full posting cycle (4 text + 1 image)
# ------------------------------------------------------------------

def run_posting_cycle(bot, media_folder: str = ""):
    log = bot.log
    log.info(f"[{bot.username}] Starting posting cycle (4 text + 1 image)")

    posts = _load_lines("text_posts.txt")
    selected = random.sample(posts, min(4, len(posts)))

    results = []
    for i, text in enumerate(selected, 1):
        ok = post_text(bot, text)
        results.append(ok)
        log.info(f"[{bot.username}] Text post {i}/4: {'ok' if ok else 'failed'}")
        time.sleep(random.uniform(30, 90))

    ok = post_image(bot, media_folder=media_folder)
    results.append(ok)
    log.info(f"[{bot.username}] Image post: {'ok' if ok else 'failed'}")

    return results
