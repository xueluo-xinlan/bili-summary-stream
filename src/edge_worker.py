# -*- coding: utf-8 -*-
"""BiliSummaryStream 边缘算力节点 —— 云端主控的 GPU 转写服务（HTTP :18088）。

对外契约与云端 `src/transcriber_client.py::HybridTranscriberClient` 严格对齐：

    GET  /health
         -> 200 {"status": "ok", "model": ..., "busy": bool, ...}

    POST /transcribe   {"bvid": "BV...", "cid": 123, "audio_url": "https://..."}
         -> 200 {"status": "ok",    "result": {"language","duration","full_text","timeline_text"}}
         -> 200 {"status": "error", "message": "..."}
         -> 200 {"status": "busy",  "message": "..."}

设计约束：
1. **仅标准库**（http.server / json / subprocess / threading），不给边缘机引入新依赖。
2. **转写走子进程**（worker.py），Whisper 的 GB 级内存随进程退出即归还——
   沿用 2026-09-15 内存改造确立的「常驻轻、重活外包」模式，常驻内存保持 ~80MB。
3. **音频流优先本机取流**：B 站音频 URL 带 IP 绑定签名，云端取来的 URL 在本机
   下载常被 403，故先用本机 cookie 重新取流；调用方传值仅作白名单兜底。
4. **GPU 串行化**：单卡同时只跑一个转写任务，其余请求排队。
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# 固定 stdout/stderr 为 UTF-8：日志经 cmd 包装器落盘，控制台代码页 cp936 会造成中文乱码
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_ROOT = Path(__file__).resolve().parent.parent
os.chdir(BASE_ROOT)
sys.path.insert(0, str(BASE_ROOT))

from src.config_loader import load_config  # noqa: E402
from src.bili_api import BiliClient        # noqa: E402
from src.audio import AudioExtractor       # noqa: E402

HOST = "0.0.0.0"
PORT = 18088
BVID_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")

# 调用方传入的 audio_url 只允许这些域名（阻断 SSRF：绝不下载任意 URL）
ALLOWED_HOST_SUFFIXES = (
    ".bilivideo.com", ".bilivideo.cn", ".bilivideo.net",
    ".bilibili.com", ".hdslb.com", ".akamaized.net",
)
# 单次转写的最长等待（秒）。云端客户端 POST 已放宽至 600s（见 VPS transcriber_client.py 的
# LAPTOP_TRANSCRIBE_TIMEOUT），此处同步放宽并留出余量——两者是耦合的，改一边必须改另一边。
# 旧值 150 是配合旧的 180s 客户端超时；客户端的耐心一放宽，这里不改就会让
# 排队请求在锁上提前失败，白白回落云端。
TRANSCRIBE_LOCK_TIMEOUT = 550
LOG = lambda *a: print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]", *a, flush=True)

# 客户端（云端探测只有 0.8s 超时）提前断开属常态，不应吐整段堆栈污染日志
_CONN_ERRORS = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


def _host_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return any(host == s.lstrip(".") or host.endswith(s) for s in ALLOWED_HOST_SUFFIXES)


class EdgeWorker:
    """GPU 转写节点：状态自持，供多线程 handler 共享。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False
        self._jobs_done = 0
        self._jobs_failed = 0
        self._last_error = ""
        self._started_at = time.time()

        cfg = load_config("config.yaml")
        b = cfg.get("bilibili", {})
        self.cfg = cfg
        self.bili = BiliClient(
            sessdata=b.get("sessdata", ""),
            bili_jct=b.get("bili_jct", ""),
            dedeuserid=str(b.get("sender_uid", "")),
        )
        self.audio = AudioExtractor(
            cache_dir=cfg.get("paths", {}).get("audio_cache", "cache/audio")
        )
        self.whisper_cfg = cfg.get("whisper", {})
        self.model = self.whisper_cfg.get("model", "large-v3-turbo")
        self.worker_py = BASE_ROOT / "src" / "worker.py"
        writable = os.access(BASE_ROOT, os.W_OK)
        LOG(f"边缘节点就绪 | model={self.model} | worker={self.worker_py.name} "
            f"| 项目可写={writable}")

    # ---------- 供 /health 读取的只读快照 ----------
    def snapshot(self):
        return {
            "status": "ok",
            "service": "BiliSummaryStream Edge GPU Worker",
            "gpu": "RTX 5060 Laptop",
            "model": self.model,
            "busy": self._busy,
            "jobs_done": self._jobs_done,
            "jobs_failed": self._jobs_failed,
            "uptime_sec": int(time.time() - self._started_at),
        }

    # ---------- 音频流解析 ----------
    def resolve_audio_url(self, bvid: str, cid: int, provided: str) -> tuple:
        """本机取流优先；失败才回退到调用方传值（且必须过域名白名单）。"""
        try:
            url = self.bili.get_audio_stream_url(bvid, cid)
            if url:
                return url, "local"
        except Exception as e:
            LOG(f"  本机取流失败({bvid}): {type(e).__name__}: {e}")
        if provided and _host_allowed(provided):
            return provided, "remote"
        if provided:
            raise RuntimeError(f"调用方提供的音频地址不在白名单内: {urlparse(provided).hostname}")
        raise RuntimeError("本机取流失败，且调用方未提供可用的音频地址")

    # ---------- 主流程 ----------
    def transcribe(self, bvid: str, cid: int, provided_url: str) -> dict:
        if not self._lock.acquire(timeout=TRANSCRIBE_LOCK_TIMEOUT):
            raise TimeoutError("GPU 正忙，排队超时")
        self._busy = True
        wav_path = None
        try:
            t0 = time.time()
            audio_url, src = self.resolve_audio_url(bvid, cid, provided_url)
            LOG(f"▶ 转写任务 {bvid} (cid={cid}) 音频来源={src}")
            wav_path = self.audio.download_and_convert(audio_url, bvid)
            LOG(f"  音频就绪: {wav_path.name} "
                f"({wav_path.stat().st_size / 1024 / 1024:.1f} MB, 耗时 {time.time()-t0:.1f}s)")

            # 子进程转写：模型内存随进程退出归还，常驻进程保持轻量
            fd, out_json = tempfile.mkstemp(suffix=".json", prefix=f"tr_{bvid}_")
            os.close(fd)
            try:
                cmd = [sys.executable, "-X", "utf8", str(self.worker_py),
                       str(wav_path), out_json]
                env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
                proc = subprocess.run(
                    cmd, cwd=str(BASE_ROOT), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600,
                )
                tail = proc.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
                for line in tail:
                    LOG(f"  [worker] {line}")
                if proc.returncode != 0:
                    raise RuntimeError(f"worker 退出码 {proc.returncode}")

                with open(out_json, "r", encoding="utf-8") as f:
                    res = json.load(f)
            finally:
                try:
                    os.unlink(out_json)
                except OSError:
                    pass

            if not res.get("full_text"):
                # 音乐 / 纯画面视频经 VAD 过滤后为空属正常结果，需与故障区分开以便诊断
                dur = res.get("duration") or 0
                self._jobs_done += 1
                LOG(f"⚠ 转写完成但无人声 {bvid} | 时长 {dur:.0f}s —— "
                    f"VAD 过滤后无语音片段（音乐/纯画面视频）")
                return {
                    "status": "no_speech",
                    "message": f"该视频未检测到语音内容（VAD 过滤后为空，时长 {dur:.0f}s）",
                }

            self._jobs_done += 1
            LOG(f"✓ 转写完成 {bvid} | 字符 {len(res.get('full_text',''))} | "
                f"时长 {res.get('duration', 0):.0f}s | 总耗时 {time.time()-t0:.1f}s")
            return res
        except Exception as e:
            self._jobs_failed += 1
            self._last_error = f"{type(e).__name__}: {e}"
            LOG(f"✗ 转写失败 {bvid}: {self._last_error}")
            raise
        finally:
            self._busy = False
            self._lock.release()
            # 清理临时 wav，避免磁盘无限增长
            if wav_path is not None:
                try:
                    Path(wav_path).unlink(missing_ok=True)
                except Exception:
                    pass


