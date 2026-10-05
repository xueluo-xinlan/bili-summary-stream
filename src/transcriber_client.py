# -*- coding: utf-8 -*-
"""内容获取客户端 —— 五级降级，只为拿到**真实文本**。

    L1  本机 RTX 5060 转写   —— 最高质量、零成本（需 PC 开机）
    L2  B站 AI/人工字幕       —— 免费，已内置重试与多条目回退（见 bili_api）
    L3  云端音频转写          —— 7×24 可用（Gemini 原生音频输入）
    L4  云端视觉描述          —— **无人声视频的唯一出路**（抽帧看画面）
    L5  无真实内容            —— 返回 None，由调用方排队待补推

铁律
----
**绝不再用「标题 + 简介」编造摘要。** 此前 L5 缺失时，系统会拿元数据生成看似
完整、实则空洞的"空壳摘要"发给用户——那比没有内容更糟，因为看不出是假的。
拿不到真实文本时，宁可返回 None 让上层排队补推。
"""

import time
from typing import Any, Dict, Optional

import requests

# 至少要有这么多字才算"真的拿到了内容"。低于此值的层视为无产出，继续降级。
# 起因：实测一支视频 GPU 只转出 9 个字符，旧逻辑却拿它生成了摘要并推送。
MIN_CONTENT_CHARS = 50

# L1（本机 GPU 转写）的等待上限，秒。
#
# ⚠️ 这里曾经硬编码 180，是**丢结果**的元凶：
#   worker 侧子进程超时是 3600s，笔记本本身完全算得完；而 180 秒只够约 17 分钟的
#   视频（扣掉模型加载等固定开销后 ≈1050s 音频），**但 filter.max_duration 允许 2400s**。
#   实测三支长视频全部撞线，其中 BV1JRY76NECN（1235s）worker 在客户端断开后
#   **仅 5 秒**就出结果（7539 字 / 194.7s）——结果被丢弃，再回落云端 ASR 多花约 214s。
#   合计白等 ≈400s，而本地 GPU 本来就能交付。
#
# 取值依据（实测速率 ≈7.5x 实时，RTX 5060 Laptop + large-v3-turbo float16）：
#   timeout = 固定开销(≈40s) + 音频时长 / 7.5
#   覆盖 max_duration=2400s 需要 ≈ 40 + 320 = 360s，取 600 留足余量。
# 与笔记本侧 edge_worker.py 的 TRANSCRIBE_LOCK_TIMEOUT 是**耦合**的，改这里要同步改那边。
LAPTOP_TRANSCRIBE_TIMEOUT = 600

# 内容来源的中文标注——推送时告知用户这份总结建立在什么之上
SOURCE_LABELS: Dict[str, str] = {
    "laptop_rtx_5060": "本机GPU转写",
    "official_subtitle": "B站AI字幕",
    "cloud_asr": "云端ASR",
    "cloud_vision": "云端视觉描述",
}


def source_label(source: str) -> str:
    """把内部 source 标识转成给人看的中文标签。"""
    return SOURCE_LABELS.get(source or "", source or "未知来源")


