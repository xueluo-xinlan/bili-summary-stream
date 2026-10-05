# 部署与排障手册

## 一、云端主控（Linux）

### 1.1 依赖

```bash
sudo apt update && sudo apt install -y python3-venv ffmpeg git
git clone https://github.com/xueluo-xinlan/bili-summary-stream.git
cd bili-summary-stream
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 1.2 配置

```bash
cp config.yaml.example config.yaml
```

`config.yaml` 里必须填的项：

| 段 | 键 | 说明 |
|---|---|---|
| `bilibili` | `sessdata` / `bili_jct` | 浏览器 F12 → Application → Cookies 里复制 |
| `bilibili` | `sender_uid` | **发信小号**的 UID（机器人身份，不是接收方） |
| `notification` | `receiver_uids` | 接收推送的账号 UID 列表 |
| `profiles` | `<UID>` | 每个接收账号一套画像：`keywords` / `up_mids` / `requirements` |
| `llm` | `base_url` / `api_key` / `model` | 任意 OpenAI 兼容接口 |
| `hybrid` | `laptop_worker_url` | 边缘节点地址（不部署边缘节点可留空） |

> ⚠️ `config.yaml` 含账号凭据，已被 `.gitignore` 排除，**切勿提交**。

### 1.3 登录（扫码）

```bash
.venv/bin/python login.py
```

- **必须用「发信小号」扫码**：B 站私信要求发送方与登录账号一致，用大号扫会导致身份串号（自己给自己发）。
- 有图形界面的机器直接看终端二维码；无头服务器看 `cache/bili_login_qr.png`（可在本地打开该图片再扫）。
- 中文 Windows 上跑该脚本需设 `PYTHONUTF8=1`，否则终端编码（cp936）装不下二维码字符而崩溃。

### 1.4 装成常驻服务

```bash
sudo cp deploy/linux/bili-summary.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bili-summary
systemctl status bili-summary
journalctl -u bili-summary -f
```

单元文件里 `WorkingDirectory` 与 `ExecStart` 的路径**按你的实际安装路径修改**。

可选：每日自检（检查服务状态、日志错误、巡检进程数是否正常）

```bash
sudo cp deploy/linux/bili-self-test.service deploy/linux/bili-self-test.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now bili-self-test.timer
```

## 二、边缘 GPU 节点（Windows，可选）

### 2.1 依赖

```powershell
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pip install faster-whisper
```

首次转写会自动下载模型（`large-v3-turbo`，约 1.5GB），需可访问模型源。

### 2.2 自检与注册

```powershell
.\.venv\Scripts\python src\edge_worker.py     # 前台跑，看 :18088 是否正常
.\deploy\windows\install_edge_worker_task.ps1  # 注册「登录即静默启动」
.\deploy\windows\edge_status.ps1               # 查看状态
```

### 2.3 与云端对接

云端 `config.yaml`：

```yaml
hybrid:
  laptop_worker_url: http://<边缘机内网地址>:18088
  probe_timeout: 0.8
```

边缘机需允许云端访问 `18088` 端口（防火墙放行 / 组网工具）。

## 三、日常运维

| 目的 | 命令 |
|---|---|
| 立即巡检一次 | `.venv/bin/python main.py scan-profiles` |
| 手动总结单个视频 | `.venv/bin/python main.py summarize --bvid BV1xxxxxxxxx` |
| 查看处理历史 | `.venv/bin/python main.py history` |
| 服务日志 | `journalctl -u bili-summary -f` 或 `tail -f daemon.log` |
| 全面自检 | `.venv/bin/python tools/self_test.py` |
| 体检报告 | `.venv/bin/python tools/health_report.py` |

## 四、常见问题

**Q1：登录后仍提示未登录 / 请求错误**
- 检查 `config.yaml` 的 `sessdata` 是否为最新（重新扫码会刷新）；
- 云端与边缘若各有一份配置，**两边都要更新**（云端负责推送，边缘负责取流）；
- 检查服务器时间是否正确（B 站接口对时间敏感）。

**Q2：视频详情抓取持续失败（个别视频）**
多为视频已删除、设为私密或需要额外权限。系统会将其放入待补推队列反复尝试；若长期失败，属正常现象，不影响其它视频。

**Q3：转写一直走云端、慢**
- 边缘节点是否开机、`/health` 是否可达；
- `hybrid.laptop_worker_url` 是否填对；
- 探活超时默认 0.8s，跨公网可适当放宽。

**Q4：PowerShell 脚本乱码**
`deploy/windows/` 下脚本有 GBK 与 UTF-8 两种编码（原样保留自开发环境）。若你的系统为纯英文环境，用 PS7 或把脚本另存为 UTF-8 with BOM。

**Q5：会不会被封号**
代码内置了保守的请求间隔与限速，但**风险自负**。请勿把 `send_interval`、`max_push_per_keyword` 调得过激。

## 五、备份与升级

```bash
# 只需备份两样：凭据与数据
cp config.yaml ~/bili-config-backup.yaml
cp data/history.db ~/bili-history-backup.db

# 升级
git pull
systemctl restart bili-summary
```

## 六、安全提示

- `config.yaml`、`cache/`、`logs/`、`data/`、`summaries/` 均已在 `.gitignore` 内；
- 私信内容与总结都经 B 站服务器，勿在其中传递敏感信息；
- 边缘节点只对白名单域名取流，且不暴露任何写接口到公网（建议仅内网 / 组网内可达）。
