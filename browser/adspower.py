import requests
import time
import logging

from config import ADSPOWER_URL, ADSPOWER_API_KEY


class AdsPowerManager:
    def __init__(self, base_url=None, api_key=None, log=None):
        self.base_url = (base_url or ADSPOWER_URL).rstrip("/")
        self._api_key = api_key or ADSPOWER_API_KEY
        self._log = log or logging.getLogger("adspower")

    def _params(self, **kw) -> dict:
        """Merge API key into every request's query params."""
        if self._api_key:
            kw["serial_number"] = self._api_key
        return kw

    def start_browser(self, user_id):
        """Open an AdsPower profile and return (selenium_address, debug_port) or raise."""
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{self.base_url}/browser/start",
                    params=self._params(user_id=user_id, ip_tab=1, cdp_mask=1),
                    timeout=30,
                )
                data = resp.json()
                if data.get("code") == 0:
                    ws = data["data"]["ws"]
                    selenium_address = ws.get("selenium")
                    debug_port = data["data"].get("debugging_port")
                    if not selenium_address:
                        raise RuntimeError("No selenium address in AdsPower response")
                    self._log.info(f"[AdsPower] Browser started for {user_id}: {selenium_address}")
                    return selenium_address, debug_port
                else:
                    msg = data.get("msg", "unknown error")
                    self._log.warning(f"[AdsPower] Start failed (attempt {attempt+1}): {msg}")
                    time.sleep(3)
            except requests.RequestException as e:
                self._log.warning(f"[AdsPower] Request error (attempt {attempt+1}): {e}")
                time.sleep(3)
        raise RuntimeError(f"Failed to start AdsPower browser for user_id={user_id}")

    def stop_browser(self, user_id):
        try:
            resp = requests.get(
                f"{self.base_url}/browser/stop",
                params=self._params(user_id=user_id),
                timeout=15,
            )
            data = resp.json()
            if data.get("code") == 0:
                self._log.info(f"[AdsPower] Browser stopped for {user_id}")
                return True
            self._log.warning(f"[AdsPower] Stop failed: {data.get('msg')}")
        except Exception as e:
            self._log.warning(f"[AdsPower] Stop error: {e}")
        return False

    def list_profiles(self):
        try:
            resp = requests.get(
                f"{self.base_url}/user/list",
                params=self._params(),
                timeout=15,
            )
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {}).get("list", [])
        except Exception as e:
            self._log.warning(f"[AdsPower] List profiles error: {e}")
        return []

    def get_2fa_code(self, user_id):
        try:
            resp = requests.get(
                f"{self.base_url}/user/list",
                params=self._params(user_id=user_id),
                timeout=15,
            )
            data = resp.json()
            profiles = data.get("data", {}).get("list", [])
            if profiles:
                secret = profiles[0].get("fakey", "")
                if secret:
                    import pyotp
                    return pyotp.TOTP(secret).now()
        except Exception as e:
            self._log.warning(f"[AdsPower] 2FA error: {e}")
        return None
