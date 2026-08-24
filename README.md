# social-auto-upload

我是 Jayz。这个仓库是我自己在本机用的多平台上传工具，日常主要发 **YouTube** 和 **TikTok**。

流程很简单：视频先以私密方式传到 YouTube 做内容/版权检查，通过后再按排期公开；需要的话再发 TikTok。TikTok 应用还没过审时，只能发「仅自己可见」。本机页面在 `http://127.0.0.1:8765`，不会把文件发到我自己的服务器上。

国内平台（抖音、B 站、小红书等）的 CLI 也还在，我没天天用，能跑就留着。

<img src="media/show/tkupload.gif" alt="tiktok show" width="800"/>

## 目录

- [能做什么](#能做什么)
- [安装](#安装)
- [给 AI Agent](#给-ai-agent)
- [怎么用](#怎么用)
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

自己动手看这两份就够了：

- [安装说明](./docs/install.md)
- [更新说明](./docs/update.md)

```bash
git clone https://github.com/Jay-Z-zhang/social-auto-upload.git
cd social-auto-upload
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e .
```

## 给 AI Agent

把仓库丢给 `OpenClaw`、`Codex`、`Claude Code` 时，把 [Agent Bootstrap Prompt](./docs/agent-bootstrap.md) 一起贴过去。它会按 `uv` + `sau` CLI 装环境，先验证命令能不能跑。

补充：

- [CLI 使用说明](./docs/CLI.md)
- [YouTube / TikTok 合规预审](./docs/compliance-setup.md)
- [抖音 Skill](./skills/douyin-upload/SKILL.md) · [快手](./skills/kuaishou-upload/SKILL.md) · [小红书](./skills/xiaohongshu-upload/SKILL.md) · [B 站](./skills/bilibili-upload/SKILL.md)
- 旧 Flask + Vue 页面已经不是主线，见 [历史 Web 说明](./docs/legacy-web.md)

## 怎么用

### 我自己最常用的：合规预审 + 定时公开

```bash
sau review --file videos/demo.mp4 --title "示例标题" --youtube-account <account_name> --platforms youtube --shorts
sau review-batch --dir videos --youtube-account <account_name> --shorts --dry-run
python sau_review_web.py
```

本机页面打开 `http://127.0.0.1:8765`。一次性配置（GCP、TikTok 开发者应用）见 [合规预审说明](./docs/compliance-setup.md)。

`sau youtube` 是另一条路：用浏览器登录 Studio 上传。官方 API 项目如果没过 Google 审核，视频可能被锁私密；所以没有 API 时可以用 Studio。有 API 时我更愿意走 `sau review`。`--visibility` 可选 `public` / `unlisted` / `private`。墙内访问 youtube.com 时，在 `conf.py` 里设 `YT_PROXY`，例如 `"http://127.0.0.1:7890"`。

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
