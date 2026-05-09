import time
import random
import os
import pyperclip

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchWindowException, WebDriverException

from browser.adspower import AdsPowerManager


class ThreadsBot:
    BASE_URL = "https://www.threads.com"

    def __init__(self, username, adspower_id, logger):
        self.username = username
        self.adspower_id = adspower_id
        self.log = logger
        self.driver = None
        self.adspower = AdsPowerManager(log=logger)

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    def open_browser(self):
        selenium_address, _, webdriver_path = self.adspower.start_browser(self.adspower_id)
        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", selenium_address)

        service = None
        if webdriver_path and os.path.exists(webdriver_path):
            service = Service(executable_path=webdriver_path)

        # Chrome needs a few seconds to fully start and open its debug port.
        # Retry the connection up to 10 times before giving up.
        last_err = None
        for attempt in range(10):
            try:
                self.driver = (
                    webdriver.Chrome(service=service, options=options)
                    if service else
                    webdriver.Chrome(options=options)
                )
                break
            except Exception as e:
                last_err = e
                wait = 3 + attempt * 2  # 3, 5, 7, … seconds
                self.log.info(f"Chrome not ready yet (attempt {attempt + 1}/10) — retrying in {wait}s")
                time.sleep(wait)
        else:
            raise RuntimeError(f"Could not connect to Chrome after 10 attempts: {last_err}")

        self.log.info(f"Browser opened for {self.username}")
        # Give AdsPower time to handle proxy auth before we try to navigate
        time.sleep(8)
        self._navigate_to_threads()
        return self.driver

    def _navigate_to_threads(self):
        """Navigate to Threads home and wait for the page to be interactive.
        Handles proxy auth dialogs by dismissing them and waiting for the
        proxy session to establish before retrying.
        """
        for attempt in range(6):
            try:
                # Dismiss any proxy auth / native dialog that may be blocking
                try:
                    alert = self.driver.switch_to.alert
                    alert.dismiss()
                    self.log.info("Dismissed browser dialog before navigation")
                    time.sleep(2)
                except Exception:
                    pass

                self.driver.get(self.BASE_URL)

                # Wait for body — if proxy auth is pending this will time out
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                time.sleep(random.uniform(2, 3))
                self.log.info(f"Navigated to Threads ({self.driver.current_url})")
                return
            except Exception as e:
                err = str(e)
                if "ERR_PROXY_AUTH_REQUESTED" in err or "PROXY_AUTH" in err:
                    wait = 10 + attempt * 5  # 10, 15, 20, 25, 30, 35s
                    self.log.warning(
                        f"Proxy auth pending (attempt {attempt + 1}/6) — waiting {wait}s for proxy to authenticate"
                    )
                    time.sleep(wait)
                elif "invalid session id" in err or "disconnected" in err:
                    self.log.error("Browser session lost during navigation")
                    raise
                else:
                    self.log.warning(f"Navigation attempt {attempt + 1} failed: {e}")
                    time.sleep(5)
        self.log.warning("Could not navigate to Threads after 6 attempts — continuing anyway")

    def close_browser(self):
        if self.driver:
            try:
                # Do NOT call driver.quit() — it sends a shutdown command that kills
                # the AdsPower browser window. Just drop the Selenium reference and
                # let AdsPower's stop_browser API handle the actual shutdown.
                self.driver = None
            except Exception:
                pass
        self.adspower.stop_browser(self.adspower_id)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def _ensure_window(self):
        """Switch to an open window if the current one was closed."""
        try:
            _ = self.driver.current_url
        except NoSuchWindowException:
            handles = self.driver.window_handles
            if handles:
                self.driver.switch_to.window(handles[-1])
                self.log.info("Switched to available window after previous one closed")
            else:
                raise NoSuchWindowException("All browser windows are closed")

    def go(self, url):
        self._ensure_window()
        for attempt in range(3):
            try:
                # Dismiss any pending proxy auth dialog
                try:
                    self.driver.switch_to.alert.dismiss()
                    time.sleep(2)
                except Exception:
                    pass
                self.driver.get(url)
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                time.sleep(random.uniform(2, 3.5))
                return
            except Exception as e:
                if "ERR_PROXY_AUTH_REQUESTED" in str(e) or "PROXY_AUTH" in str(e):
                    self.log.warning(f"Proxy auth on go({url}) — waiting 15s")
                    time.sleep(15)
                elif "invalid session id" in str(e) or "disconnected" in str(e):
                    raise
                elif attempt < 2:
                    time.sleep(3)
                else:
                    raise

    def go_home(self):
        self.go(self.BASE_URL)

    def dismiss_overlays(self):
        """Close any open hover/preview cards or modals.

        Threads shows a small floating card when the cursor hovers over a
        username. If the bot's previous click landed near such a username
        (e.g. while liking a post), the card may stay open and intercept the
        next interaction — including matching a stray 'Follow' button inside
        the card. Pressing Escape and clicking on a neutral spot dismisses it.
        """
        try:
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.2)
        except Exception:
            pass
        try:
            self.driver.execute_script("""
                // Click on body at a known-empty spot near the top to drop
                // focus from any popup, without triggering link navigation.
                const evt = new MouseEvent('click', {
                    bubbles: true, cancelable: true, view: window,
                    clientX: 5, clientY: 5
                });
                document.body.dispatchEvent(evt);
            """)
        except Exception:
            pass

    def is_logged_in(self):
        try:
            url = self.driver.current_url.lower()

            # Navigate to Threads if not already there
            if "threads.com" not in url and "threads.net" not in url:
                self._navigate_to_threads()
                time.sleep(2)
                url = self.driver.current_url.lower()

            # Explicit logged-out indicators in URL
            if any(k in url for k in ["login", "accounts/login", "/logout"]):
                return False

            # Check for visible login form or password field
            try:
                login_form = self.driver.find_elements(
                    By.XPATH,
                    '//input[@type="password"] | //button[contains(text(),"Log in")]'
                )
                if login_form:
                    return False
            except Exception:
                pass

            # Positive check: logged-in UI element present
            try:
                WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//div[@role="textbox"] | '
                        '//*[@aria-label="New post"] | '
                        '//*[contains(@aria-label,"compose")] | '
                        '//span[contains(text(),"What\'s new?")] | '
                        '//div[contains(text(),"What\'s new?")]'
                    ))
                )
                return True
            except Exception:
                pass

            return "threads.com" in url or "threads.net" in url
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def wait(self, by, selector, timeout=15):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )

    def wait_click(self, by, selector, timeout=15):
        el = WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, selector))
        )
        el.click()
        return el

    def smart_click(self, element):
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", element
            )
            time.sleep(0.4)
            try:
                element.click()
                return True
            except Exception:
                try:
                    self.driver.execute_script("arguments[0].click();", element)
                    return True
                except Exception:
                    ActionChains(self.driver).move_to_element(element).click().perform()
                    return True
        except Exception as e:
            self.log.warning(f"smart_click failed: {e}")
            return False

    def paste_text(self, element, text):
        """Paste text into a focused element. Uses clipboard + ActionChains to handle emojis."""
        import platform
        try:
            original = pyperclip.paste()
            pyperclip.copy(text)
            time.sleep(0.5)

            element.click()
            time.sleep(0.5)

            if platform.system() == "Darwin":
                ActionChains(self.driver).key_down(Keys.COMMAND).send_keys("v").key_up(Keys.COMMAND).perform()
            else:
                ActionChains(self.driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()

            time.sleep(1)
            pyperclip.copy(original)
            return True
        except Exception as e:
            self.log.warning(f"paste_text clipboard failed: {e} — trying send_keys fallback")
            try:
                element.clear()
                for char in text:
                    element.send_keys(char)
                    time.sleep(0.03)
                return True
            except Exception:
                return False

    def scroll_down(self, amount=None):
        px = amount or random.randint(400, 900)
        self.driver.execute_script(f"window.scrollBy(0, {px});")
        time.sleep(random.uniform(0.3, 1.2))

    def scroll_to_top(self):
        self.driver.execute_script("window.scrollTo(0, 0);")

    # ------------------------------------------------------------------
    # Post creation entry point (opens the compose dialog)
    # ------------------------------------------------------------------

    def open_compose(self):
        """Open the post compose dialog. Returns the textbox element or None."""
        self.go_home()
        self.scroll_to_top()
        time.sleep(1)

        # Verify Threads actually loaded — if proxy is still authenticating the page
        # will be blank and no compose button will exist.  Give it up to 30s extra.
        threads_loaded = False
        for _wait_attempt in range(3):
            try:
                cur = (self.driver.current_url or "").lower()
                if "threads.com" not in cur and "threads.net" not in cur:
                    self.log.warning(f"[{self.username}] Not on Threads ({cur[:60]}) — re-navigating")
                    self._navigate_to_threads()
                    time.sleep(3)
                    continue
                # Check for any known Threads UI element
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//div[@role="textbox"] | '
                        '//span[contains(text(),"What\'s new?")] | '
                        '//div[contains(text(),"What\'s new?")] | '
                        '//*[@aria-label="New post"] | '
                        '//div[@aria-label="Create"]'
                    ))
                )
                threads_loaded = True
                break
            except Exception:
                self.log.warning(
                    f"[{self.username}] Threads UI not detected on home (attempt {_wait_attempt+1}/3) — waiting"
                )
                time.sleep(10)

        if not threads_loaded:
            self.log.warning(f"[{self.username}] Threads home did not load — skipping compose")
            return None

        # Ordered by likelihood — aria-label is most stable
        selectors = [
            (By.XPATH, '//div[@aria-label="Empty text field. Type to compose a new post."]'),
            (By.XPATH, '//span[contains(text(),"What\'s new?")]'),
            (By.XPATH, '//div[contains(text(),"What\'s new?")]'),
            (By.XPATH, '//div[@aria-label="Create"]'),
            (By.XPATH, '//a[@aria-label="Create"]'),
            (By.XPATH, '//button[@aria-label="New post"]'),
            (By.XPATH, '//*[@aria-label="New post"]'),
            (By.CSS_SELECTOR, 'div[aria-label="Create"]'),
        ]

        def _js_grab_compose_textbox(self):
            """JS-based textbox finder that avoids feed/nav containers."""
            return self.driver.execute_script("""
                // 1. activeElement — set by Threads when the compose field is focused
                const ae = document.activeElement;
                if (ae && ae !== document.body && ae !== document.documentElement) {
                    const role = ae.getAttribute('role');
                    const ce   = ae.getAttribute('contenteditable');
                    if (role === 'textbox' || ce === 'true') return ae;
                }

                // 2. Walk every contenteditable / textbox div, skipping:
                //    - nav bars
                //    - feed post containers (data-pressable-container)
                //    - things inside a post article (comment reply boxes)
                const candidates = document.querySelectorAll(
                    '[role="textbox"], [contenteditable="true"]'
                );
                for (const el of candidates) {
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    // Skip nav / sidebar items
                    if (el.closest('nav') || el.closest('header')) continue;
                    // Skip feed post cards (the "reply" inline boxes)
                    if (el.closest('[data-pressable-container]')) continue;
                    // Skip comment reply dialogs that are already nested in a post
                    if (el.closest('article')) continue;
                    // Must be editable
                    if (el.getAttribute('contenteditable') === 'false') continue;
                    return el;
                }
                return null;
            """)

        clicked = False
        for by, sel in selectors:
            try:
                el = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((by, sel)))
                self.smart_click(el)
                self.log.info(f"Compose opened via: {sel[:60]}")
                clicked = True

                # Give the compose overlay time to animate in, then try JS grab.
                # Use a short poll loop (500ms × 8 = 4s max) so we don't wait
                # blindly when the textbox is already ready.
                for _poll in range(8):
                    time.sleep(0.5)
                    try:
                        tb = _js_grab_compose_textbox(self)
                        if tb:
                            self.log.info(f"[{self.username}] Compose textbox found (poll {_poll+1})")
                            return tb
                    except Exception:
                        pass

                # Still not found — wait an extra 3s then retry once more
                time.sleep(3)
                try:
                    tb = _js_grab_compose_textbox(self)
                    if tb:
                        self.log.info(f"[{self.username}] Compose textbox found after extended wait")
                        return tb
                except Exception:
                    pass

                break
            except Exception:
                continue

        if not clicked:
            # Log what aria-labels ARE present to help diagnose selector failures
            try:
                labels = self.driver.execute_script("""
                    return Array.from(document.querySelectorAll('[aria-label]'))
                        .map(e => e.getAttribute('aria-label'))
                        .filter(l => l && l.length < 80);
                """)
                self.log.info(f"aria-labels on page: {labels[:20]}")
            except Exception:
                pass

            # JS fallback: find any button/div that suggests "new post" or "create"
            self.log.warning("Primary selectors failed — trying JS compose fallback")
            try:
                result = self.driver.execute_script("""
                    const hints = ['new post', "what's new", 'create', 'compose'];
                    const els = document.querySelectorAll('[aria-label],[placeholder]');
                    for (const el of els) {
                        const label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').toLowerCase();
                        if (hints.some(h => label.includes(h))) {
                            el.click();
                            return el.getAttribute('aria-label') || el.getAttribute('placeholder');
                        }
                    }
                    return null;
                """)
                if result:
                    self.log.info(f"Compose opened via JS fallback: {result}")
                    time.sleep(3)
                    clicked = True
                    try:
                        tb = _js_grab_compose_textbox(self)
                        if tb:
                            return tb
                    except Exception:
                        pass
            except Exception as e:
                self.log.warning(f"JS compose fallback failed: {e}")

        if not clicked:
            self.log.warning(f"[{self.username}] Could not open compose dialog")
            return None

        # Last resort: refresh home and try the whole flow once more
        self.log.warning(f"[{self.username}] Compose textbox not found — refreshing home and retrying")
        try:
            self.driver.refresh()
            time.sleep(4)
            self.scroll_to_top()
            time.sleep(1)
            for by, sel in selectors[:3]:  # only try the most reliable selectors
                try:
                    el = WebDriverWait(self.driver, 8).until(EC.element_to_be_clickable((by, sel)))
                    self.smart_click(el)
                    time.sleep(3)
                    tb = _js_grab_compose_textbox(self)
                    if tb:
                        self.log.info(f"[{self.username}] Compose textbox found after refresh")
                        return tb
                    break
                except Exception:
                    continue
        except Exception as e:
            self.log.warning(f"[{self.username}] Refresh retry failed: {e}")

        self.log.warning(f"[{self.username}] Could not open compose dialog")
        return None

    def click_post_button(self):
        """Click the Post submit button in the compose dialog.

        Threads renders the submit as a plain <div> with text 'Post' — no role=button,
        no <button> tag. Multiple 'Post' elements may be on the page (e.g. sidebar nav).
        Strategy: find all visible leaf elements whose text is exactly 'Post', then pick
        the one closest to the active textbox in the DOM tree.
        """
        try:
            result = self.driver.execute_script("""
                const textbox = document.querySelector('[role="textbox"]');
                if (!textbox) return 'no-textbox';

                // Collect all visible, non-disabled leaf elements with text "Post"
                const candidates = [];
                for (const el of document.querySelectorAll('*')) {
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    const txt = (el.innerText || '').trim();
                    if (txt !== 'Post') continue;
                    const disabled = el.getAttribute('aria-disabled') === 'true'
                                  || el.getAttribute('disabled') != null;
                    if (disabled) continue;
                    // Skip containers — we want the innermost element with text "Post"
                    const childHasPost = Array.from(el.children).some(
                        c => (c.innerText || '').trim() === 'Post'
                    );
                    if (childHasPost) continue;
                    candidates.push(el);
                }
                if (candidates.length === 0) return 'not-found';

                // Pick the candidate that shares the MOST ancestors with the textbox
                // (i.e. closest in DOM — fewest hops to a common ancestor)
                let bestBtn = null, bestDist = Infinity;
                for (const btn of candidates) {
                    let el = btn, dist = 0;
                    while (el && !el.contains(textbox)) {
                        el = el.parentElement;
                        dist++;
                    }
                    if (dist < bestDist) { bestDist = dist; bestBtn = btn; }
                }

                if (bestBtn) { bestBtn.click(); return 'clicked:' + bestDist; }
                return 'not-found';
            """)
            if result and result.startswith('clicked'):
                self.log.info(f"[{self.username}] Post button clicked (DOM dist={result.split(':')[1]})")
                # Verify the compose dialog actually closed
                try:
                    WebDriverWait(self.driver, 8).until_not(
                        EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
                    )
                    time.sleep(2)
                    return True
                except Exception:
                    self.log.warning(f"[{self.username}] Clicked Post but dialog still open — retrying")
                    return False
            self.log.warning(f"[{self.username}] click_post_button: {result}")
        except Exception as e:
            self.log.warning(f"[{self.username}] click_post_button error: {e}")
        return False

    # ------------------------------------------------------------------
    # Profile page navigation
    # ------------------------------------------------------------------

    def go_to_profile(self, username=None):
        target = username or self.username
        self.go(f"{self.BASE_URL}/@{target}")

    def go_to_settings(self):
        self.go(f"{self.BASE_URL}/settings")

    def go_to_edit_profile(self):
        self.go(f"{self.BASE_URL}/settings/profile/edit")
