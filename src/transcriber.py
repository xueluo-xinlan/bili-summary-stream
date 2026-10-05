import os
import sys
import gc
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

# Windows 平台下自动将 pip 安装的 nvidia cublas/cudnn 路径注入 DLL 寻址目录
if sys.platform == "win32":
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    for nvidia_bin in site_packages.glob("nvidia/*/bin"):
        if nvidia_bin.is_dir():
            try:
                os.add_dll_directory(str(nvidia_bin))
            except Exception:
                pass
            os.environ["PATH"] = str(nvidia_bin) + os.pathsep + os.environ.get("PATH", "")

# 必须在 import numpy / faster_whisper 之前压线程数：
# 该 numpy 构建带 OpenBLAS，import 时按 CPU 核数预留每线程缓冲，多核机上单独就能提交约 750MB。
# 本服务的重计算全在 CUDA 上，CPU 侧 BLAS 线程无收益，故统一压到 1（每线程缓冲随之消失）。
for _thread_env in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thread_env, "1")

from faster_whisper import WhisperModel

class WhisperTranscriber:
    def __init__(
        self,
        model_size_or_path: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        download_root: str = "cache/models",
        auto_offload: bool = True
    ):
        """
        初始化 Whisper 转写引擎 (支持 RTX 5060 硬件加速与内存/显存按需动态释放)
        """
        if "HF_ENDPOINT" not in os.environ:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        self.model_size_or_path = model_size_or_path
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self.auto_offload = auto_offload
        Path(self.download_root).mkdir(parents=True, exist_ok=True)
        self._model: Optional[WhisperModel] = None

    def _get_model(self) -> WhisperModel:
        if self._model is None:
            print(f"[*] 按需动态加载 Whisper 模型 [{self.model_size_or_path}] 到 {self.device} ({self.compute_type})...")
            t0 = time.time()
            self._model = WhisperModel(
                self.model_size_or_path,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.download_root
            )
            print(f"[+] Whisper 模型加载完成 (耗时: {time.time()-t0:.2f}s)")
        return self._model

    def unload_model(self):
        """立即卸载模型，释放全部显存与内存"""
        if self._model is not None:
            del self._model
            self._model = None
            gc.collect()
            
            # Windows 底层彻底回收 Working Set 内存
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.psapi.EmptyWorkingSet(ctypes.c_size_t(-1))
                except Exception:
                    pass
                    
            print("[♻️ 内存优化] Whisper 模型已成功卸载，显存已清空，Windows 工作集内存已彻底回收！")

    def transcribe(self, audio_path: str, language: str = "zh") -> Dict[str, Any]:
        """
        对音频文件进行高精度转写，任务完成后根据配置自动释放显存
        """
        model = self._get_model()
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )

        full_text_lines = []
        timeline_lines = []

        for seg in segments:
            start_m, start_s = divmod(int(seg.start), 60)
            end_m, end_s = divmod(int(seg.end), 60)
            time_str = f"[{start_m:02d}:{start_s:02d} -> {end_m:02d}:{end_s:02d}]"
            text = seg.text.strip()
            if not text:
                continue

            full_text_lines.append(text)
            timeline_lines.append(f"{time_str} {text}")

        res = {
            "language": info.language,
            "duration": info.duration,
            "full_text": "".join(full_text_lines),
            "timeline_text": "\n".join(timeline_lines)
        }

        # 转写完成后立即卸载模型，零显存驻留
        if self.auto_offload:
            self.unload_model()

        return res
