"""
main.py — entry point.

Default: launches the GUI.

CLI flags (headless mode):
  --no-ui                         run without GUI (uses accounts.txt + config.py)
  --accounts  <file>              custom accounts file
  --batch-size <n>                override batch size
  --single    <username> <id>     run a single account directly
  --status                        print account states from DB
"""
import argparse
import sys
import multiprocessing


def cmd_ui():
    from ui.app import launch
    launch()


def cmd_run(args):
    import data.db as db
    from coordinator import Coordinator
    db.init_db()
    c = Coordinator(accounts_file=args.accounts, batch_size=args.batch_size)
    c.run()


def cmd_single(args):
    import data.db as db
    from data.db import _connect
    from worker import run_account
    db.init_db()
    username, adspower_id = args.single
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO accounts (username, adspower_id) VALUES (?, ?)",
        (username, adspower_id),
    )
    conn.commit()
    conn.close()
    run_account(username, adspower_id)


def cmd_status(args):
    import data.db as db
    db.init_db()
    from data.db import _connect
    conn = _connect()
    rows = conn.execute(
        "SELECT username, adspower_id, state, setup_done, warmup_done, "
        "telegram_posted, updated_at FROM accounts ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()

    if not rows:
        print("No accounts in database yet.")
        return

    header = f"{'USERNAME':<22} {'ADSPOWER_ID':<16} {'STATE':<10} {'SETUP':<6} {'WARM':<5} {'TG':<4} UPDATED"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['username']:<22} {r['adspower_id']:<16} {r['state']:<10} "
            f"{'✓' if r['setup_done'] else '✗':<6} {'✓' if r['warmup_done'] else '✗':<5} "
            f"{'✓' if r['telegram_posted'] else '✗':<4} {r['updated_at'] or ''}"
        )


def main():
    parser = argparse.ArgumentParser(description="ThreadsBotV2")
    parser.add_argument("--no-ui",      action="store_true",  help="Run headless (no GUI)")
    parser.add_argument("--accounts",   default="accounts.txt")
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--single",     nargs=2, metavar=("USERNAME", "ADSPOWER_ID"))
    parser.add_argument("--status",     action="store_true")
    args = parser.parse_args()

    if args.status:
        cmd_status(args)
    elif args.single:
        cmd_single(args)
    elif args.no_ui:
        cmd_run(args)
    else:
        cmd_ui()  # default: launch GUI


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
