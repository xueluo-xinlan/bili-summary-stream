# -*- coding: utf-8 -*-
"""云端多模态内容提取 —— 音频转写 + 视频抽帧视觉描述。

背景
----
PC 关机时，云端原本拿不到任何真实内容，只能用「标题 + 简介」编造摘要（"空壳"）。
本模块用**现有模型通道**（cliproxy → Gemini，原生支持音频与图像输入）在云端完成
内容理解，使 7×24 推送也能建立在真实内容之上，无需新增服务商。

两条通道
--------
``transcribe_audio()``  音频 → 逐字转写。实测 210s 音频仅 6.3s（本地 RTX 5060 需 29.7s），
                        且准确度相当或更优。
``describe_video()``    视频抽帧 → 视觉描述。**这是唯一能处理"无人声"视频的手段**——
                        MMD / 纯音乐 / 纯画面类视频经 ASR 必然为空，唯有看画面才有真内容。

设计约束
--------
1. **失败一律返回 None**，绝不把异常抛进巡检主流程。
2. 音频超长时自动分片，避免单次请求体积超限。
3. 临时文件用完即删，不占用磁盘。
"""

import base64
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_AUDIO_PROMPT = (
    "请把这段音频里的中文内容逐字转写出来。"
    "只输出转写文本本身，不要任何解释、标题、标点整理或翻译。"
    "如果音频中没有人声（纯音乐/纯音效），只输出四个字：[无语音]"
)

_VISION_PROMPT = (
    "以下 {n} 张图片按时间顺序取自同一个 B站视频（视频中无语音，只有音乐与画面）。\n"
    "请直接输出以下三段内容，不要任何开场白：\n"
    "① 视频主题与整体内容\n"
    "② 画面风格与表现手法（色调、光影、构图、镜头编排）\n"
    "③ 具体可见元素：角色模型与外形特征、服装配饰、场景布置、动作、特效、画面中的文字\n"
    "要求：尽可能具体，写出实际看到的细节，不要泛泛而谈。"
)

_NEGATIVE_MARKERS = ("[无语音]", "[無語音]", "无语音", "沒有語音", "没有语音")


