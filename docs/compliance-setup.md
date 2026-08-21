# Compliance Pre-Review Setup Guide

The `sau review` command uses YouTube as a compliance gate: it uploads your video
as **private** to YouTube, waits for YouTube's automated content/copyright checks
to finish, and only publishes (on YouTube and optionally TikTok) if the video
passes review.

This guide walks you through the one-time setup for both platforms.

---

## 1. Google Cloud Project + YouTube Data API v3

### 1.1 Create a GCP Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click **Select a project** → **New Project**.
3. Name it (e.g. `social-auto-upload`) and click **Create**.

### 1.2 Enable the YouTube Data API v3

1. In the GCP Console, go to **APIs & Services** → **Library**.
2. Search for **YouTube Data API v3**.
3. Click it, then click **Enable**.

### 1.3 Create OAuth 2.0 Credentials

1. Go to **APIs & Services** → **Credentials**.
2. Click **+ CREATE CREDENTIALS** → **OAuth client ID**.
3. If prompted, configure the **OAuth consent screen** first:
   - User Type: **External** (or Internal if using Google Workspace).
   - Fill in the app name, support email, etc.
   - Under **Scopes**, add:
     - `https://www.googleapis.com/auth/youtube.upload`
     - `https://www.googleapis.com/auth/youtube`
   - Add your Google account email under **Test users** (required while the
     app is in "Testing" status).
4. Back in Credentials, create an OAuth client ID:
   - Application type: **Desktop app**.
   - Name it anything (e.g. `sau-cli`).
5. Click **Download JSON** and save the file as `client_secret.json` in
   the project root (next to `sau_cli.py`).

### 1.4 Verify Setup

```bash
# Install dependencies if you haven't already
uv pip install -e .

# Test the OAuth flow (opens a browser for Google consent)
sau review --file videos/demo.mp4 --title "Test" --youtube-account my_yt --platforms youtube
```

On first run, a browser window will open asking you to authorize the app.
After consenting, the token is cached at `cookies/youtube_oauth_my_yt.json`
and reused automatically on future runs.

### Important Notes

- **Quota**: The default YouTube API quota is 10,000 units/day.
  `videos.insert` costs 1,600 units, so you can upload ~6 videos/day.
  Request a quota increase via the GCP Console if needed.
- **Unverified API projects**: Videos uploaded via an unverified API project
  are force-locked to private. Since our workflow uploads as private first
  and then switches to public after review, this is fine. However, you
  must add your Google account as a **Test user** in the OAuth consent
  screen while the app is in "Testing" status.

---

## 2. TikTok Developer App + Content Posting API

Skip this section if you only plan to use `--platforms youtube`.

### 2.1 Register as a TikTok Developer

