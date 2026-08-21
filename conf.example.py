from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
XHS_SERVER = "http://127.0.0.1:11901"  # only used by xhs-related flows
LOCAL_CHROME_PATH = ""  # optional, e.g. C:/Program Files/Google/Chrome/Application/chrome.exe
LOCAL_CHROME_HEADLESS = True  # default headless behavior for uploader/examples
DEBUG_MODE = True  # default debug behavior
# Optional proxy for the YouTube uploader. Where youtube.com is blocked, direct
# connections time out and the (patchright) chromium does NOT use the system proxy.
# Point this at your local proxy port, e.g. "http://127.0.0.1:7890". None = no proxy.
YT_PROXY = None

# --- Compliance pre-review (sau review) ---
# YouTube Data API v3 OAuth credentials (download from GCP Console).
YOUTUBE_CLIENT_SECRET_FILE = "client_secret.json"
# TikTok Content Posting API credentials (from TikTok Developer Portal).
TIKTOK_CLIENT_KEY = ""
TIKTOK_CLIENT_SECRET = ""
# How often (seconds) to poll YouTube/TikTok for processing status.
COMPLIANCE_POLL_INTERVAL = 10
# Max wait time (seconds) for processing to complete before giving up.
COMPLIANCE_POLL_TIMEOUT = 600
# Auto-delete the private YouTube video if compliance review fails.
COMPLIANCE_DELETE_ON_FAIL = True
