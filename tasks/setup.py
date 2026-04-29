"""
Account setup: set profile picture and bio.
Runs once per account before warmup.
"""
import os
import random
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "..", "content")


def _pick_random_pfp():
    pfp_dir = os.path.join(CONTENT_DIR, "pfps")
    files = [
        os.path.join(pfp_dir, f)
        for f in os.listdir(pfp_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]
    if not files:
        raise FileNotFoundError("No pfp images found in content/pfps/")
    return random.choice(files)


def _pick_random_bio():
    bio_file = os.path.join(CONTENT_DIR, "bios.txt")
    with open(bio_file, "r", encoding="utf-8") as f:
        bios = [line.strip() for line in f if line.strip()]
    if not bios:
        raise ValueError("bios.txt is empty")
    return random.choice(bios)


def run_setup(bot):
    """Set pfp and bio. Returns True on success."""
    log = bot.log
    log.info(f"[{bot.username}] Running account setup...")

    bio = _pick_random_bio()
    pfp_path = _pick_random_pfp()

    success = _set_profile(bot, pfp_path, bio)
    if success:
        log.info(f"[{bot.username}] Setup complete — bio set, pfp uploaded")
    else:
        log.warning(f"[{bot.username}] Setup partially failed")
    return success


def _set_profile(bot, pfp_path, bio):
    try:
        bot.go_to_edit_profile()
        time.sleep(2)

        # --- Upload pfp ---
        try:
            file_input = WebDriverWait(bot.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
            )
            file_input.send_keys(os.path.abspath(pfp_path))
            bot.log.info(f"[{bot.username}] Uploaded pfp: {os.path.basename(pfp_path)}")
            time.sleep(3)

            # Confirm crop/save dialog if it appears
            for confirm_xpath in [
                '//button[contains(text(),"Apply")]',
                '//button[contains(text(),"Save")]',
                '//div[contains(text(),"Apply")]',
            ]:
                try:
                    el = WebDriverWait(bot.driver, 4).until(
                        EC.element_to_be_clickable((By.XPATH, confirm_xpath))
                    )
                    el.click()
                    time.sleep(2)
                    break
                except Exception:
                    pass
        except Exception as e:
            bot.log.warning(f"[{bot.username}] pfp upload failed: {e}")

        # --- Set bio ---
        try:
            bio_field = WebDriverWait(bot.driver, 10).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//textarea[@placeholder="Bio"] | //input[@name="biography"] | //textarea[@name="biography"]')
                )
            )
            bio_field.click()
            time.sleep(0.3)
            bot.driver.execute_script("arguments[0].value = '';", bio_field)
            bot.paste_text(bio_field, bio)
            bot.log.info(f"[{bot.username}] Bio set: {bio[:40]}...")
            time.sleep(1)
        except Exception as e:
            bot.log.warning(f"[{bot.username}] Bio field not found: {e}")

        # --- Save profile ---
        for save_xpath in [
            '//button[contains(text(),"Save")]',
            '//div[@role="button" and contains(text(),"Save")]',
            '//button[@type="submit"]',
        ]:
            try:
                el = WebDriverWait(bot.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, save_xpath))
                )
                el.click()
                bot.log.info(f"[{bot.username}] Saved profile")
                time.sleep(3)
                return True
            except Exception:
                continue

        bot.log.warning(f"[{bot.username}] Could not find Save button")
        return False

    except Exception as e:
        bot.log.error(f"[{bot.username}] _set_profile error: {e}")
        return False
