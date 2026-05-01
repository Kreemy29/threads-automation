# ThreadsBotV2

Multi-account Threads automation bot. Runs up to 15 concurrent accounts through a full lifecycle: warmup → active posting → outreach engagement, all controlled from a dark-mode GUI.

---

## Features

- **Multi-account** — runs N accounts in parallel via Python multiprocessing
- **AdsPower integration** — each account uses its own anti-detect browser profile
- **Human-like behavior** — mood-based scrolling (casual / engaged / fast / distracted) between every action
- **AI-generated content** — Gemini API writes fresh post captions and contextually relevant comments based on the actual post being commented on
- **Full SOP lifecycle**
  - Setup → Warmup (follow + like target accounts) → Active loop (post + engage)
  - Automatic error recovery with retry limit before marking account invalid
- **Outreach comments** — visits top model profiles, reads the post + comments, generates a relevant reply; optionally attaches an image (configurable %)
- **Follow / unfollow** — follows tier-1 users and commenters, unfollows on a schedule
- **Mother post repost** — reposts a configured Threads post on account activation
- **Settings GUI** — all parameters controlled from the UI, saved to presets, picked up by workers on next run
- **Per-account logs** — live log viewer in the GUI + log files in `logs/`

---

## Project Structure

```
ThreadsBotV2/
├── main.py                  # GUI entry point
├── coordinator.py           # Spawns & monitors worker processes
├── worker.py                # Per-account lifecycle (setup → warmup → active)
├── config.py                # All settings (overridden by presets/active.json)
├── accounts.txt             # username,adspower_profile_id per line
│
├── core/
│   └── bot.py               # ThreadsBot Selenium wrapper
│
├── browser/
│   └── adspower.py          # AdsPower REST API client
│
├── tasks/
│   ├── setup.py             # Account setup (skipped — manual setup)
│   ├── warmup.py            # Warmup phase: follow + like + feed browse
│   ├── posting.py           # Text posts (AI-generated) + image posts
│   ├── engagement.py        # Outreach comments, follow/unfollow batches
│   └── telegram_task.py     # Mother post repost
│
├── data/
│   └── db.py                # SQLite DB (accounts, follow queue, action log)
│
├── ui/
│   ├── app.py               # CustomTkinter GUI (Accounts / Settings / Logs)
│   └── bio_gen.py           # Bio generation helper
│
├── content/
│   ├── text_posts.txt       # Example posts — Gemini generates variations
│   ├── captions.txt         # Image post captions (one per line)
│   ├── comments.txt         # Fallback comments when AI unavailable
│   ├── tier1_users.txt      # Handles to follow during active loop
│   └── images/              # Images used for posts and pic comments
│
└── presets/
    └── active.json          # Last-saved settings (read by workers at startup)
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
ADSPOWER_URL=http://local.adspower.net:50325/api/v1
ADSPOWER_API_KEY=your_key_here
GEMINI_API_KEY=your_gemini_key_here
```

Or enter the API keys directly in the Settings tab of the GUI.

### 3. Add accounts

Edit `accounts.txt` — one account per line:

```
username1,adspower_profile_id_1
username2,adspower_profile_id_2
```

Log into each account manually in its AdsPower browser profile before starting the bot.

### 4. Add content

| File | Purpose |
|------|---------|
| `content/text_posts.txt` | One example post per line — Gemini writes variations in the same style |
| `content/captions.txt` | Captions for image posts |
| `content/images/` | Images used for posts and pic comments (jpg/png/webp) |
| `content/tier1_users.txt` | `@handles` to follow during the active loop |

### 5. Run

```bash
python main.py
```

---

## Settings Reference

All settings are in the **Settings tab** of the GUI and saved to `presets/active.json`.

| Setting | Description | Default |
|---------|-------------|---------|
| Text posts per cycle | AI posts per posting cycle | 4 |
| Cycle interval MIN/MAX (min) | Time between posting cycles | 30–60 min |
| Image post | Enable image post per cycle | ✓ |
| Likes per scroll MIN/MAX | Likes during active feed browsing | 1–5 |
| Warmup duration (min) | Total warmup session length | 60 min |
| Warmup targets | Comma-separated @handles to visit during warmup | — |
| Warmup likes MIN/MAX | Likes per profile during warmup | 2–6 |
| Warmup max follows | Max accounts to follow during warmup | 8 |
| Comments/day MIN/MAX | Outreach comments per 24h | 4–6 |
| Pic comment ratio % | % of outreach comments that attach an image | 20% |
| Follow batch size | Follows per batch during active loop | 20 |
| Unfollow after (hours) | Hours before unfollowing followed accounts | 2h |
| Outreach targets | Comma-separated @handles to comment on | — |
| Concurrent accounts | How many accounts to run in parallel | 15 |
| Mother post URL | Threads post URL to repost on account activation | — |

---

## How It Works

### Account lifecycle

```
pending → setup → warmup → active (loops every 30–60 min)
                           ↓
                         error (auto-retry up to 5×, then invalid)
```

### Active loop (every cycle)

1. Post 4 AI-generated text posts (mood-based browsing between each)
2. Post 1 image post
3. Outreach: visit target profile → read post + comments → post AI comment (optionally with image)
4. Follow batch from tier1_users.txt
5. Unfollow accounts due for unfollow
6. Idle scroll to keep session alive

### Mood system

Between actions the bot browses the feed in one of four moods:

| Mood | Behavior |
|------|---------|
| casual | Medium scroll, reads occasionally |
| engaged | Slow scroll, reads often, more likes |
| fast | Fast scroll, rarely stops |
| distracted | Long pauses, random stops |

Mood shifts every 4–8 scroll ticks to simulate natural attention patterns.

---

## Notes

- Workers read `presets/active.json` at startup — save settings in the GUI **before** clicking Start
- Accounts marked `invalid` (bad AdsPower profile ID or 5 consecutive errors) won't be retried automatically — fix the issue and click **Reset** in the Accounts tab
- The `content/images/` folder is shared across all accounts; per-account media can be set via the `media_folder` column in the DB
