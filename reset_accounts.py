"""Quick utility to reset all accounts back to pending state."""
import data.db as db

db.init_db()
import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), "data", "bot.db")
conn = sqlite3.connect(db_path)
rows = conn.execute("SELECT username, state FROM accounts").fetchall()
print("Before:")
for r in rows:
    print(f"  {r[0]}: {r[1]}")

conn.execute("UPDATE accounts SET state='pending', retry_count=0, notes=''")
conn.commit()

rows = conn.execute("SELECT username, state FROM accounts").fetchall()
print("\nAfter:")
for r in rows:
    print(f"  {r[0]}: {r[1]}")
conn.close()
