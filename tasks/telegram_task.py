"""
Mother account repost task (SOP flow):
  - Navigate to MOTHER_POST_URL
  - Click Repost icon -> click "Quote"
  - Type CTA into the first thread item
  - Click "Add to thread"
  - Upload an image into the second thread item
  - Type the same CTA on the second item
  - Submit the thread
  - Pin to profile (best effort)

If CTA file or images folder are not configured, falls back to the simple
Repost-or-Quote flow (preserves prior behavior).
"""
import os
import time
import random

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import (
    MOTHER_POST_URL,
    MOTHER_REPOST_ENABLED,
    MOTHER_REPOST_CTA_FILE,
    MOTHER_REPOST_IMAGES_FOLDER,
)


def _pick_random_line(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        return random.choice(lines) if lines else None
    except Exception:
        return None


def _pick_random_image(folder):
    if not folder or not os.path.isdir(folder):
        return None
    try:
        imgs = [
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"))
        ]
        return random.choice(imgs) if imgs else None
    except Exception:
        return None


def run_mother_repost(bot):
    """Run the mother repost flow once per account.
    Returns True on success (pin failure is non-fatal)."""
    log = bot.log

    if not MOTHER_REPOST_ENABLED:
        log.info(f"[{bot.username}] Mother repost disabled in settings — skipping")
        return False
    if not MOTHER_POST_URL:
        log.warning(f"[{bot.username}] MOTHER_POST_URL not set — skipping repost")
        return False

    cta = _pick_random_line(MOTHER_REPOST_CTA_FILE)
    image = _pick_random_image(MOTHER_REPOST_IMAGES_FOLDER)

    log.info(f"[{bot.username}] Navigating to mother post: {MOTHER_POST_URL}")
    bot.go(MOTHER_POST_URL)
    time.sleep(2.5)

    # Full SOP flow: Quote + CTA + Add to thread + image + CTA + Pin
    if cta and image:
        if _quote_thread_with_image(bot, cta, image):
            log.info(f"[{bot.username}] Mother repost thread published")
            time.sleep(3)
            pinned = _pin_latest_post(bot)
            log.info(f"[{bot.username}] Pin {'ok' if pinned else 'skipped/failed'}")
            return True
        log.warning(f"[{bot.username}] Full SOP flow failed — falling back to simple repost")

    # Fallback: legacy simple repost or quote (no thread, no image)
    if _repost(bot):
        log.info(f"[{bot.username}] Reposted mother post (fallback)")
    elif _quote_repost(bot):
        log.info(f"[{bot.username}] Quoted mother post (fallback)")
    else:
        log.warning(f"[{bot.username}] Could not repost or quote mother post")
        return False

    time.sleep(3)
    pinned = _pin_latest_post(bot)
    log.info(f"[{bot.username}] Pin {'ok' if pinned else 'skipped/failed'}")
    return True


# ------------------------------------------------------------------
# Full SOP flow: Quote -> CTA -> Add to thread -> image -> CTA -> Post
# ------------------------------------------------------------------

def _quote_thread_with_image(bot, cta_text, image_path):
    """Build a 2-item thread: quote-repost with CTA + new media item with same CTA."""
    log = bot.log
    try:
        # 1. Open the Repost menu
        repost_btn = _find_repost_button(bot)
        if not repost_btn:
            log.warning(f"[{bot.username}] Repost button not found")
            return False
        bot.smart_click(repost_btn)
        time.sleep(1.5)

        # 2. Click "Quote"
        quote_clicked = False
        for xpath in [
            '//span[text()="Quote"]',
            '//div[text()="Quote"]',
            '//div[@role="button" and contains(text(),"Quote")]',
        ]:
            try:
                el = WebDriverWait(bot.driver, 4).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                el.click()
                quote_clicked = True
                break
            except Exception:
                continue
        if not quote_clicked:
            log.warning(f"[{bot.username}] Quote option not found")
            return False
        time.sleep(2.5)

        # 3. Type CTA in the first textbox
        try:
            first_textbox = WebDriverWait(bot.driver, 8).until(
                EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
            )
        except Exception as e:
            log.warning(f"[{bot.username}] First textbox not found: {e}")
            return False
        if not bot.paste_text(first_textbox, cta_text):
            log.warning(f"[{bot.username}] Failed to paste first CTA")
            return False
        try:
            bot.driver.execute_script(
                """arguments[0].dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
                   arguments[0].dispatchEvent(new Event('change',{bubbles:true}));""",
                first_textbox,
            )
        except Exception:
            pass
        time.sleep(1.2)

        # 4. Click "Add to thread"
        add_clicked = False
        for xpath in [
            "//div[@role='button']//*[text()='Add to thread']",
            "//*[text()='Add to thread']",
            "//div[contains(text(),'Add to thread')]",
            "//span[contains(text(),'Add to thread')]",
        ]:
            try:
                btn = WebDriverWait(bot.driver, 4).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                bot.smart_click(btn)
                add_clicked = True
                break
            except Exception:
                continue
        if not add_clicked:
            log.warning(f"[{bot.username}] 'Add to thread' button not found")
            return False
        time.sleep(2.5)

        # 5. Upload image to the newest file input
        try:
            file_inputs = WebDriverWait(bot.driver, 8).until(
                EC.presence_of_all_elements_located((By.XPATH, "//input[@type='file']"))
            )
            file_inputs[-1].send_keys(os.path.abspath(image_path))
            log.info(f"[{bot.username}] Uploaded image: {os.path.basename(image_path)}")
            time.sleep(4)
        except Exception as e:
            log.warning(f"[{bot.username}] Failed to upload image: {e}")
            return False

        # 6. Paste CTA into the second textbox
        try:
            textboxes = bot.driver.find_elements(By.XPATH, '//div[@role="textbox"]')
            if not textboxes:
                log.warning(f"[{bot.username}] Second textbox missing")
                return False
            second_textbox = textboxes[-1]
            if not bot.paste_text(second_textbox, cta_text):
                log.warning(f"[{bot.username}] Failed to paste second CTA")
                return False
            try:
                bot.driver.execute_script(
                    """arguments[0].dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
                       arguments[0].dispatchEvent(new Event('change',{bubbles:true}));""",
                    second_textbox,
                )
            except Exception:
                pass
            time.sleep(1.2)
        except Exception as e:
            log.warning(f"[{bot.username}] Second item caption error: {e}")
            return False

        # 7. Submit
        return bot.click_post_button()

    except Exception as e:
        log.warning(f"[{bot.username}] _quote_thread_with_image error: {e}")
        return False


# ------------------------------------------------------------------
# Repost (native repost — no added text)
# ------------------------------------------------------------------

def _repost(bot):
    """Click the Repost button on the current post page."""
    try:
        # Find the repost icon (two arrows / repost SVG)
        repost_btn = _find_repost_button(bot)
        if not repost_btn:
            return False

        bot.smart_click(repost_btn)
        time.sleep(1.5)

        # A dropdown appears with "Repost" and "Quote" options — click Repost
        for xpath in [
            '//span[text()="Repost"]',
            '//div[text()="Repost"]',
            '//div[@role="button" and contains(text(),"Repost")]',
        ]:
            try:
                el = WebDriverWait(bot.driver, 4).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                el.click()
                time.sleep(2)
                return True
            except Exception:
                continue

    except Exception as e:
        bot.log.debug(f"[{bot.username}] _repost error: {e}")
    return False


# ------------------------------------------------------------------
# Quote repost (adds a comment on top of the repost)
# ------------------------------------------------------------------

def _quote_repost(bot):
    """Click the Repost button then choose Quote, leave text empty or minimal."""
    try:
        repost_btn = _find_repost_button(bot)
        if not repost_btn:
            return False

        bot.smart_click(repost_btn)
        time.sleep(1.5)

        # Click "Quote" in the dropdown
        for xpath in [
            '//span[text()="Quote"]',
            '//div[text()="Quote"]',
            '//div[@role="button" and contains(text(),"Quote")]',
        ]:
            try:
                el = WebDriverWait(bot.driver, 4).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                el.click()
                time.sleep(2)
                break
            except Exception:
                continue

        # The compose dialog opens with the quoted post embedded.
        # Optionally add a short line of text — keep it light so it looks organic.
        filler_texts = ["🔥", "👀", "✨", "💯", "check this"]
        try:
            textbox = WebDriverWait(bot.driver, 6).until(
                EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
            )
            bot.paste_text(textbox, random.choice(filler_texts))
            time.sleep(1)
        except Exception:
            pass  # posting without text is fine too

        return bot.click_post_button()

    except Exception as e:
        bot.log.debug(f"[{bot.username}] _quote_repost error: {e}")
    return False


# ------------------------------------------------------------------
# Shared helper: find the repost/share button on a post page
# ------------------------------------------------------------------

def _find_repost_button(bot):
    selectors = [
        (By.XPATH, '//svg[@aria-label="Repost"]'),
        (By.XPATH, '//div[@aria-label="Repost"]'),
        (By.XPATH, '//svg[@aria-label="Share"]'),
        (By.CSS_SELECTOR, 'svg[aria-label="Repost"]'),
    ]
    for by, sel in selectors:
        try:
            el = WebDriverWait(bot.driver, 5).until(
                EC.presence_of_element_located((by, sel))
            )
            if el.is_displayed():
                # Walk up to the clickable button if we hit the SVG
                parent = el
                for _ in range(4):
                    role = parent.get_attribute("role")
                    if role in ("button", "link"):
                        return parent
                    parent = bot.driver.execute_script(
                        "return arguments[0].parentElement;", parent
                    )
                return el  # fallback: return the SVG itself
        except Exception:
            continue
    return None


# ------------------------------------------------------------------
# Pin the most recent post on the profile
# ------------------------------------------------------------------

def _pin_latest_post(bot):
    try:
        bot.go_to_profile()
        time.sleep(2.5)

        # The three-dots / more options button on the first post
        options_btns = bot.driver.find_elements(
            By.XPATH,
            '//*[@aria-label="More" or @aria-label="Options" or @aria-label="More options"]',
        )
        if not options_btns:
            bot.log.warning(f"[{bot.username}] No options button found on profile")
            return False

        bot.smart_click(options_btns[0])
        time.sleep(1.5)

        for xpath in [
            '//span[text()="Pin to profile"]',
            '//div[text()="Pin to profile"]',
            '//span[contains(text(),"Pin")]',
            '//div[contains(text(),"Pin")]',
        ]:
            try:
                pin_btn = WebDriverWait(bot.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, xpath))
                )
                pin_btn.click()
                time.sleep(2)

                # Confirm dialog if it pops up
                for confirm in [
                    '//div[@role="button" and text()="Pin"]',
                    '//button[normalize-space()="Pin"]',
                ]:
                    try:
                        el = WebDriverWait(bot.driver, 3).until(
                            EC.element_to_be_clickable((By.XPATH, confirm))
                        )
                        el.click()
                        time.sleep(1.5)
                        break
                    except Exception:
                        pass

                return True
            except Exception:
                continue

        bot.log.warning(f"[{bot.username}] 'Pin to profile' not found in menu")
        return False

    except Exception as e:
        bot.log.warning(f"[{bot.username}] _pin_latest_post error: {e}")
        return False
