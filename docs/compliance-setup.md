# Compliance Pre-Review Setup Guide

This is the flow Jayz actually uses: `sau review` uploads a video as **private**
to YouTube, waits for YouTube's automated content/copyright checks, and only
publishes (YouTube, optionally TikTok) if the video passes.

One-time setup for both platforms is below. The local page is
`python sau_review_web.py` → `http://127.0.0.1:8765`.

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
| `ACOUSTID_API_KEY` | `""` | AcoustID key for audio preflight. Empty disables the check. |
| `PREFLIGHT_MIN_SCORE` | `0.85` | AcoustID score threshold; a match at or above blocks/warns. |
| `PREFLIGHT_BLOCK_ON_MATCH` | `True` | If True, a preflight match aborts before YouTube is called. |

---

## 3b. Audio Copyright Preflight (before the YouTube gate)

The YouTube gate catches Content ID matches, but it costs an API upload every
time. Audio preflight runs locally first and short-circuits obvious hits so
you don't burn quota.

### 3b.1 Install fpcalc (Chromaprint)

`fpcalc` is the Chromaprint CLI. macOS:

```bash
brew install chromaprint
```

Ubuntu/Debian: `sudo apt install libchromaprint-tools`. Windows: download the
binary from [acoustid.org/chromaprint](https://acoustid.org/chromaprint) and put
`fpcalc.exe` on PATH.

### 3b.2 Get an AcoustID API key

Free at [acoustid.org/api-key](https://acoustid.org/api-key) — sign in, register
an application, copy the key into `conf.py`:

```python
ACOUSTID_API_KEY = "your-key-here"
PREFLIGHT_MIN_SCORE = 0.85
PREFLIGHT_BLOCK_ON_MATCH = True
```

### 3b.3 Try it standalone

```bash
sau preflight-audio --file videos/ep_03.mp4
```

Exit codes: `0` clean, `1` couldn't run (missing key or fpcalc), `2` matched.
`--no-block` reports without failing. `--min-score 0.6` widens the net.

### 3b.4 What preflight does NOT catch

AcoustID's fingerprint DB is user-contributed, biased toward music tracks. It
will miss unregistered BGM, movie/TV soundtracks that aren't in MusicBrainz, and
purely visual claims. The YouTube gate remains the source of truth — preflight
just saves the round trip for the easy hits.

Clips shorter than ~15s are auto-skipped (AcoustID needs enough audio to match).

---

## 3c. TikTok anti-repost preprocessing

TikTok's dedupe engine looks at file hash, container metadata, video signature
and audio fingerprint. Uploading the same file to YouTube and TikTok is the
fastest way to get flagged as unoriginal. `sau review --platforms youtube,tiktok`
now generates a TikTok-specific variant automatically before the TikTok upload.

### 3c.1 What it does

Runs one ffmpeg pass per episode:

- Strip container metadata (`-map_metadata -1`)
- Re-encode video with libx264 at `--crf 23` (different bitstream than the YT copy)
- Micro-crop 4px per edge (`--crop-px 4`), so the video signature shifts
- Re-encode audio to AAC 128k (different audio fingerprint from source)
- Optional `--tiktok-mirror` horizontal flip (strongest signal, but breaks text/logos)

Output lands at `<source_dir>/tiktok/<name>_tt.mp4`. Cached on disk — re-runs
skip the ffmpeg pass unless you pass `--overwrite`.

### 3c.2 Standalone

```bash
sau prep-tiktok --file videos/ep_01.mp4
# → videos/tiktok/ep_01_tt.mp4

sau prep-tiktok --file videos/ep_01.mp4 --mirror --crf 20 --overwrite
```

### 3c.3 In the review pipeline

Default: enabled. YT gets the original, TikTok gets the prepped variant.

```bash
# default — TikTok gets a prepped variant
sau review --file videos/ep_01.mp4 --title "..." --platforms youtube,tiktok \
  --youtube-account jayz --tiktok-account jayz

# disable prep (upload original file to both)
sau review ... --no-tiktok-prep

# use the horizontal flip as well
sau review ... --tiktok-mirror
```

`review-batch` accepts the same flags.

### 3c.4 When NOT to mirror

`--tiktok-mirror` is the single strongest dedupe signal but it also flips
on-screen text, subtitles, logos, and any left-right asymmetry the audience
would notice. Turn it on only for pure visual dramas without burned-in
captions. If your drama has hardcoded subtitles or a station logo, leave it off.

---

## 3d. Publishing pacing (anti-throttle)

`sau review-batch` enforces per-run rate limits so a whole season doesn't submit
in five minutes. Defaults align with what real accounts do:

| Flag | Default | What it does |
|---|---|---|
| `--min-interval` | `90` | Seconds between consecutive submits. `run_review` for one item usually takes 60–120s already, but this floors it. |
| `--yt-per-day-max` | `3` | Warn if `--per-day` is higher. YouTube's Data API quota still hard-caps at ~6/day (each insert = 1600 units of 10k). |
| `--tt-per-day-max` | `5` | Warn if the batch has more TikTok posts than this in one run. |

Warnings don't abort — they log and continue. The API-quota ceiling (6/day)
does abort with a clear error, because there's no point starting a run that
YouTube will refuse mid-way.

---

## 3e. Series metadata (`series.yaml`)

Drop a `series.yaml` in the batch directory to drive titles, hashtags,
disclaimers, and the YouTube playlist across every episode of a drama.

### 3e.1 File format

```yaml
name: "Second Chance"
total_eps: 60
hashtags: ["drama", "shortdrama", "romance"]
disclaimer: "Fictional drama. All characters 18+."
youtube_playlist_id: "PLxxxxxxxxxxxx"
description_footer: |
  ▶ Full Playlist: https://youtube.com/playlist?list=PLxxxxxxxxxxxx

  Cast: A / B / C
  Writer: Jayz

# optional; default is: "{name} EP{ep:02d}/{total} | {hook}"
title_template: "{name} EP{ep:02d}/{total} | {hook}"
```

Only `name` is required. Everything else is optional.

### 3e.2 Filename → episode number

The parser picks up `ep01`, `EP03`, `ep_03`, `s2e07`, and a bare leading number
like `03_scene.mp4`. Files it can't parse fall back to sidecar titles verbatim
(logged as a warning). Two-digit zero-padded is the safe format.

### 3e.3 Sidecar `.txt` becomes the hook

`ep_03.txt` line 1 is the per-episode hook (e.g. `She finally learns the truth`),
line 2 is per-episode hashtags, lines 3+ is per-episode description. The
template composes it into:

```
Second Chance EP03/60 | She finally learns the truth
```

And the description is: `<sidecar description>` → `<footer>` → `<disclaimer>`,
each block separated by a blank line. Tags = sidecar tags ∪ series hashtags,
deduped, order preserved.

### 3e.4 Auto playlist append

If `youtube_playlist_id` is set, each successfully published episode is
appended to that playlist via `playlistItems.insert`. Failure is non-fatal
(the video is already live). Find the ID in a playlist URL:

```
https://youtube.com/playlist?list=PLxxxxxxxxxxxx
                                   ^^^^^^^^^^^^^^ that
```

Playlist must belong to the YouTube channel `--youtube-account` is logged
into.

---

## 3f. Auto-generated covers

YouTube gives you a cover image slot; the default is a random processing frame.
Custom covers with a big EP number consistently outperform in playlists — the
audience is skimming episode chips, and the EP number is the second thing they
read after the title. `sau review-batch` generates one per episode when a
`series.yaml` is present.

### 3f.1 What gets generated

- Sample a single frame at `frame_at` (default 0.35 = 35% into the clip)
- Scale-and-crop to 1280×720
- Dim the frame ~20%; overlay a dark strip along the bottom
- Draw `EP 03` big in yellow (Impact / Arial Black / Helvetica, whichever the
  system has) plus the series name in white
- Save as `<video_stem>_cover.jpg` next to the video

### 3f.2 Manual override

Drop your own `<video_stem>_cover.jpg` (JPG or PNG, < 2MB, ideally 1280×720)
in the batch directory. The pipeline picks that up and skips generation — hand
work always wins.

### 3f.3 Standalone

```bash
sau make-cover --file videos/dramaA/ep_03.mp4 --series-name "Second Chance"
# → videos/dramaA/ep_03_cover.jpg

sau make-cover --file videos/dramaA/ep_05.mp4 --series-name "Second Chance" \
  --frame-at 0.5 --font "/System/Library/Fonts/Supplemental/Impact.ttf" \
  --overwrite
```

`--ep` is auto-parsed from the filename when omitted.

### 3f.4 In the review pipeline

```bash
# default — auto covers on when series.yaml is present
sau review-batch --dir videos/dramaA --youtube-account jayz --platforms youtube

# only use hand-crafted covers, skip auto generation
sau review-batch --dir videos/dramaA ... --no-auto-cover
```

### 3f.5 YouTube requirements

Custom thumbnails require a verified YouTube channel (phone-verified in Studio).
If the channel isn't verified, `thumbnails.set` returns a 403 and the pipeline
logs a warning — video is already published, only the cover fell back to a
processing frame. Verify the channel once at
[youtube.com/verify](https://youtube.com/verify) and re-run cover upload with a
one-off `sau make-cover` + manual Studio upload.

---

## 3g. Per-episode metadata (`episodes.yaml`)

`series.yaml` covers cross-episode constants; `episodes.yaml` covers per-episode
content — the cover theme, the title hook, the description, and the tags. When
both are present, `episodes.yaml` wins for every field it defines.

```yaml
# videos/dramaA/episodes.yaml
1:
  theme: "BETRAYAL AT THE WEDDING"
  hook: "She said YES. He said her sister's name."
  description: |
    Emma walked down the aisle. Her fiancé said 'I do' — to her sister.
    She'd give anything to disappear. Then a stranger offered exactly that.
  tags: ["betrayal", "wedding", "revenge", "billionaire"]
  tt_tags: ["wedding", "revenge"]     # optional TikTok subset

2:
  theme: "STRANGER IN A HOSPITAL BED"
  hook: "..."
  ...
```

Handwrite it, or use `sau gen-metadata` (§3h) to draft it and edit from there.

Filename → episode number matching now covers four patterns:

- `ep_01.mp4` / `EP03.mp4` / `s2e07.mp4`
- `01_scene.mp4` (leading number)
- `mydrama01.mp4` / `SecondChance02.mp4` (trailing number)
- `drama_003.mp4` / `drama_60_final.mp4` (embedded number)

Run `sau list-eps --dir <folder>` to preview what the parser thinks before
committing to an upload run:

```bash
sau list-eps --dir videos/dramaA
# Series: Second Chance (total_eps=60)
#   [EP01]  mydrama01.mp4
#   [EP02]  mydrama02.mp4
#   [  ??]  behind_the_scenes.mp4
```

Exit code 0 when every file parses, 1 otherwise — useful in CI or a pre-run hook.

---

## 3h. LLM metadata generation (`sau gen-metadata`)

Given a `series.yaml` with synopsis + rough episode outlines, `sau gen-metadata`
calls DeepSeek once and drops a full `episodes.yaml`. You review, edit the
questionable ones, and then `sau review-batch` reads the file — DeepSeek is
never called at publish time.

### 3h.1 Configure the key

Get an API key at [platform.deepseek.com](https://platform.deepseek.com) (free
tier is enough for a season) and put it in `conf.py`:

```python
DEEPSEEK_API_KEY = "your-key-here"
DEEPSEEK_MODEL = "deepseek-chat"       # or deepseek-reasoner
```

`conf.py` is in `.gitignore` — never commit real keys.

### 3h.2 Enrich `series.yaml`

The generator needs enough context to write a coherent drama arc. Minimum:

```yaml
name: "Second Chance"
total_eps: 60
target_audience: "US/UK women 25-45, drama & romance fans"
tone: "high-stakes revenge romance, cliffhanger every episode"
synopsis: |
  Emma catches her fiancé cheating with her sister on the wedding day.
  She flees, meets billionaire Damien who lost his memory in an accident.
  They marry for revenge but slowly fall in love...
episode_outlines: |
  Ep 1: Wedding day betrayal, Emma runs away in the rain
  Ep 2: Meets Damien at the hospital, he mistakes her for his wife
  Ep 3-5: They stage a fake marriage, sister shows up furious
  Ep 6-10: Damien's memory starts returning
  ...
```

You don't need to write all 60 outlines — the model interpolates. But the more
you write, the more the generated hooks stay on-plot.

### 3h.3 Minimum-input mode (recommended when you only know the title)

Short-form dramas often ship with just a title and no scene-by-scene script.
Instead of writing a full 60-episode outline, write 4-5 milestone episodes —
inciting incident, mid-point twist, low point, climax. The LLM interpolates
the rest, and every generated episode has a real story anchor to point at
instead of recycling the title.

```yaml
name: "The CEO's Secret Bride"
total_eps: 60
target_audience: "US/UK women 25-45, drama & romance fans"
tone: "billionaire romance with mistaken-identity twists"
synopsis: |
  A working-class woman is forced into a fake marriage with a cold-hearted CEO
  to save her family. As his enemies close in, the fake feelings become real.
episode_outlines: |
  Ep 1  — Forced-marriage contract signed at the office
  Ep 15 — She overhears him tell his mother "she means nothing"
  Ep 30 — His ex fiancée returns, demands the marriage annulled
  Ep 45 — She discovers the contract's real terms — she's collateral
  Ep 60 — Public wedding do-over, contract torn up

  (LLM: interpolate the episodes between these anchors with rising stakes,
  a mid-episode cliffhanger, and enough setup for the next milestone.)
```

That's the entire authoring effort. `sau gen-metadata --dir ...` fills in 60
episodes' worth of theme/hook/description/tags off this seed.

**Bad seed** — leaves no anchor to write against:

```yaml
name: "The CEO's Secret Bride"
synopsis: "A CEO and a girl fall in love."
episode_outlines: ""
```

The output will be 60 nearly identical descriptions all restating the title.
Always give the model at least the 4 milestones above.

### 3h.3 Run it

```bash
# generate everything missing from episodes.yaml
sau gen-metadata --dir videos/dramaA

# only certain episodes (comma-separated numbers or ranges)
sau gen-metadata --dir videos/dramaA --episodes 1-10,15

# regenerate even ones that already have entries
sau gen-metadata --dir videos/dramaA --episodes 3 --overwrite

# preview JSON without writing
sau gen-metadata --dir videos/dramaA --dry-run
```

Existing entries are kept when `--overwrite` is off — safe to re-run to fill gaps.

### 3h.4 Review the output

Every generated episode gets a `theme` (cover), a `hook` (title tail), a
`description`, and tags. Read the file, edit anything that sounds off, and
lock it. The upload pipeline reads only this file — the LLM never runs again.

---

## 3i. Hashtag strategy across platforms

Different platforms punish tag overload differently. The pipeline splits tags
per platform:

- **YouTube**: up to 12 tags. Combined from `episodes.yaml::tags` + `series.yaml::hashtags`.
- **TikTok**: up to 5 tags. Reads `episodes.yaml::tt_tags` if present, otherwise
  the first 3 of the YouTube list. TikTok posts with many tags get quieter
  distribution — keep it lean.

TikTok caption composition: pipeline glues the title and the hashtags into one
string (`<title>\n\n#tag1 #tag2 …`) since TikTok's Content Posting API has no
separate tags field. Capped at TikTok's 2200-char caption limit.

---

## 3j. Shorts + long-form companion uploads

When you want the same episode to hit **both** the YT Shorts feed (for
discovery) and the long-form feed (for retention/watchtime), enable Shorts
companion mode. Every source video longer than N seconds gets a `<stem>_short.mp4`
slice uploaded alongside the long form.

### 3j.1 Enable

In `series.yaml`:

```yaml
shorts_cut_seconds: 59           # 0 disables. Between 15 and 60.
shorts_playlist_id: "PLxxxxxx"   # optional; falls back to youtube_playlist_id
```

`sau review-batch` reads these on every run — no CLI flag needed. The
companion is created lazily right before publish; it's cached as
`<stem>_short.mp4` on disk so subsequent runs reuse it (`--overwrite` on
`sau split-shorts` if you need to regen).

### 3j.2 What the pipeline actually does per episode

1. Long-form (original file) goes through the full flow: preflight → private
   upload → compliance → schedule public + playlist + cover.
2. If `shorts_cut_seconds` triggers, ffmpeg slices the first N seconds into
   `<stem>_short.mp4`.
3. The Shorts variant is uploaded as a **second** private video, then made
   public/scheduled at the **same time** as the long form.
4. Shorts inherits: title + `#Shorts`, description + `#Shorts`, cover,
   tags, and (`shorts_playlist_id` if set, else) the main playlist.
5. Compliance is **not** re-checked — the long form just passed, and the
   Shorts is a strict subset. This saves the quota-heavy `videos.list` calls.

### 3j.3 Quota accounting

Each YT Data API `videos.insert` costs 1600 units of the daily 10k quota.
With Shorts companion enabled every episode costs **2** inserts. `run_batch_review`
hard-caps at 6 inserts/day (existing behavior), so:

- Shorts off: up to 6 episodes/day
- Shorts on: up to 3 episodes/day

Batch aborts before starting when the projected quota exceeds this. Split the
folder or disable `shorts_cut_seconds` for one run if you need to catch up.

### 3j.4 Standalone

```bash
sau split-shorts --file videos/dramaA/ep_01.mp4
# → videos/dramaA/ep_01_short.mp4 (first 59s)

sau split-shorts --file videos/dramaA/ep_01.mp4 --duration 45 --overwrite
```

Exit codes: `0` sliced OK, `1` ffmpeg/probe error, `3` source already ≤ target
(no work done, safe to ignore in a batch script).

### 3j.5 When NOT to enable

- Your channel is on a Data API quota tier below the default 10k/day.
- Your source is already ≤ 60s (Shorts variant would be a no-op — you can
  just publish as `--shorts`).
- You're trying to maximize the number of independent episodes per day; then
  quota is better spent on more titles.

---

## 3k. Idempotent batch resume (manifest)

Every `sau review-batch` run writes per-episode outcomes to a sqlite manifest
at `db/database.db`. Re-running the same batch:

1. Consults the manifest before touching each episode.
2. If **everything** requested is already live for that episode → skip entirely.
3. If **partially** done → resume from where it stopped:
   - Long-form YT already up → skip preflight + upload + compliance + publish.
   - Shorts companion already up → skip.
   - TikTok already up → skip.
4. Anything that hasn't landed re-runs normally.

The manifest key is `(batch_dir, episode)`, so moving a folder invalidates the
manifest — deliberate; renaming shouldn't silently reuse someone else's row.

### 3k.1 Why this matters

Real batch runs fail mid-flight — YT quota hits at ep 47/60, TikTok rate-limits
at ep 32, your laptop goes to sleep. Without a manifest, the next run wants to
re-upload the first 46 episodes (which are already public) and burns quota /
duplicates content. The manifest turns "retry the whole batch" into "resume
from ep 47."

### 3k.2 See it

```bash
sau status --dir videos/dramaA
```

Output looks like:

```
  EP  STATUS    YT VIDEO      SHORTS        TIKTOK           UPDATED
------------------------------------------------------------------------
   1  done      abc123        xyz789        v-98765          2026-09-01T12:00
   2  done      def456        xyz790        v-98766          2026-09-01T12:03
   3  error     ghi789        -             …                2026-09-01T12:05
      ↳ TikTok publish failed: rate limit
   4  pending   -             -             -                2026-09-01T12:05
------------------------------------------------------------------------
Total 4: done=2 partial=0 pending=1 error=1
```

- `done` — everything requested is live
- `partial` — some platforms landed, others still to go
- `error` — the last run left a non-fatal error; re-running the batch will retry
- `pending` — never attempted or fully reset

Rows show the actual YT videoId / publish_id so you can jump to Studio /
TikTok admin. `…` means "we wanted to try this platform but didn't succeed;
next batch run will retry it."

### 3k.3 Reset (force re-upload)

Regenerated a video and want the batch to re-treat it as new? Clear its row:

```bash
# clear one episode
sau status --dir videos/dramaA --reset 3

# clear the whole directory (prompts for confirmation)
sau status --dir videos/dramaA --reset-all
```

This does **not** delete the videos already on YouTube / TikTok — it only tells
the manifest to forget. You'll typically want to also delete the old video on
YT Studio to avoid duplicates, then let the batch re-upload.

### 3k.4 The manifest lives across sessions

`db/database.db` is a plain sqlite file at project root. Committing it is
optional but generally not recommended (includes source filenames). Delete it
to reset everything for every drama.

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

### YouTube Shorts

```bash
sau review \
  --file short.mp4 \
  --title "My Short" \
  --youtube-account creator1 \
  --shorts
```

`--shorts` appends `#Shorts` to the title/description if it is not already there.

### Batch scheduled publish (`sau review-batch`)

Put videos in one folder. Optional sibling `.txt` files: line 1 title, line 2 tags, remaining lines description.

```
shorts/
  01.mp4
  01.txt
  02.mp4
```

Preview the schedule only:

```bash
sau review-batch \
  --dir shorts \
  --youtube-account creator1 \
  --shorts \
  --dry-run
```

Upload now (private), then stagger public times (default: 2 per day at 12:00 and 19:00 local time):

```bash
sau review-batch \
  --dir shorts \
  --youtube-account creator1 \
  --tiktok-account creator1_tk \
  --platforms youtube,tiktok \
  --per-day 2 \
  --times 12,19 \
  --start-days 1 \
  --shorts
```

YouTube API quota is about 6 `videos.insert` calls per day. The batch command refuses folders larger than that.

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

## 6. Local web UI

A local page wraps `sau review-batch`: scan a folder, preview the schedule, dry-run, then run with live logs.

```bash
python sau_review_web.py
# or, after `uv pip install -e .`:
sau-web
```

It opens `http://127.0.0.1:8765` (bind is localhost only). This is not a public website and does not replace the TikTok Developer Portal.

On the page:

1. Paste the folder path and click **扫描视频**.
2. Pick YouTube / TikTok account names (same names used in `cookies/youtube_oauth_*.json`).
3. Click **预览排期**, then **空跑（不上传）** to confirm times.
4. Click **开始发布** to upload privately to YouTube, wait for compliance, then schedule public times.

Until the TikTok app passes Content Sharing audit, keep TikTok unchecked, or expect `SELF_ONLY` posts.

---

## 6b. Episode Workspace UI (`/workspace`)

The batch page (§6) is fine when the metadata is already right. But most of the
time you're editing 60 auto-generated entries and eyeballing 60 covers. The
Workspace page opens on the same `python sau_review_web.py` server and gives you
one card per episode — cover thumbnail on the left, theme/hook/description/tags
on the right, all editable inline, one-click regen for covers.

### 6b.1 Open it

```bash
python sau_review_web.py    # or `sau-web` after uv pip install -e .
```

Then hit:

```
http://127.0.0.1:8765/workspace
http://127.0.0.1:8765/workspace?dir=videos/dramaTest   # pre-fill the folder
```

Type the folder path in the top bar (relative to project root, or absolute),
click **加载**. Cards load lazily as one flat list — scroll through, edit,
save per-card.

### 6b.2 What each card does

- **Cover thumbnail**: reads `<video_stem>_cover.jpg` next to the video.
  Missing → shows "未生成封面" and the button reads "生成封面".
- **Regen button**: calls `make_cover` with the current `theme` you typed in
  (even if unsaved), overwrites the file, and refreshes the thumbnail
  in-place. Failure → toast, retry.
- **Save button**: patches only that episode's entry in `episodes.yaml`
  (creates the file if missing). Other episodes are untouched.
- **Theme / Hook / Description / Tags / TT tags**: same fields as
  `episodes.yaml`. Empty inputs are saved as empty strings — no silent
  fallback to sidecar, so you always know what's on disk.

### 6b.3 Typical flow

1. `sau gen-metadata --dir videos/dramaA` — LLM drafts the 60-episode yaml
2. Open `/workspace?dir=videos/dramaA` — scroll through, fix theme/hook where
   the LLM was lazy, save each fix
3. Click **生成封面** on cards where the auto-cover doesn't match the theme
4. Head back to `/` and run **开始发布** to upload the batch

The workspace never uploads on its own — it only edits `episodes.yaml` and
regenerates local jpgs. Publishing is still an explicit action on the main
page.

### 6b.4 Limits (for now)

- No batch save — you save one card at a time. Refresh the page and unsaved
  edits are lost.
- No multi-drama picker — one dir per page load.
- No dry-run preview of the final title after series template rendering; the
  `hook` field shown here is the hook only, the pipeline glues
  `{series.name} EP{ep}/{total} | {hook}` at publish time.

Those are on the list if this page pulls its weight.

---

## 7. Troubleshooting

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