WORKER = EdgeWorker()


class Handler(BaseHTTPRequestHandler):
    server_version = "BiliEdgeWorker/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音默认逐请求访问日志，只留业务日志
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except _CONN_ERRORS:
            pass  # 对端已走，无需回应

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            # /health 必须永远秒回：云端探测超时仅 0.8s，且转写期间不得被判为离线
            self._send(200, WORKER.snapshot())
        else:
            self._send(404, {"status": "error", "message": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/transcribe":
            self._send(404, {"status": "error", "message": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 64 * 1024:
                self._send(400, {"status": "error", "message": "invalid body"})
                return
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self._send(400, {"status": "error", "message": f"bad json: {e}"})
            return

        bvid = str(req.get("bvid") or "").strip()
        if not BVID_RE.match(bvid):
            self._send(200, {"status": "error", "message": f"非法 bvid: {bvid!r}"})
            return
        try:
            cid = int(req.get("cid") or 0)
        except Exception:
            cid = 0
        if cid <= 0:
            self._send(200, {"status": "error", "message": f"非法 cid: {req.get('cid')!r}"})
            return

        try:
            res = WORKER.transcribe(bvid, cid, str(req.get("audio_url") or ""))
            # transcribe 可能直接返回带 status 的终态（如 no_speech），需原样透传
            if isinstance(res, dict) and res.get("status"):
                self._send(200, res)
            else:
                self._send(200, {"status": "ok", "result": res})
        except TimeoutError as e:
            self._send(200, {"status": "busy", "message": str(e)})
        except Exception as e:
            self._send(200, {"status": "error", "message": f"{type(e).__name__}: {e}"})


class EdgeHTTPServer(ThreadingHTTPServer):
    """把客户端提前断开收敛成一行日志；其余异常仍走默认堆栈。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, _CONN_ERRORS):
            host = client_address[0] if isinstance(client_address, tuple) else client_address
            LOG(f"（客户端提前断开，已忽略）{type(exc).__name__} from {host}")
            return
        super().handle_error(request, client_address)


if __name__ == "__main__":
    srv = EdgeHTTPServer((HOST, PORT), Handler)
    LOG(f"BiliSummaryStream 边缘算力节点已监听 {HOST}:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        LOG("边缘节点已停止")
