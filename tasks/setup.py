"""
Account setup: set bio only. Profile pictures are handled manually via AdsPower.
"""
import os
import random
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

CONTENT_DIR = os.path.join(os.path.dirname(__file__), "..", "content")


def _pick_random_bio():
    bio_file = os.path.join(CONTENT_DIR, "bios.txt")
    with open(bio_file, "r", encoding="utf-8") as f:
        bios = [line.strip() for line in f if line.strip()]
    if not bios:
        raise ValueError("bios.txt is empty")
    return random.choice(bios)


def run_setup(bot):
    """Account setup — bio and pfp are handled manually for now."""
    bot.log.info(f"[{bot.username}] Setup skipped (manual setup)")
    return True

    # --- Bio setup (disabled) ---
    # bio = _pick_random_bio()
    # try:
    #     bot.go_to_edit_profile()
    #     time.sleep(2)
    #     bio_field = WebDriverWait(bot.driver, 10).until(
    #         EC.presence_of_element_located(
    #             (By.XPATH, '//textarea[@placeholder="Bio"] | //input[@name="biography"] | //textarea[@name="biography"]')
    #         )
    #     )
    #     bio_field.click()
    #     time.sleep(0.3)
    #     bot.driver.execute_script("arguments[0].value = '';", bio_field)
    #     bot.paste_text(bio_field, bio)
    #     bot.log.info(f"[{bot.username}] Bio set: {bio[:40]}...")
    #     time.sleep(1)
    #     for save_xpath in [
    #         '//button[contains(text(),"Save")]',
    #         '//div[@role="button" and contains(text(),"Save")]',
    #         '//button[@type="submit"]',
    #     ]:
    #         try:
    #             el = WebDriverWait(bot.driver, 5).until(EC.element_to_be_clickable((By.XPATH, save_xpath)))
    #             el.click()
    #             bot.log.info(f"[{bot.username}] Setup complete — bio saved")
    #             time.sleep(3)
    #             return True
    #         except Exception:
    #             continue
    #     bot.log.warning(f"[{bot.username}] Could not find Save button")
    #     return False
    # except Exception as e:
    #     bot.log.error(f"[{bot.username}] Setup error: {e}")
    #     return False
