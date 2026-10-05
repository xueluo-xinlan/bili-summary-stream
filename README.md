# BiliSummaryStream · B 站视频深度总结与多账号个性化推送流

[English](README.en.md) · **简体中文**

基于 **本地 GPU 加速转写（faster-whisper）** 与 **大语言模型深度提炼** 的 B 站自动化「总结 → 私信推送」系统。

云端主控 7×24 小时巡检并负责提炼与推送；PC 显卡在线时自动接管音频转写，PC 关机则**秒级无缝降级**到云端多模态提取（音频 ASR + 无人声视频抽帧视觉描述）。全程只推**真实内容**，取不到内容时宁可老实排队，也不用标题简介编造空壳摘要。

---

## 它解决什么问题

B 站信息密度极低：一期 40 分钟的视频，干货可能只有 3 分钟。逐个看太贵，只看标题又会被标题党坑。

本项目把「看视频」变成「读推送」：

- 你关心哪些关键词、哪些 UP 主、想用什么风格提炼 —— **在 B 站私信里直接说人话就能改**；
- 刷到好视频，**点分享 → 私信发给小号**，几分钟后收到该视频的深度总结；
- 视频评论区 **@ 小号** 同样能触发总结；
- 多个账号各有各的画像（独立关键词、独立 UP 名单、独立提炼风格），互不干扰。

---

## 核心特性

| # | 特性 | 说明 |
|---|---|---|
| 1 | **多账号独立画像** | 每个账号一套独立关键词 / UP 名单 / 提炼风格，独立检索、独立提炼、独立推送 |
| 2 | **私信指令实时改配置** | 在私信里发 `设置关键词 …`、`设置风格 …`、`监控UP 123456`，机器人热重载并即刻确认 |
| 3 | **分享即总结** | 私信分享视频卡片（或直接发 BV 号 / 链接），自动转写提炼并单独回发该账号 |
| 4 | **评论区 @ 触发** | 评论区 `@发信小号` 即被捕获，总结后私信回给发起者 |
| 5 | **云边协同算力路由** | 探活 0.8s：PC 在线用 RTX 显卡转写（更快更省），离线自动走云端多模态，**不中断服务** |
| 6 | **相关性闸门 + 诚实降级** | 抓不到真实内容时，要么排队待补推、要么发一条「待补推」的诚实通知，**绝不编造** |

---

## 系统架构

```
┌──────────────────────────────────┐            ┌──────────────────────────────────┐
│  云端主控（Linux / systemd）       │            │  边缘 GPU 节点（Windows，可选）    │
│  bili-summary.service            │   HTTP     │  edge_worker  (:18088)           │
│                                  │  :18088    │                                  │
│  · 每 30 分钟多账号独立巡检        │ ─────────► │  · faster-whisper large-v3-turbo │
│  · 私信 / @ 实时监听（轮询 6s）    │ ◄───────── │  · 本机 cookie 取流（绕开签名）    │
│  · LLM 提炼 + 多段分片推送        │            │  · 单卡串行排队，重活子进程隔离    │
│  · 云端多模态兜底（ASR + 视觉）    │            │  · 常驻内存 ~80MB                │
└──────────────────────────────────┘            └──────────────────────────────────┘
        │
        ├── 巡检：关键词检索 / UP 主上新 → 相关性闸门 → 转写 → 提炼 → 分片私信推送
        ├── 交互：私信指令热重载配置 / 分享视频即时总结 / 评论区 @ 触发
        └── 存储：SQLite 去重（history.db）+ 总结归档（summaries/）
```

**降级链（设计要点）**

```
本地 GPU 转写（在线） ──离线──► 云端音频 ASR ──无人声──► 云端视频抽帧视觉描述 ──都失败──► 待补推队列
```

- B 站音频 URL 带 IP 绑定签名，云端取来的链接在本机下载常被 403，故边缘节点**先用本机 cookie 重新取流**；
- 边缘节点把 Whisper 跑在**子进程**里，GB 级内存随进程退出立即归还（常驻保持 ~80MB）；
- 云端「字幕通道」默认关闭：实测字幕接口返回的正文常与请求视频不匹配，宁可走 ASR 也不吃来路不明的内容。

---

## 快速开始

### 0. 准备

| 组件 | 必要性 | 说明 |
|---|---|---|
| Linux 服务器 | **必需** | 云端主控；1 核 1G 即可运行（内存占用约 90MB） |
| Python 3.10+ | 必需 | |
| LLM API | 必需 | 任意 OpenAI 兼容接口（本地 CLIProxyAPI / 云端 API 均可） |
| Windows PC + NVIDIA GPU | 可选 | 边缘转写加速；没有则全程走云端多模态 |
| FFmpeg | 可选 | 音频转码 / 预处理 |

### 1. 云端主控

```bash
git clone https://github.com/xueluo-xinlan/bili-summary-stream.git
cd bili-summary-stream

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp config.yaml.example config.yaml   # 然后按注释填好凭据与账号
```

登录（扫码，**必须用「发信小号」扫**，否则发信方身份会串号）：

```bash
.venv/bin/python login.py            # 终端直出二维码；无图形界面的服务器请看 cache/bili_login_qr.png
```

装成 systemd 常驻服务（开机自启、崩溃自动拉起）：

```bash
sudo cp deploy/linux/bili-summary.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bili-summary
journalctl -u bili-summary -f        # 看实时日志
```

### 2. 边缘 GPU 节点（Windows，可选但强烈建议）

```powershell
# 在 PC 上把仓库 clone 下来，装好依赖后：
.\deploy\windows\install_edge_worker_task.ps1   # 注册「登录即静默启动」计划任务
.\deploy\windows\edge_status.ps1                # 查看边缘节点健康状态
```

