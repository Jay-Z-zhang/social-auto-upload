# TikTok App Review 申请材料

重新提交 TikTok Content Posting API 审核时，用以下内容填写。

---

## App Name

**Social Auto-Publish**

---

## App Description (简短版，150 字以内)

> Social Auto-Publish is a content scheduling platform for creators. Users connect their TikTok accounts via OAuth, upload videos, set captions and tags, and schedule posts. The platform handles batch publishing across TikTok and YouTube, helping creators manage multi-platform distribution efficiently.

---

## App Description (详细版)

> **Social Auto-Publish** is a web-based content scheduling and distribution platform designed for content creators who manage multiple social media accounts.
>
> **Key Features:**
> - **Multi-platform account management:** Creators connect their TikTok, YouTube, and other social accounts via OAuth.
> - **Video upload and organization:** Users upload videos to a media library for easy management.
> - **Batch scheduling:** Creators set posting schedules (e.g., 2 videos per day at 12:00 and 19:00) and the platform publishes automatically.
> - **Cross-platform publishing:** One video can be distributed to TikTok and YouTube simultaneously.
> - **Publishing dashboard:** Real-time status tracking for all scheduled and published posts.
>
> **How TikTok Integration Works:**
> 1. Creator authorizes their TikTok account via OAuth
> 2. Creator uploads video content and enters caption, hashtags
> 3. Creator sets a publishing schedule or publishes immediately
> 4. Platform calls TikTok Content Posting API to upload the video
> 5. Creator views publish status in the dashboard
>
> **Target Users:** Independent content creators, social media managers, and small media teams who need to publish video content across multiple platforms efficiently.

---

## Use Case / User Flow

```
1. Creator signs up / logs in to Social Auto-Publish
2. Creator connects TikTok account via OAuth authorization
3. Creator uploads video files to the media library
4. Creator selects videos, enters title/caption/hashtags
5. Creator chooses "Publish Now" or sets a scheduled time
6. Platform calls TikTok Content Posting API to upload
7. Creator sees real-time publish status in dashboard
8. Creator can manage multiple accounts and batch schedules
```

---

## Why This App Serves External Users (审核关键点)

TikTok 上次拒绝的理由是「personal or internal company use」。重新申请时要强调：

1. **This is a SaaS platform, not a personal tool**
   - The platform is designed to serve multiple content creators
   - Each creator connects their own TikTok account via OAuth
   - The platform operator does not control the content posted

2. **User-initiated publishing**
   - All video uploads and publish actions are initiated by the end user (creator)
   - The platform acts as a scheduling layer, not an automated bot

3. **Multi-tenant architecture**
   - Each user has their own account, media library, and connected platforms
   - The platform supports multiple independent creators

---

## Screenshots to Include

审核时建议提供这些截图：

1. **登录/注册页面** — 证明有用户账号系统
2. **账号管理页面** — 显示可以连接多个 TikTok 账号
3. **发布中心** — 显示用户自己上传视频、填写标题、选择发布时间
4. **发布进度页面** — 显示实时状态追踪

---

## Scopes Requested

申请以下权限：

| Scope | 用途 |
|-------|------|
| `video.upload` | Upload videos to creator's TikTok account |
| `video.publish` | Publish videos (make them visible) |
| `user.info.basic` | Display creator's TikTok username in dashboard |

---

## Privacy Policy URL

```
https://your-domain.com/privacy.html
```

部署后填真实 URL。

---

## Terms of Service URL

```
https://your-domain.com/terms.html
```

部署后填真实 URL。

---

## 提交前 Checklist

- [ ] 落地页已部署，能公开访问
- [ ] Privacy Policy 页面已部署
- [ ] Terms of Service 页面已部署
- [ ] 截图准备好（登录页、账号管理、发布中心、状态页）
- [ ] App Description 已填写（强调服务第三方创作者）
- [ ] OAuth Redirect URI 配置正确
