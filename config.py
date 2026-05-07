import os
import json
from dotenv import load_dotenv

load_dotenv()

ADSPOWER_URL      = os.getenv("ADSPOWER_URL", "http://local.adspower.net:50325/api/v1")
ADSPOWER_API_KEY  = os.getenv("ADSPOWER_API_KEY", "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
GROK_API_KEY      = os.getenv("GROK_API_KEY", "")

MOTHER_POST_URL = os.getenv("MOTHER_POST_URL", "")
MOTHER_REPOST_ENABLED = True
MOTHER_REPOST_CTA_FILE = ""
MOTHER_REPOST_IMAGES_FOLDER = ""

GHOST_POSTS_PER_CYCLE = 0

BATCH_SIZE = 15

WARMUP_ENABLED      = True
WARMUP_DURATION     = 3600
WARMUP_LIKES_MIN    = 2
WARMUP_LIKES_MAX    = 6
WARMUP_MAX_FOLLOWS  = 8

POST_CYCLE_MIN = 1800
POST_CYCLE_MAX = 3600
SESSION_DURATION = 1800  # fixed active session length per account (seconds)

TEXT_POSTS_PER_CYCLE = 4

ACTIVE_LIKES_MIN = 1
ACTIVE_LIKES_MAX = 5

OUTREACH_COMMENTS_MIN = 4
OUTREACH_COMMENTS_MAX = 6

PIC_COMMENT_RATIO = 20  # % of outreach comments that include an image

FOLLOW_BATCH_SIZE = 20
UNFOLLOW_AFTER_SECONDS = 7200

WARMUP_TARGETS = [
    "https://www.threads.com/@amberrosee",
    "https://www.threads.com/@iamcardib",
    "https://www.threads.com/@bellahadid",
    "https://www.threads.com/@emrata",
    "https://www.threads.com/@chrissy_teigen",
]

OUTREACH_TARGETS = [
    "https://www.threads.com/@bellahadid",
    "https://www.threads.com/@emrata",
    "https://www.threads.com/@chrissy_teigen",
]

# Override defaults with UI-saved settings (written by the GUI on Start)
_active = os.path.join(os.path.dirname(__file__), "presets", "active.json")
if os.path.exists(_active):
    try:
        with open(_active, "r", encoding="utf-8") as _f:
            _s = json.load(_f)
        WARMUP_ENABLED         = _s.get("warmup_enabled", WARMUP_ENABLED)
        WARMUP_DURATION        = _s.get("warmup_duration", WARMUP_DURATION // 60) * 60
        WARMUP_LIKES_MIN       = _s.get("warmup_likes_min", WARMUP_LIKES_MIN)
        WARMUP_LIKES_MAX       = _s.get("warmup_likes_max", WARMUP_LIKES_MAX)
        WARMUP_MAX_FOLLOWS     = _s.get("warmup_max_follows", WARMUP_MAX_FOLLOWS)
        POST_CYCLE_MIN         = _s.get("cycle_min", POST_CYCLE_MIN // 60) * 60
        POST_CYCLE_MAX         = _s.get("cycle_max", POST_CYCLE_MAX // 60) * 60
        if POST_CYCLE_MIN > POST_CYCLE_MAX:
            POST_CYCLE_MIN, POST_CYCLE_MAX = POST_CYCLE_MAX, POST_CYCLE_MIN
        OUTREACH_COMMENTS_MIN  = _s.get("comments_min", OUTREACH_COMMENTS_MIN)
        OUTREACH_COMMENTS_MAX  = _s.get("comments_max", OUTREACH_COMMENTS_MAX)
        FOLLOW_BATCH_SIZE      = _s.get("follow_batch", FOLLOW_BATCH_SIZE)
        UNFOLLOW_AFTER_SECONDS = _s.get("unfollow_hours", UNFOLLOW_AFTER_SECONDS // 3600) * 3600
        ACTIVE_LIKES_MIN       = _s.get("active_likes_min", ACTIVE_LIKES_MIN)
        ACTIVE_LIKES_MAX       = _s.get("active_likes_max", ACTIVE_LIKES_MAX)
        TEXT_POSTS_PER_CYCLE   = _s.get("text_posts", TEXT_POSTS_PER_CYCLE)
        SESSION_DURATION       = _s.get("session_duration", SESSION_DURATION // 60) * 60
        PIC_COMMENT_RATIO      = _s.get("pic_comment_ratio", PIC_COMMENT_RATIO)
        MOTHER_POST_URL        = _s.get("mother_post_url", MOTHER_POST_URL)
        MOTHER_REPOST_ENABLED       = _s.get("mother_repost_enabled", MOTHER_REPOST_ENABLED)
        MOTHER_REPOST_CTA_FILE      = _s.get("mother_repost_cta_file", MOTHER_REPOST_CTA_FILE)
        MOTHER_REPOST_IMAGES_FOLDER = _s.get("mother_repost_images_folder", MOTHER_REPOST_IMAGES_FOLDER)
        GHOST_POSTS_PER_CYCLE       = _s.get("ghost_posts_per_cycle", GHOST_POSTS_PER_CYCLE)
        BATCH_SIZE             = _s.get("batch_size", BATCH_SIZE)
        if _s.get("adspower_api_key"):
            ADSPOWER_API_KEY   = _s["adspower_api_key"]
        if _s.get("grok_key"):
            GROK_API_KEY       = _s["grok_key"]
        _raw_w = [h.strip().lstrip("@") for h in _s.get("warmup_targets", "").split(",") if h.strip()]
        if _raw_w:
            WARMUP_TARGETS = [f"https://www.threads.net/@{h}" for h in _raw_w]
        _raw_o = [h.strip().lstrip("@") for h in _s.get("outreach_targets", "").split(",") if h.strip()]
        if _raw_o:
            OUTREACH_TARGETS = [f"https://www.threads.net/@{h}" for h in _raw_o]
    except Exception:
        pass