1. Go to [TikTok Developer Portal](https://developers.tiktok.com/).
2. Sign up / log in.
3. Click **Manage apps** → **Connect an app**.

### 2.2 Create an App

1. Fill in the app details (name, description, icon).
2. Under **Add products**, select **Content Posting API**.
3. Enable **Direct Post** mode.
4. Under **Platform**, add a **Web** platform with:
   - Redirect URI: `http://localhost:19876/`
   (This is the local callback server used by `sau review`.)

### 2.3 Get Credentials

1. After the app is created, note the **Client Key** and **Client Secret**.
2. Add them to your `conf.py`:

```python
TIKTOK_CLIENT_KEY = "your_client_key_here"
TIKTOK_CLIENT_SECRET = "your_client_secret_here"
```

### 2.4 Request Scopes

In the app settings, request the following scopes:
- `user.info.basic`
- `video.publish`

These require TikTok's review/approval before they become active.

### 2.5 About the Audit Gate

TikTok restricts all content published by **unaudited** apps to
**private/SELF_ONLY** visibility. This means:

- You can build and test the full flow immediately.
- Videos will be uploaded but only visible to the creator.
- To publish publicly, your app must pass TikTok's **Content Sharing
  audit**. Submit the audit request in the Developer Portal.

The compliance workflow handles this gracefully: if `PUBLIC_TO_EVERYONE` is
not available, it falls back to `SELF_ONLY` and logs a warning.

### 2.6 Verify Setup

```bash
sau review \
  --file videos/demo.mp4 \
  --title "Test Upload" \
  --youtube-account my_yt \
  --tiktok-account my_tk \
  --platforms youtube,tiktok
```

On first run for TikTok, a browser window opens for OAuth authorization.
The token is cached at `cookies/tiktok_oauth_my_tk.json`.

---

## 3. Configuration Reference

All settings go in `conf.py` (copy from `conf.example.py`):

| Setting | Default | Description |
|---------|---------|-------------|
| `YOUTUBE_CLIENT_SECRET_FILE` | `"client_secret.json"` | Path to Google OAuth credentials JSON |
| `TIKTOK_CLIENT_KEY` | `""` | TikTok app Client Key |
| `TIKTOK_CLIENT_SECRET` | `""` | TikTok app Client Secret |
| `COMPLIANCE_POLL_INTERVAL` | `10` | Seconds between status checks |
| `COMPLIANCE_POLL_TIMEOUT` | `600` | Max seconds to wait for processing |
| `COMPLIANCE_DELETE_ON_FAIL` | `True` | Delete private YouTube video if review fails |

---

## 4. CLI Usage Examples

### YouTube only (basic compliance check)

```bash
sau review \
  --file my_video.mp4 \
  --title "My Video Title" \
  --desc "Video description here" \
  --tags "tag1,tag2,tag3" \
  --youtube-account creator1
```

### YouTube + TikTok (review then cross-post)

```bash
sau review \
  --file my_video.mp4 \
  --title "My Video Title" \
  --desc "Video description" \
  --tags "travel,vlog" \
  --platforms youtube,tiktok \
  --youtube-account creator1 \
  --tiktok-account creator1_tk
```

### Scheduled publish (review now, go public later)

```bash
sau review \
  --file my_video.mp4 \
  --title "Scheduled Video" \
  --platforms youtube \
  --youtube-account creator1 \
  --schedule "2026-08-25 14:00"
```

### AI-generated content declaration

```bash
sau review \
  --file ai_video.mp4 \
  --title "AI Generated Content" \
  --youtube-account creator1 \
  --synthetic-media
```

---

## 5. How It Works

```
sau review --file video.mp4 --title "Test" --youtube-account yt1 --platforms youtube,tiktok --tiktok-account tk1
                                 |
                                 v
                   1. Upload to YouTube as PRIVATE
                   (via YouTube Data API v3 videos.insert)
                                 |
                                 v
                   2. Poll processingDetails every 10s
                   (wait for processingStatus != "processing")
                                 |
                                 v
                   3. Check compliance:
                   - status.uploadStatus == "processed"?
                   - status.rejectionReason present?
                   - contentDetails.licensedContent?
                                 |
                    +-------------+-------------+
                    |                           |
                  PASS                        FAIL
                    |                           |
                    v                           v
           4a. YouTube:                 4b. Report rejection
               private -> public            reason to user.
           4b. TikTok:                      Delete private
               upload via API               YouTube video.
               poll until complete          Exit code 2.
                    |
                    v
           5. Print compliance report.
              Exit code 0.
```

---

## 6. Troubleshooting

### "YouTube OAuth client secret file not found"
Download `client_secret.json` from GCP Console → APIs & Services → Credentials.
Place it in the project root or set `YOUTUBE_CLIENT_SECRET_FILE` in `conf.py`.

### "Access blocked: This app's request is invalid" during Google OAuth
Make sure you added your Google account email as a **Test user** in the
GCP OAuth consent screen configuration.

### YouTube quota exceeded
Default is 10,000 units/day (~6 uploads). Request an increase via the
[Audit and Quota Extension Form](https://support.google.com/youtube/contact/yt_api_form).

### TikTok "unaudited_client_can_only_post_to_private_accounts"
Your TikTok developer app hasn't passed the Content Sharing audit yet.
The **TikTok account itself** must be set to private (Settings → Privacy),
not just the post visibility. Until the app is audited, this workflow
posts as `SELF_ONLY`.

### TikTok "client_key" invalid on the login page
Draft **Production** credentials often cannot complete OAuth. Use the
**Sandbox** Client Key / Client Secret from the Sandbox tab, and put the
HTTPS Login Kit redirect URI on the Sandbox app as well:

`https://www.jayzzhang.online/social-auto-upload/tiktok-callback.html`

### TikTok "non_sandbox_target"
Sandbox apps can only be authorized by accounts listed under
**Sandbox → Target Users**. Add the TikTok username, then log in with
that same account.