边缘节点对云端只暴露一个 HTTP 接口 `:18088`（`/health` 与 `/transcribe`），域名白名单 + 本机取流，不下载任意 URL。

### 3. 日常使用

云端无需人工干预：每 30 分钟巡检一次，期间私信与 @ 实时响应。

```bash
.venv/bin/python main.py watch          # 前台跑守护（调试用；正式环境交给 systemd）
.venv/bin/python main.py listen         # 只跑实时监听
.venv/bin/python main.py scan-profiles  # 立即执行一次巡检
.venv/bin/python main.py summarize --bvid BV1xxxxxxxxx
```

---

## B 站私信交互指令速查

在私信对话框里直接发（对「发信小号」）：

| 意图 | 发送示例 | 响应 |
|---|---|---|
| 查看当前配置 | `查看设置` / `我的配置` / `帮助` | 回复该账号的关键词、监控 UP 与提炼偏好面板 |
| 覆盖关键词 | `设置关键词 AI大模型, 具身智能, Agent架构` | 覆盖更新专属关键词 |
| 增加 / 删除关键词 | `添加关键词 强化学习` / `删除关键词 深度学习` | 追加 / 移除单个词 |
| 修改提炼风格 | `设置风格 重点关注论文方法论与代码落地` | 后续推送按此偏好提炼 |
| 监控 / 取关 UP 主 | `监控UP 123456` / `取消监控UP 123456` | 增删巡检名单 |
| 即时总结 | 直接发 `BV1xxxxxxxxx`，或**分享视频卡片** | 单独回发多段深度总结 |
| 带临时要求总结 | `BV1xxxxxxxxx 重点看续航和散热` | 临时重点融入本次总结 |

---

## 目录结构

```
bili-summary-stream/
├── main.py                      # 入口：watch / listen / scan-profiles / summarize / history
├── login.py                     # 扫码登录（写入 config.yaml）
├── config.yaml.example          # 配置模板（注释齐全，复制为 config.yaml 后填值）
├── src/
│   ├── pipeline.py              # 主流水线：检索 → 闸门 → 转写 → 提炼 → 推送
│   ├── bili_api.py              # B 站接口封装（含 cookie / UA / 风控重试）
│   ├── bot_listener.py          # 私信与 @ 实时监听、指令解析
│   ├── filter.py                # 相关性闸门（关键词 + 语义双重筛选）
│   ├── summarizer.py            # LLM 提炼（含防「圆谎式」失配的护栏）
│   ├── notifier.py              # 私信分片推送与限速
│   ├── cloud_mm.py              # 云端多模态提取（音频 ASR / 抽帧视觉描述）
│   ├── transcriber_client.py    # 云边混合转写路由（探活 + 降级）
│   ├── edge_worker.py           # 边缘节点 HTTP 服务（:18088）
│   ├── transcriber.py           # 本地 faster-whisper 转写
│   ├── worker.py                # 边缘侧单次转写子进程入口
│   ├── audio.py                 # 取流与转码
│   ├── storage.py               # SQLite 去重与历史
│   └── config_loader.py         # 配置加载与校验
├── tools/                       # 运维工具（自检 / 体检 / 推送复核）
├── deploy/
│   ├── linux/                   # systemd 单元（主服务 + 每日自检）
│   └── windows/                 # 边缘节点部署脚本（计划任务 / 静默启动 / 健康巡检）
└── docs/
    ├── ARCHITECTURE.md          # 架构与关键设计取舍
    └── DEPLOYMENT.md            # 部署与排障手册
```

---

## 常见问题

**Q：必须要有独显 PC 吗？**
不必。没有边缘节点时全程走云端多模态（音频 ASR + 视觉描述），只是耗时更长、成本更高。

**Q：为什么必须用「发信小号」扫码？**
B 站私信要求发送方与登录账号一致。若用大号扫码，会出现「自己给自己发」的身份错乱。

**Q：取不到内容会推假总结吗？**
不会。抓不到真实正文时进入待补推队列；`pending_notice: true` 时发一条明确标注「待补推」的通知，绝不用标题简介糊弄。

**Q：会被风控吗？**
代码内置请求间隔、UA 与重试策略，并把推送限速（`max_chunk_size` / `send_interval` / `max_push_per_keyword`）做得保守。但请自行控制账号与频率，**使用风险自负**。

---

## 让功能更强：接入 laya 本地判断小模型

B 站里的交互面板目前还比较简略，有条件的话，可加个 [laya](https://github.com/NandhaKishorM/laya) 的判断小模型 **【要本地部署】**，功能会加强很多哦~

laya 是一个非自回归的轻量决策引擎：**单次前向**即可给出「是 / 否」、打分与类型化选择，很适合放在本项目的「相关性闸门」与「推送决策」位置：

- 现在：关键词命中 + LLM 语义筛选 → 决定「这条视频值不值得推」；
- 加上 laya：把这一步变成**本地毫秒级判决**（要不要推 / 推给哪个账号 / 归到哪个关键词），既省 LLM 调用，又能让交互面板做出更细的即时判断（如私信里「这条要不要现在总结」的快速分流）。

Apache-2.0 许可，与本地部署的定位契合；小模型常驻内存低，符合本项目「常驻轻、重活外包」的设计取向。

---

## 许可

[MIT](LICENSE) © 2026 xueluo-xinlan

> 本项目仅供个人学习与自用，请遵守 B 站用户协议与相关法律法规；请勿用于批量爬取、营销骚扰等用途。
