"""
coordinator.py — manages batches of 15 accounts.

Flow:
  1. Load all accounts from accounts.txt into the DB
  2. Pull BATCH_SIZE pending/error accounts
  3. Spawn one Process per account
  4. When a process finishes, pull the next pending account and spawn a replacement
  5. Repeat until all accounts are done
"""
import multiprocessing
import time
import logging
import sys

import data.db as db
from config import BATCH_SIZE
from worker import run_account

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [COORDINATOR] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("coordinator")


class Coordinator:
    def __init__(self, accounts_file="accounts.txt", batch_size=BATCH_SIZE, stop_event=None):
        self.accounts_file = accounts_file
        self.batch_size = batch_size
        self.active: dict[str, multiprocessing.Process] = {}
        # stop_event is a threading.Event; set it to signal a graceful shutdown
        self._stop = stop_event

    def run(self):
        db.init_db()
        db.load_accounts_from_file(self.accounts_file)
        db.reset_running_accounts()  # recover from previous crash

        log.info(f"Accounts loaded. Batch size: {self.batch_size}")

        self._fill_slots()

        while self.active:
            if self._stop and self._stop.is_set():
                log.info("Stop requested — terminating active workers")
                self._terminate_all()
                break
            self._reap_finished()
            self._fill_slots()
            self._print_status()
            time.sleep(10)

        log.info("All accounts processed.")

    def _terminate_all(self):
        for username, p in self.active.items():
            if p.is_alive():
                p.terminate()
                log.info(f"Terminated worker @{username}")
            db.update_account(username, state="pending")
        self.active.clear()

    def _fill_slots(self):
        needed = self.batch_size - len(self.active)
        if needed <= 0:
            return

        pending = db.get_pending_accounts(needed)
        for account in pending:
            username = account["username"]
            adspower_id = account["adspower_id"]
            original_state = account["state"]

            if username in self.active:
                continue

            log.info(f"Starting worker for @{username}")
            p = multiprocessing.Process(
                target=run_account,
                args=(username, adspower_id, original_state),
                name=f"worker-{username}",
                daemon=True,
            )
            p.start()
            self.active[username] = p
            db.update_account(username, state="running")
            # Stagger starts, but honor a stop request mid-spawn so Stop in
            # the GUI doesn't have to wait N×2s for the whole batch to launch.
            if self._stop is not None:
                if self._stop.wait(2):
                    return
            else:
                time.sleep(2)

    MAX_RETRIES = 5

    def _reap_finished(self):
        finished = [u for u, p in self.active.items() if not p.is_alive()]
        for username in finished:
            p = self.active.pop(username)
            exit_code = p.exitcode
            log.info(f"Worker @{username} finished (exit code {exit_code})")
            account = db.get_account(username)
            current_state = account["state"] if account else "unknown"
            # Never overwrite terminal states the worker set intentionally
            if current_state == "invalid":
                pass
            elif current_state == "running":
                note = f"process exited with code {exit_code}" if exit_code != 0 else "worker exited without updating state"
                retry = (account.get("retry_count") or 0) + 1
                if retry >= self.MAX_RETRIES:
                    db.update_account(username, state="invalid", retry_count=retry,
                                      notes=f"gave up after {retry} retries — last: {note}")
                    log.error(f"@{username} marked invalid after {retry} failed attempts")
                else:
                    db.update_account(username, state="error", retry_count=retry, notes=note)
                    log.warning(f"@{username} errored (attempt {retry}/{self.MAX_RETRIES})")
            elif exit_code != 0:
                retry = (account.get("retry_count") or 0) + 1
                note = f"process exited with code {exit_code}"
                if retry >= self.MAX_RETRIES:
                    db.update_account(username, state="invalid", retry_count=retry,
                                      notes=f"gave up after {retry} retries — last: {note}")
                    log.error(f"@{username} marked invalid after {retry} failed attempts")
                else:
                    db.update_account(username, state="error", retry_count=retry, notes=note)
                    log.warning(f"@{username} errored (attempt {retry}/{self.MAX_RETRIES})")
            else:
                # Successful exit — reset retry counter
                db.update_account(username, retry_count=0)

    def _print_status(self):
        pending_count = len(db.get_pending_accounts(9999))
        active_names = list(self.active.keys())
        log.info(
            f"Active: {len(self.active)} {active_names} | "
            f"Pending: {pending_count}"
        )
