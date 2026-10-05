# BiliSummaryStream · Bilibili video summarizer with per-account push streams

**English** · [简体中文](README.md)

Automated Bilibili (bilibili.com) video → summary → DM push system, powered by **local GPU transcription (faster-whisper)** plus **LLM distillation**.

A cloud master node polls 7×24 and handles summarization and delivery; when your PC is on, its GPU transparently takes over transcription — and when the PC goes offline, the system **degrades seamlessly** to cloud multimodal extraction (audio ASR + keyframe vision for silent videos). It only ever summarizes **real content**: if it cannot obtain the actual material, it queues honestly instead of inventing a summary from the title.

---

## Why

A 40-minute Bilibili video may contain 3 minutes of substance. Watching everything is too expensive; judging by title is how you get clickbaited.

This project turns "watching" into "reading a push":

- Change which keywords you follow, which creators you track, and how summaries are written — **by chatting with the bot in Bilibili DMs**;
- See something good: **share it to the bot's DM** and get a deep summary minutes later;
- **@ the bot** in a video's comment section for the same result;
- Multiple receiver accounts each keep their own profile (independent keywords, creators, and style).

---

## Features

| # | Feature | Notes |
|---|---|---|
| 1 | **Per-account profiles** | Isolated keywords / creator lists / distillation style per account |
| 2 | **Config via DM commands** | `设置关键词 …`, `设置风格 …`, `监控UP 123456` — hot-reloaded, instantly confirmed |
| 3 | **Share-to-summarize** | Share a video card (or send a BV id / URL) and get a summary DM back |
| 4 | **Comment @ trigger** | `@your_bot` in comments is captured and answered by DM |
| 5 | **Hybrid cloud/edge routing** | 0.8s liveness probe: GPU when online, cloud multimodal when offline, never interrupted |
| 6 | **Relevance gate + honest degradation** | Never fabricates: queues for retry or sends an explicit "pending" notice |

---

## Architecture

```
┌──────────────────────────────────┐            ┌──────────────────────────────────┐
│  Cloud master (Linux / systemd)  │   HTTP     │  Edge GPU node (Windows, opt.)   │
│  bili-summary.service            │  :18088    │  edge_worker  (:18088)           │
│                                  │            │                                  │
│  · 30-min multi-account polling  │ ─────────► │  · faster-whisper large-v3-turbo │
│  · DM / @ listener (6s poll)     │ ◄───────── │  · local cookies for audio URLs  │
│  · LLM distillation + push       │            │  · serial GPU queue, subprocess  │
│  · Cloud multimodal fallback     │            │  · ~80MB resident                │
└──────────────────────────────────┘            └──────────────────────────────────┘
```

**Degradation chain**

```
local GPU transcription ──offline──► cloud audio ASR ──silent──► cloud keyframe vision ──fail──► retry queue
```

Key point: Bilibili audio URLs are **IP-bound**. URLs fetched by the cloud are usually 403 on your PC, so the edge node always re-fetches streams with its own cookies, and only downloads from a strict domain allow-list (no arbitrary URLs — SSRF-safe).

---

## Quick start

### 1. Cloud master (Linux)

```bash
git clone https://github.com/xueluo-xinlan/bili-summary-stream.git
cd bili-summary-stream
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cp config.yaml.example config.yaml   # fill in cookies, sender uid, receivers, profiles, LLM api

.venv/bin/python login.py            # QR login — MUST scan with the SENDER (bot) account

sudo cp deploy/linux/bili-summary.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now bili-summary
```

### 2. Edge GPU node (Windows, optional but recommended)

```powershell
.\deploy\windows\install_edge_worker_task.ps1   # autostart silently at logon
.\deploy\windows\edge_status.ps1                # health check
```

Then point the cloud config at it:

```yaml
hybrid:
  laptop_worker_url: http://192.168.1.50:18088
  probe_timeout: 0.8
```

Without an edge node everything still works — via cloud multimodal extraction (slower, costs API calls).

### 3. DM command cheat-sheet

| Intent | Example | Response |
|---|---|---|
| Show config | `查看设置` / `帮助` | Current keywords, creators, style |
| Replace keywords | `设置关键词 AI, Agent` | Overwrites the list |
| Add / remove a keyword | `添加关键词 强化学习` / `删除关键词 深度学习` | Appends / removes one |
| Set distillation style | `设置风格 重点关注方法论与代码` | Used for later pushes |
| Track / untrack a creator | `监控UP 123456` / `取消监控UP 123456` | Edits the watch list |
| Summarize now | send `BV1xxxxxxxxx` or share a video | Returns a multi-part summary |
| Ad-hoc requirement | `BV1xxxxxxxxx 重点看续航和散热` | Folds into this summary |

---

## Layout

```
bili-summary-stream/
├── main.py              # watch / listen / scan-profiles / summarize / history
├── login.py             # QR login → config.yaml
├── config.yaml.example  # fully commented template
├── src/                 # pipeline, bili_api, bot_listener, filter, summarizer,
│                        # notifier, cloud_mm, transcriber_client, edge_worker,
│                        # transcriber, worker, audio, storage, config_loader
├── tools/               # self-test, health report, push review
├── deploy/linux/        # systemd units
├── deploy/windows/      # edge-node scheduled tasks & helpers
└── docs/                # ARCHITECTURE.md, DEPLOYMENT.md
```

---

## Design notes

- **Resident-light, offload-heavy**: Whisper runs in a *subprocess* so GB-scale memory returns to the OS on exit; the daemon stays around 80–90MB.
- **Subtitle channel disabled by default**: measured — the subtitle API returned content belonging to *other* videos ~75% of the time across repeated queries for the same `bvid+cid`. Falling back to real ASR is safer than trusting it.
- **Anti-rationalisation guard**: when transcript and topic mismatch, LLMs tend to "explain away" the mismatch with a plausible-sounding summary. The guard detects and flags it instead of letting it through.
- **Conservative rate limits** to reduce account risk (`max_push_per_keyword`, `max_chunk_size`, `send_interval`).

---

## Make it stronger: add the laya decision model locally

The in-app interaction panel is currently fairly minimal. If you have the resources, consider adding [laya](https://github.com/NandhaKishorM/laya) — a small decision model **【run it locally】** — which improves things a lot:

laya is a non-autoregressive decision engine: a **single forward pass** yields yes/no, a score, and typed choices. That maps neatly onto this project's relevance gate and push decisions (should this be pushed / to which account / under which keyword), cutting LLM calls and enabling snappier decisions in the interaction panel.

Apache-2.0 licensed, small enough to keep resident — consistent with this project's "resident-light" philosophy.

---

## License

[MIT](LICENSE) © 2026 xueluo-xinlan

> For personal, educational use. Respect Bilibili's terms of service and applicable law; do not use for bulk scraping or spam.