class CloudMMExtractor:
    """通过 OpenAI 兼容接口调用多模态模型，在云端提取视频真实内容。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8317/v1",
        api_key: str = "EMPTY",
        model: str = "gemini-3.8-flash-high",
        work_dir: str = "cache/cloud_mm",
        timeout: int = 300,
        vision_frames: int = 6,
        audio_bitrate: str = "32k",
        max_upload_mb: float = 8.0,
        referer: str = "https://www.bilibili.com",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.vision_frames = max(3, int(vision_frames))
        self.audio_bitrate = audio_bitrate
        self.max_upload_mb = max_upload_mb
        self.referer = referer
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self.last_error: str = ""

    # ------------------------------------------------------------------ 基础调用
    def _chat(self, content: List[Dict[str, Any]], max_tokens: int = 4000,
              temperature: float = 0.1) -> Optional[str]:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            resp = self._session.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning("云端多模态调用失败 %s", self.last_error)
                return None
            return (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning("云端多模态调用异常 %s", self.last_error)
            return None

    def _ffmpeg(self, args: List[str], timeout: int = 420) -> bool:
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-headers", f"Referer: {self.referer}\r\n",
               "-user_agent", "Mozilla/5.0", *args]
        try:
            subprocess.run(cmd, check=True, timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            return True
        except subprocess.CalledProcessError as e:
            self.last_error = f"ffmpeg 失败: {e.stderr.decode('utf-8', 'replace')[:200]}"
            logger.warning("%s", self.last_error)
            return False
        except Exception as e:
            self.last_error = f"ffmpeg 异常: {e}"
            logger.warning("%s", self.last_error)
            return False

    # ------------------------------------------------------------------ 通道一：音频转写
    def _download_audio(self, audio_url: str, bvid: str) -> Optional[Path]:
        out = self.work_dir / f"{bvid}_cloud.mp3"
        if out.exists() and out.stat().st_size > 0:
            return out
        ok = self._ffmpeg([
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", audio_url, "-vn", "-ac", "1", "-ar", "16000",
            "-b:a", self.audio_bitrate, str(out),
        ])
        return out if (ok and out.exists() and out.stat().st_size > 0) else None

    def transcribe_audio(self, audio_url: str, bvid: str,
                         duration: float = 0.0) -> Optional[Dict[str, Any]]:
        """音频 → 逐字转写。无人声时返回 None（交由视觉通道接管）。"""
        if not audio_url:
            return None
        t0 = time.time()
        mp3 = self._download_audio(audio_url, bvid)
        if mp3 is None:
            return None

        size_mb = mp3.stat().st_size / 1024 / 1024
        pieces: List[Path] = [mp3]
        if size_mb > self.max_upload_mb:
            # 超限则按时长切片，避免单请求体积过大
            n = int(size_mb / self.max_upload_mb) + 1
            dur = duration or 600.0
            seg_len = max(60.0, dur / n)
            pieces = []
            for i in range(n):
                seg = self.work_dir / f"{bvid}_seg{i}.mp3"
                if self._ffmpeg(["-i", str(mp3), "-ss", str(i * seg_len),
                                 "-t", str(seg_len), "-c", "copy", str(seg)]):
                    pieces.append(seg)
            if not pieces:
                pieces = [mp3]
            logger.info("音频 %.1fMB 超限，切为 %d 片", size_mb, len(pieces))

        chunks: List[str] = []
        try:
            for p in pieces:
                if p.stat().st_size == 0:
                    continue
                b64 = base64.b64encode(p.read_bytes()).decode()
                txt = self._chat([
                    {"type": "text", "text": _AUDIO_PROMPT},
                    {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}},
                ], max_tokens=8000)
                if txt is None:
                    return None
                chunks.append(txt)
        finally:
            for p in pieces:
                if p is not mp3:
                    p.unlink(missing_ok=True)

        full = "\n".join(c for c in chunks if c).strip()
        if not full or any(m in full for m in _NEGATIVE_MARKERS):
            logger.info("云端转写判定为无语音 (%s)", bvid)
            return None
        if len(full) < 10:                      # 过短基本是噪声或拒答
            return None

        logger.info("云端音频转写完成 %s | %d 字 | %.1fs", bvid, len(full), time.time() - t0)
        return {
            "source": "cloud_asr",
            "duration": duration,
            "full_text": full,
            "timeline_text": full,
        }

    # ------------------------------------------------------------------ 通道二：视觉描述
    def _extract_frames(self, video_url: str, bvid: str, duration: float) -> List[Path]:
        """顺序拉流抽帧——不做远程 seek（CDN 常提前断流，seek 会失败）。"""
        for old in self.work_dir.glob(f"{bvid}_f*.jpg"):
            old.unlink(missing_ok=True)
        interval = max(8.0, (duration or 120.0) / self.vision_frames)
        ok = self._ffmpeg([
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", video_url,
            "-vf", f"fps=1/{interval:.3f},scale=640:-2",
            "-frames:v", str(self.vision_frames), "-q:v", "4",
            str(self.work_dir / f"{bvid}_f%02d.jpg"),
        ], timeout=600)
        frames = sorted(self.work_dir.glob(f"{bvid}_f*.jpg"))
        if not frames:
            self.last_error = self.last_error or "抽帧为空"
        return frames if ok or frames else []

    def describe_video(self, video_url: str, bvid: str,
                       duration: float = 0.0) -> Optional[Dict[str, Any]]:
        """视频抽帧 → 视觉描述。无人声视频的唯一出路。"""
        if not video_url:
            return None
        t0 = time.time()
        frames = self._extract_frames(video_url, bvid, duration)
        if not frames:
            return None
        try:
            content: List[Dict[str, Any]] = [
                {"type": "text", "text": _VISION_PROMPT.format(n=len(frames))}]
            for f in frames:
                b64 = base64.b64encode(f.read_bytes()).decode()
                content.append({"type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            txt = self._chat(content, max_tokens=4000, temperature=0.2)
        finally:
            for f in frames:
                f.unlink(missing_ok=True)

        if not txt or len(txt) < 40:
            return None
        logger.info("云端视觉描述完成 %s | %d 帧 | %d 字 | %.1fs",
                    bvid, len(frames), len(txt), time.time() - t0)
        return {
            "source": "cloud_vision",
            "duration": duration,
            "full_text": txt,
            "timeline_text": txt,
        }

    # ------------------------------------------------------------------ 校验
    def verify_relevance(self, title: str, owner: str, text: str) -> bool:
        """判定一段文本是否真的属于该视频。

        必要性：实测 B站 `x/player/v2` 的字幕接口会**返回其它视频的字幕**
        （同一 (bvid,cid) 连续 5 次查询，4 次是无关内容）。若不加校验就送给
        摘要器，会产出"对错误视频的自信总结"——比空壳更危险，因为看不出是假的。

        判定失败时**一律返回 False**（宁可退回转写通道，也不喂错内容）。
        """
        if not text or len(text) < 20:
            return False
        probe = text[:1500]
        # ⚠️ 必须问「主题是否一致」，不能问「是否确实出自该视频」。
        #    后者是**出处问题**：只给一段文本、没有别的证据，模型无法核实来源，
        #    于是一律答「否」——闸门会变成一台把**正确字幕也毙掉**的机器。
        #    实测：出处问法下匹配样本被判否；改成主题问法后 匹配→是 / 错配→否。
        prompt = (
            f"下面是一段 B站视频的转写/字幕文本。请判断："
            f"**这段文本的内容主题**与**标题《{title}》"
            f"（UP主「{owner}」）所指的主题**是否一致。\n\n"
            f"注意：\n"
            f"· 只要属于同一话题或同一内容领域就算一致，不需要逐句吻合，"
            f"也不需要文本提到标题里的每个词。\n"
            f"· 若文本讲的是**完全不同的领域**（例如标题讲游戏卡组、文本却讲美食制作），"
            f"则判否。\n\n"
            f"只回答一个字：是 或 否。不要解释。\n\n"
            f"【文本】\n{probe}"
        )
        # ⚠️ max_tokens 必须给足。推理型模型会把预算耗在思考上，给 8 个 token 会返回
        #    **空字符串**；而空串不是 None，会落到 `"".startswith("是") == False`，
        #    被判成"不相关"——闸门于是变成一台「全部拒收」的机器，把**正确的字幕也毙掉**。
        #    （实测金丝雀：3 条正确配对全部被误拒。）空响应必须显式区分于「判否」。
        for budget in (64, 256):
            out = self._chat([{"type": "text", "text": prompt}],
                             max_tokens=budget, temperature=0.0)
            if out and out.strip():
                head = out.strip()[:4]
                ok = head.startswith("是") or head.lower().startswith("yes")
                logger.info("字幕相关性校验: %s | 标题=《%s》",
                            "通过" if ok else "不通过", title[:30])
                return ok
            logger.warning("相关性校验返回空响应（max_tokens=%d），提高预算重试", budget)

        logger.error("相关性校验连续空响应 → 按不通过处理（保守）。"
                     "注意：这是**未判定**，不是判否；持续出现说明 token 预算或上游异常。")
        return False

    # ------------------------------------------------------------------ 清理
    def cleanup(self, bvid: str) -> None:
        for p in self.work_dir.glob(f"{bvid}*"):
            p.unlink(missing_ok=True)

    def cleanup_stale(self, max_age_hours: float = 24.0) -> int:
        """兜底清扫：删掉 work_dir 里超过指定时长的临时文件。

        正常路径由 transcriber 取到内容后调用 ``cleanup(bvid)`` 清理；
        但进程被 kill / 崩溃时会漏掉，所以再加一道**按时间**的清扫。
        返回删除的文件数。
        """
        cutoff = time.time() - float(max_age_hours) * 3600.0
        removed = 0
        try:
            for p in self.work_dir.glob("*"):
                try:
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink(missing_ok=True)
                        removed += 1
                except Exception:
                    continue
        except Exception:
            pass
        return removed