class HybridTranscriberClient:
    def __init__(
        self,
        bili_client,
        laptop_worker_url: str = "http://192.168.1.50:18088",
        probe_timeout: float = 0.8,
        cloud_mm=None,
        enable_cloud: bool = True,
        enable_subtitles: bool = True,
    ):
        self.bili_client = bili_client
        self.laptop_worker_url = laptop_worker_url.rstrip("/")
        self.probe_timeout = probe_timeout
        self.cloud_mm = cloud_mm
        self.enable_cloud = bool(enable_cloud) and cloud_mm is not None
        # 字幕通道默认启用，但**必须过串台校验**——实测该接口会返回其它视频的字幕
        self.enable_subtitles = bool(enable_subtitles)
        # 记录本轮降级过程，便于日志与排查
        self.trace: list = []

    # ---------------------------------------------------------------- 探测
    def is_laptop_online(self) -> bool:
        """0.8 秒极速心跳探测笔记本 RTX 5060 算力 Worker。"""
        try:
            r = requests.get(f"{self.laptop_worker_url}/health", timeout=self.probe_timeout)
            if r.status_code == 200:
                return r.json().get("status") == "ok"
        except Exception:
            pass
        return False

    # ---------------------------------------------------------------- L1
    def _try_laptop(self, bvid: str, cid: int, audio_url: str) -> Optional[Dict[str, Any]]:
        if not self.is_laptop_online():
            self.trace.append("L1 笔记本离线")
            return None
        print("[★ 算力路由] 检测到笔记本 RTX 5060 在线！正在调度本地 GPU 加速转写...")
        try:
            resp = requests.post(
                f"{self.laptop_worker_url}/transcribe",
                json={"bvid": bvid, "cid": cid, "audio_url": audio_url},
                timeout=LAPTOP_TRANSCRIBE_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok":
                    res = data.get("result", {})
                    res["source"] = "laptop_rtx_5060"
                    print(f"[✓ 算力路由] 本地 RTX 5060 转写完成！字符数: {len(res.get('full_text', ''))}")
                    self.trace.append("L1 成功")
                    return res
                # 在线且正常应答但没给出文本（无人声/正忙）——不是故障
                _reject = str(data.get("status") or "unknown")
                _why = str(data.get("message") or "").strip()[:120]
                print(f"[i] 边缘节点在线但未产出文本（{_reject}）：{_why}")
                self.trace.append(f"L1 无文本({_reject})")
            else:
                print(f"[!] 边缘节点响应异常: HTTP {resp.status_code} {resp.text[:100]}")
                self.trace.append(f"L1 HTTP {resp.status_code}")
        except Exception as e:
            print(f"[!] 边缘节点通信异常: {e}")
            self.trace.append(f"L1 异常 {type(e).__name__}")
        return None

    # ---------------------------------------------------------------- L2
    def _try_subtitles(self, bvid: str, cid: int,
                       video_info: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not self.bili_client or not self.enable_subtitles:
            self.trace.append("L2 已停用")
            return None
        info = video_info or {}
        title = str(info.get("title") or "")
        owner = str(info.get("owner_name") or "")
        duration = float(info.get("duration") or 0)
        try:
            subs = self.bili_client.get_official_subtitles(
                bvid, cid, expected_duration=duration)
        except Exception as e:
            print(f"[!] 字幕通道异常: {e}")
            self.trace.append(f"L2 异常 {type(e).__name__}")
            return None
        if not (subs and subs.get("full_text")):
            self.trace.append("L2 无字幕")
            return None

        text = subs["full_text"]

        # —— 规则：音乐类视频 + 歌词形态字幕 → 本就是合法内容，直接放行 ——
        # 判据来自 B站分区（tname/tid）与字幕形态（♪ 密度/句长）。
        # 这样既不会把 MMD/歌曲的歌词误判为串台，也不会让普通讲解视频
        # 混进歌词——后者恰恰是串台的典型特征。
        try:
            music_like = self.bili_client.is_music_like(info)
            lyrics_like = self.bili_client.looks_like_lyrics(text)
        except Exception:
            music_like = lyrics_like = False
        if music_like and lyrics_like:
            print(f"[✓ 算力路由] 音乐类视频（{info.get('tname') or '标题判定'}）"
                  f"的歌词字幕，放行，字符数: {len(text)}")
            self.trace.append("L2 成功(歌词)")
            return subs

        # —— 其余情况必须过相关性闸门 ——
        # 实测 x/player/v2 会返回**其它视频**的字幕（5 次里 4 次无关），
        # 不加闸门就会产出"对错误视频的自信总结"——比空壳更危险。
        if self.cloud_mm is not None:
            if not self.cloud_mm.verify_relevance(title, owner, text):
                print(f"[!] 字幕与视频不符（疑似串台），弃用该字幕 ({bvid})"
                      f"［音乐类={music_like} 歌词形态={lyrics_like}］")
                self.trace.append("L2 串台被弃")
                return None

        print(f"[✓ 算力路由] 取得 B 站字幕并通过校验，字符数: {len(text)}")
        self.trace.append("L2 成功")
        return subs

    # ---------------------------------------------------------------- L3
    def _try_cloud_audio(self, bvid: str, cid: int, duration: float,
                         audio_url: str) -> Optional[Dict[str, Any]]:
        if not self.enable_cloud:
            return None
        if not audio_url:
            try:
                audio_url = self.bili_client.get_audio_stream_url(bvid, cid)
            except Exception:
                self.trace.append("L3 取流失败")
                return None
        print("[☁️ 算力路由] 调用云端音频转写 ...")
        res = self.cloud_mm.transcribe_audio(audio_url, bvid, duration)
        if res and res.get("full_text"):
            print(f"[✓ 云端 ASR] 转写完成，字符数: {len(res['full_text'])}")
            self.trace.append("L3 成功")
            return res
        print("[i] 云端音频转写未产出文本（无语音或失败）")
        self.trace.append("L3 无文本")
        return None

    # ---------------------------------------------------------------- L4
    def _try_cloud_vision(self, bvid: str, cid: int, duration: float) -> Optional[Dict[str, Any]]:
        if not self.enable_cloud:
            return None
        try:
            video_url = self.bili_client.get_video_stream_url(bvid, cid)
        except Exception as e:
            print(f"[!] 视频流获取失败，跳过视觉通道: {str(e)[:80]}")
            self.trace.append("L4 取流失败")
            return None
        print("[☁️ 算力路由] 调用云端视觉描述（抽帧看画面）...")
        res = self.cloud_mm.describe_video(video_url, bvid, duration)
        if res and res.get("full_text"):
            print(f"[✓ 云端视觉] 描述完成，字符数: {len(res['full_text'])}")
            self.trace.append("L4 成功")
            return res
        print("[i] 云端视觉描述未产出")
        self.trace.append("L4 无产出")
        return None

    # ---------------------------------------------------------------- 主流程
    def transcribe(
        self,
        bvid: str,
        cid: int,
        audio_url: str = "",
        video_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """五级降级取真实文本。

        返回 ``{'full_text', 'timeline_text', 'duration', 'source'}``；
        **拿不到真实内容时返回 None**（由调用方排队补推），绝不编造。
        """
        self.trace = []
        duration = float((video_info or {}).get("duration") or 0)
        t0 = time.time()

        for label, fn in (
            ("L1 本机GPU", lambda: self._try_laptop(bvid, cid, audio_url)),
            ("L2 B站字幕", lambda: self._try_subtitles(bvid, cid, video_info)),
            ("L3 云端ASR", lambda: self._try_cloud_audio(bvid, cid, duration, audio_url)),
            ("L4 云端视觉", lambda: self._try_cloud_vision(bvid, cid, duration)),
        ):
            try:
                res = fn()
            except Exception as e:
                print(f"[!] {label} 异常: {e}")
                self.trace.append(f"{label} 异常")
                continue
            if res and res.get("full_text"):
                n_chars = len((res.get("full_text") or "").strip())
                if n_chars < MIN_CONTENT_CHARS:
                    # 太短说明这一层并没有真正拿到内容。实测过一支视频 GPU 只转出
                    # **9 个字符**，旧逻辑却照它生成了一份看着像样的摘要推了出去——
                    # 那是假货。宁可继续往下一层（视觉通道往往能救），也不要拿它充数。
                    print(f"[i] {label} 只产出 {n_chars} 字（阈值 {MIN_CONTENT_CHARS}），"
                          f"视为无有效内容，继续降级")
                    self.trace.append(f"{label} 内容过短")
                    continue
                print(f"[✓ 内容获取] 来源={source_label(res.get('source'))} "
                      f"字符={n_chars} 总耗时={time.time()-t0:.1f}s")
                self._cleanup_temp(bvid)
                return res

        print(f"[✗ 内容获取] 四级均未取得真实文本（{' → '.join(self.trace)}）"
              f"，不生成摘要，转入待补推")
        self._cleanup_temp(bvid)
        return None

    def _cleanup_temp(self, bvid: str) -> None:
        """清理本次云端处理留下的临时音频/抽帧文件。

        `CloudMMExtractor.cleanup()` 早就写好了，但**全项目从来没有人调用过它**——
        cache/cloud_mm 一直在积压（审计时发现两个残留文件共 9.4MB，单个约 4.7MB）。
        取到内容之后（无论成功失败）都要清。
        """
        try:
            if self.cloud_mm is not None:
                self.cloud_mm.cleanup(bvid)
        except Exception as e:
            print(f"[i] 临时文件清理跳过: {str(e)[:60]}")

    # 兼容旧调用：取不到内容时返回空串而非编造
    def transcribe_or_none(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return self.transcribe(*args, **kwargs)
