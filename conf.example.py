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
# Optional proxy for the TikTok uploader (browser automation). Playwright's firefox
# inherits the Windows system proxy, but that silently breaks when the proxy client
# only runs TUN mode or system proxy is off; the bundled chromium ignores it entirely.
# Point this at your local proxy port, e.g. "http://127.0.0.1:7890". None = no proxy.
TK_PROXY = None

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

# --- Audio copyright preflight (before the private YouTube upload) ---
# Get a free key at https://acoustid.org/api-key . Empty string disables the check.
ACOUSTID_API_KEY = ""
# If AcoustID finds a match at or above this score, mark the video as blocked.
# 0.85 is aggressive; drop to 0.6 for wider net at the cost of false positives.
PREFLIGHT_MIN_SCORE = 0.85
# When True, a match aborts the upload before it hits YouTube. When False, the
# preflight only logs a warning and still uploads.
PREFLIGHT_BLOCK_ON_MATCH = True

# --- LLM metadata generation (sau gen-metadata) ---
# DeepSeek uses an OpenAI-compatible chat completions endpoint.
# Get a key at https://platform.deepseek.com . Empty string disables gen-metadata.
DEEPSEEK_API_KEY = ""
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
