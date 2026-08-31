# social-auto-upload

我是 Jayz。这个仓库是我自己在本机用的多平台上传工具，日常主要发 **YouTube** 和 **TikTok**。

主线场景：**一部 60 集短剧 → 一条命令铺满 YT + TikTok**。给你一个视频文件夹和剧名，工具负责：LLM 补每集标题/封面主题/描述/tag → 自动生成封面 → 版权预扫 → TikTok 反搬运预处理 → 长版 + Shorts 并发 → 幂等续跑。全程本机跑，不发到我的服务器。

流程精简版：视频先以私密方式传到 YouTube 做内容/版权检查，通过后再按排期公开；需要的话再发 TikTok。TikTok 应用还没过审时，只能发「仅自己可见」。本机页面在 `http://127.0.0.1:8765`。

国内平台（抖音、B 站、小红书等）的 CLI 也还在，我没天天用，能跑就留着。

<img src="media/show/tkupload.gif" alt="tiktok show" width="800"/>

## 目录

- [能做什么](#能做什么)
- [安装](#安装)
- [给 AI Agent](#给-ai-agent)
- [怎么用](#怎么用)
- [三个本机页面](#三个本机页面)
- [我现在在改什么](#我现在在改什么)
- [文档](#文档)
- [许可证](#许可证)

## 能做什么

| 平台 | 登录 | 视频 | 图文 | 定时 | CLI | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| YouTube | ✅ | ✅ | ❌ | ✅ | ✅ | `sau youtube` 走 Studio；`sau review` 走 Data API 预审后可定时公开 |
| TikTok | ✅ | ✅ | ❌ | ✅ | ✅ | `sau review` 走 Content Posting API；另有浏览器示例 |
| 抖音 | ✅ | ✅ | ✅ | ✅ | ✅ | 浏览器自动化 |
| Bilibili | ✅ | ✅ | ❌ | ✅ | ✅ | 运行时自动准备 `biliup` |
| 小红书 | ✅ | ✅ | ✅ | ✅ | ✅ | 浏览器自动化 |
| 快手 | ✅ | ✅ | ✅ | ✅ | ✅ | 浏览器自动化 |
| 视频号 | ✅ | ✅ | ❌ | ✅ | ✅ | 浏览器自动化 |
| 百家号 | ✅ | ✅ | ❌ | ❌ | ✅ | 浏览器自动化 |
| 支付宝生活号 | ✅ | ✅ | ❌ | ❌ | ✅ | 浏览器自动化 |
| 微博 | ✅ | ✅ | ❌ | ❌ | ✅ | 标题最多 30 字 |
| 虎扑 | ✅ | ✅ | ❌ | ❌ | ✅ | 标题 4–40 字 |

上传这种又重复又无聊的事，我不想每次让 Agent 重新认页面。脚本跑熟了更稳。

## 安装

主线用的是 **uv**（比 pip 快、无脑一点）。装完 `sau` 命令就可以跑了。

### macOS

```bash
# 1) 系统级依赖：Homebrew（如果还没装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2) uv + ffmpeg + chromaprint
brew install uv chromaprint ffmpeg

# 3) 拉代码，装 Python 依赖
git clone https://github.com/Jay-Z-zhang/social-auto-upload.git
cd social-auto-upload
uv venv
source .venv/bin/activate
uv pip install -e .

# 4) 配置文件（不会进 git）
cp conf.example.py conf.py
```

### Windows

用 PowerShell（不是 CMD），管理员模式跑第一次装。

```powershell
# 1) 装 winget 常规依赖（Chocolatey / scoop 也行，按你习惯来）
winget install --id astral-sh.uv
winget install --id AcoustID.Chromaprint     # 提供 fpcalc
winget install --id Gyan.FFmpeg              # 提供 ffmpeg / ffprobe
winget install --id Git.Git

# 2) 拉代码，装 Python 依赖
git clone https://github.com/Jay-Z-zhang/social-auto-upload.git
cd social-auto-upload
uv venv
.\.venv\Scripts\activate
uv pip install -e .

# 3) 配置文件（不会进 git）
copy conf.example.py conf.py
```

如果 winget 找不到 chromaprint，去 [acoustid.org/chromaprint](https://acoustid.org/chromaprint) 下 `fpcalc.exe` 手动扔到 PATH 里。ffmpeg 同理。

### 检查依赖

```bash
sau --help         # 应该列出 review / review-batch / gen-metadata 等子命令
fpcalc -version    # 至少 1.5
ffmpeg -version    # 任意近版
```

### 配好 conf.py

打开 `conf.py`，最少要填这几项才能跑起来：

```python
# YouTube — 从 Google Cloud Console 下载 client_secret.json 到项目根目录
YOUTUBE_CLIENT_SECRET_FILE = "client_secret.json"

# TikTok — 沙箱期可留空，只发 SELF_ONLY 时用不到
TIKTOK_CLIENT_KEY = "..."
TIKTOK_CLIENT_SECRET = "..."

# 版权预扫（可选但推荐）— acoustid.org/api-key 免费拿
ACOUSTID_API_KEY = "..."

# 元数据 LLM 生成器（可选）— platform.deepseek.com
DEEPSEEK_API_KEY = "..."
```

一次性 GCP + TikTok 配置的完整步骤见 [docs/compliance-setup.md](./docs/compliance-setup.md)。

## 给 AI Agent

把仓库丢给 `OpenClaw`、`Codex`、`Claude Code` 时，把 [Agent Bootstrap Prompt](./docs/agent-bootstrap.md) 一起贴过去。它会按 `uv` + `sau` CLI 装环境，先验证命令能不能跑。

补充：

- [CLI 使用说明](./docs/CLI.md)
- [YouTube / TikTok 合规预审](./docs/compliance-setup.md)
- [抖音 Skill](./skills/douyin-upload/SKILL.md) · [快手](./skills/kuaishou-upload/SKILL.md) · [小红书](./skills/xiaohongshu-upload/SKILL.md) · [B 站](./skills/bilibili-upload/SKILL.md)
- 旧 Flask + Vue 页面已经不是主线，见 [历史 Web 说明](./docs/legacy-web.md)

## 怎么用

### 一部剧从零到发布（我实际用法）

假设你有一部 60 集短剧的切片，命名是 `mydrama01.mp4 / mydrama02.mp4 / ...`。

**Step 1 — 建目录 + 写 series.yaml**

```bash
mkdir -p videos/dramaA
cp /path/to/切片/*.mp4 videos/dramaA/
```

在 `videos/dramaA/series.yaml` 写全剧信息（4-5 个剧情节点 LLM 就能插值出 60 集）：

```yaml
name: "The CEO's Secret Bride"
total_eps: 60
target_audience: "US/UK women 25-45, drama & romance fans"
tone: "billionaire romance with revenge twist"
synopsis: |
  Emma is forced into a fake marriage with CEO Damien to save her sister.
  As his enemies close in, the fake feelings become real.
episode_outlines: |
  Ep 1  — Emma signs the marriage contract in his office
  Ep 15 — She overhears him tell his mother "she means nothing"
  Ep 30 — His ex fiancée returns, demands the marriage annulled
  Ep 45 — She discovers the contract's real terms
  Ep 60 — Public wedding do-over, contract torn up
hashtags: ["shortdrama", "ceodrama", "romance"]
disclaimer: "Fictional drama. All characters 18+."
shorts_cut_seconds: 59            # 可选：长于 59s 的自动切 Shorts 版
```

**Step 2 — 检查文件名解析对不对**

```bash
sau list-eps --dir videos/dramaA
# [EP01] mydrama01.mp4
# [EP02] mydrama02.mp4
# ...
```

如果有文件识别不到 ep 号，改文件名。

**Step 3 — LLM 一次性生成 60 集元数据**

```bash
sau gen-metadata --dir videos/dramaA
# 花 ~30 秒，写 videos/dramaA/episodes.yaml
```

**Step 4 — 打开审阅页面改词**

```bash
python sau_review_web.py
# 然后浏览器打开 http://127.0.0.1:8765/workspace?dir=videos/dramaA
```

你会看到 60 张卡片：每集封面缩略图 + 主题 + hook + description + tags。逐条 review，改不满意的字段就点保存。

**Step 5 — 试发单集**

```bash
sau review \
  --file videos/dramaA/mydrama01.mp4 \
  --title auto \
  --youtube-account jayz \
  --platforms youtube \
  --shorts
```

会走：本地版权预扫 → 私密传 YT → Content ID 判 → 通过后转公开 + 挂 playlist + 传封面 + Shorts 切片。跑通再上批量。

**Step 6 — 批量**

打开 `http://127.0.0.1:8765/` 老页面填参数点开始，或者用 CLI：

```bash
sau review-batch \
  --dir videos/dramaA \
  --youtube-account jayz \
  --tiktok-account jayz \
  --platforms youtube,tiktok \
  --per-day 3 --times 10,15,20 \
  --shorts
```

**Step 7 — 看进度、恢复中断**

浏览器打开 `http://127.0.0.1:8765/progress?dir=videos/dramaA`：
- 表格式实时进度，每 3s 自动刷新
- 每集看得到状态（done / partial / uploading / pending / error）
- 每集有 Retry / Reset 按钮
- 顶部"继续上次批次"按钮：服务重启、笔记本断电、YT quota 用完，都用这个按钮 resume。manifest 保证已完成的集不会重传

### 其他平台 CLI

```bash
sau douyin login --account <account_name>
sau douyin upload-video --account <account_name> --file videos/demo.mp4 --title "示例标题" --desc "示例简介"

sau kuaishou login --account <account_name>
sau xiaohongshu login --account <account_name>
sau bilibili login --account <account_name>
sau tencent login --account <account_name>
sau baijiahao login --account <account_name>
sau alipay login --account <account_name>
sau weibo login --account <account_name>
sau hupu login --account <account_name>
sau youtube login --account <account_name>
```

账号名只是本地文件名，一个名字对应一份 cookie / OAuth。抖音如果弹出短信验证，会先读项目根目录的 `verify_code.txt`，你在终端里手动跑的话也可以直接输入。

视频字段约定：`title + desc + tags`。图文：`title + note + tags`。B 站不需要自己装 `biliup`，第一次跑会自动下。

`examples/` 里还有一批老脚本，抖音 / 快手 / 小红书 / B 站请优先用上面的 `sau` 命令。

### 常见坑

**macOS 首次跑 sau review 卡住不动**：可能在等 OAuth 授权。看终端有没有一个 `Please visit this URL` 的链接，用浏览器点开授权，之后 `.venv/cookies/youtube_oauth_<account>.json` 就会生成。

**Windows PowerShell 报路径带空格**：给路径加双引号，或者把项目挪到没空格的路径（比如 `C:\projects\social-auto-upload`）。

**墙内访问 YouTube**：`conf.py` 里设 `YT_PROXY = "http://127.0.0.1:7890"`（换成你自己的代理端口）。

**YT 自定义封面 403**：频道没手机验证过，去 [youtube.com/verify](https://youtube.com/verify) 走一遍。代码只 warn 不 fail，视频照发。

**TikTok 只发到 SELF_ONLY**：应用还没过审。TikTok Developer Portal 提审 Content Sharing 才能公开发。


## 三个本机页面

`python sau_review_web.py` 起来后，浏览器打开：

| URL | 用途 |
| --- | --- |
| `/` | 老的发布台 — 填参数点开始跑 batch，看实时日志 |
| `/workspace?dir=videos/xxx` | 逐集审阅元数据 + 手动生成封面，卡片式布局 |
| `/progress?dir=videos/xxx` | 发布进度表格 — 每 3s 刷新、每集 Retry / Reset、顶部"继续上次批次" |

三个页面共享同一个 sqlite manifest (`db/database.db`)，随便哪里改了都能看到。


## 我现在在改什么

我这边主线是：

- YouTube 审核过了再发，公开时间错开，避免同一秒连发
- 本机发布台页面
- CLI 能覆盖我实际会点的按钮

旧 Web 后台还在仓库里，我不当主入口，也不保证还能直接跑。

无头模式就是浏览器在后台跑、不弹窗，适合定时任务和 Agent。

定时发布很多逻辑按「从第二天开始」算，因为我自己也是提前排，不是点下去立刻全发。

## 文档

- [安装](./docs/install.md)
- [CLI](./docs/CLI.md)
- [合规预审](./docs/compliance-setup.md)
- [更新](./docs/update.md)

有 bug 或想改功能，直接提 Issue / PR。改代码前确认你有权提交，并同意按 MIT 发布。

```bash
git checkout -b feature/your-change
git commit -m "Describe the change"
git push origin feature/your-change
```

B 站上传封装了开源项目 [biliup](https://github.com/biliup/biliup)，第三方组件仍走它们自己的许可证。

## 许可证

MIT License，见 [LICENSE](LICENSE)。
