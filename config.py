import os
from dotenv import load_dotenv

load_dotenv()

ADSPOWER_URL      = os.getenv("ADSPOWER_URL", "http://local.adspower.net:50325/api/v1")
ADSPOWER_API_KEY  = os.getenv("ADSPOWER_API_KEY", "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")

# URL of the mother account's Threads post (the one that contains the Telegram link).
# Spam pages repost/quote THIS post — they never post the Telegram URL directly.
MOTHER_POST_URL = os.getenv("MOTHER_POST_URL", "")

# How many accounts run at once before rotating to the next batch
BATCH_SIZE = 15

# Warmup duration in seconds (1 hour)
WARMUP_DURATION = 3600

# Post cycle: every 30-60 minutes
POST_CYCLE_MIN = 1800
POST_CYCLE_MAX = 3600

# Text posts per cycle
TEXT_POSTS_PER_CYCLE = 4

# Outreach comments per day
OUTREACH_COMMENTS_MIN = 2
OUTREACH_COMMENTS_MAX = 4

# Follow/unfollow
FOLLOW_BATCH_SIZE = 20
UNFOLLOW_AFTER_SECONDS = 7200  # unfollow 2 hours after following

# Top US models to follow during warmup (Threads profile URLs)
WARMUP_TARGETS = [
    "https://www.threads.net/@amberrosee",
    "https://www.threads.net/@iamcardib",
    "https://www.threads.net/@bellahadid",
    "https://www.threads.net/@emrata",
    "https://www.threads.net/@chrissy_teigen",
]

# Outreach targets — top models whose recent posts we comment on
OUTREACH_TARGETS = [
    "https://www.threads.net/@bellahadid",
    "https://www.threads.net/@emrata",
    "https://www.threads.net/@chrissy_teigen",
]
