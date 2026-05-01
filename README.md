# ThreadsBotV2

Multi-account Threads automation bot. Runs up to 15 concurrent accounts through a full lifecycle: warmup → active posting → outreach engagement, all controlled from a dark-mode GUI.

---

## Features

- **Multi-account** — runs N accounts in parallel via Python multiprocessing
- **AdsPower integration** — each account uses its own anti-detect browser profile
- **Human-like behavior** — mood-based scrolling (casual / engaged / fast / distracted) between actions; unpredictable task ordering each cycle
- **AI-generated content** — Gemini API writes posts and comments in authentic LA girl + Gen Z voice (lowkey, delulu, rizz, it's giving, iykyk, era, snatched, etc.) with natural emoji placement
- **Full SOP lifecycle** — Setup → Warmup → Active loop (post + engage) with automatic error recovery
- **Unpredictable post scheduling** — each cycle's posts are shuffled and fired one at a time with 5–25 min gaps rather than back-to-back
- **Outreach comments** — visits target profiles, reads the post + existing comments, generates a contextually relevant reply; optionally attaches an image (configurable %)
- **Follow / unfollow** — follows American male commenters + tier-1 users in mini-batches (1–3 at a time, 20–50 min apart), unfollows on a schedule
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
│   ├── text_posts.txt       # Example posts — Gemini generates variations in the same style
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
| `content/text_posts.txt` | Example posts in your target voice — Gemini writes fresh variations matching the style |
| `content/captions.txt` | Captions for image posts (one per line) |
| `content/images/` | Images used for posts and pic comments (jpg/png/webp/mp4) |
| `content/tier1_users.txt` | `@handles` to follow during the active loop |

The included examples use LA girl + Gen Z slang (lowkey, delulu, rizz, iykyk, etc.) with natural emoji placement. Edit them to match your account's voice.

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
| Likes per scroll MIN/MAX | Likes during active feed browsing | 1–5 |
| Warmup duration (min) | Total warmup session length | 60 min |
| Warmup targets | Comma-separated @handles to visit during warmup | — |
| Warmup likes MIN/MAX | Likes per profile during warmup | 2–6 |
| Warmup max follows | Max accounts to follow during warmup | 8 |
| Comments/day MIN/MAX | Outreach comments per 24h | 4–6 |
| Pic comment ratio % | % of outreach comments that attach an image | 20% |
| Follow batch size | Follows checked per mini-batch | 20 |
| Unfollow after (hours) | Hours before unfollowing followed accounts | 2h |
| Outreach targets | Comma-separated @handles to comment on | — |
| Concurrent accounts | How many accounts to run in parallel | 15 |
| Mother post URL | Threads post URL to repost on account activation | — |

> **Note:** If you set Cycle MIN > MAX by mistake, the bot auto-corrects by swapping them.

---

## How It Works

### Account lifecycle

```
pending → setup → warmup → active (loops every 30–60 min)
                           ↓
                         error (auto-retry, then invalid)
```

### Active loop

Each cycle builds a shuffled task queue and fires one post at a time with randomized gaps:

1. **Post queue** — text posts + 1 image post are shuffled, then fired one at a time with 5–25 min gaps between each
2. **Outreach comments** — every 2–6 hours: visit target profile → read post + comments → post AI reply (optionally with image)
3. **Mini-follow batch** — every 20–50 min: follow 1–3 random tier-1 users (not the whole batch at once)
4. **Follow American male commenters** — while doing outreach, extract commenter names, filter for US male names via Gemini, follow up to 5 of them
5. **Unfollow check** — every 15 min: unfollow anyone past their unfollow window
6. **Idle scroll** — 30–90s of feed scrolling between every task to keep the session alive

### Voice & style

Both AI prompts (posts and comments) use the same Gen Z + LA vocabulary:

> lowkey, highkey, no cap, periodt, fr fr, bestie, slay, ate, it's giving, rent free, understood the assignment, main character, delulu, rizz, iykyk, era, snatched, bussin, hits different, bet, based, real ones know, sending me, manifesting, W, unhinged

Emojis are placed naturally in the text (not stacked at the end): 💅 ✨ 🔥 😭 💀 🫶 😩 🌸 👀 🤌 💁‍♀️ 😍 💫 🌴

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
- Accounts marked `invalid` (bad AdsPower profile ID) won't retry automatically — fix the issue and click **Reset** in the Accounts tab
- The `content/images/` folder is shared across all accounts; per-account media can be configured via the `media_folder` column in the DB
- Posts and follows are intentionally staggered and shuffled — the bot will not post or follow in a predictable pattern
