"""
Telegram repost task:
  - Navigate to the mother account's Threads post (MOTHER_POST_URL)
  - Repost OR quote it onto the spam page
  - Pin that reposted/quoted post to the top of the profile

The spam page never posts a raw Telegram URL.
It amplifies the mother's post which already contains the link.
"""
import time
import random

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import MOTHER_POST_URL


def run_mother_repost(bot):
    """
    Repost or quote the mother's Threads post, then pin it.
    Returns True if the repost succeeded (pin failure is non-fatal).
    """
    log = bot.log

    if not MOTHER_POST_URL:
        log.warning(f"[{bot.username}] MOTHER_POST_URL not set in .env — skipping repost")
        return False

    log.info(f"[{bot.username}] Navigating to mother post: {MOTHER_POST_URL}")
    bot.go(MOTHER_POST_URL)
    time.sleep(2)

    # Try repost first, fall back to quote
    if _repost(bot):
        log.info(f"[{bot.username}] Reposted mother post")
    elif _quote_repost(bot):
        log.info(f"[{bot.username}] Quoted mother post")
    else:
        log.warning(f"[{bot.username}] Could not repost or quote mother post")
        return False

    time.sleep(3)

    # Pin the repost/quote (it's now the latest post on the profile)
    pinned = _pin_latest_post(bot)
    if pinned:
        log.info(f"[{bot.username}] Pinned repost to top of profile")
    else:
        log.warning(f"[{bot.username}] Pin failed — pin manually if needed")

    return True


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
