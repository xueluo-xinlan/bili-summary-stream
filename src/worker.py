import sys
import json
from pathlib import Path

# 固定子进程 stdout/stderr 编码为 UTF-8：父进程按 UTF-8 解析协议，避免控制台代码页（cp936）造成解析歧义
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 添加项目根目录到 sys.path
BASE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_ROOT))

from src.config_loader import load_config
from src.transcriber import WhisperTranscriber

def run_transcribe(audio_path: str, model_name: str = "large-v3-turbo", device: str = "cuda", compute_type: str = "float16"):
    config = load_config("config.yaml")
    whisper_cfg = config.get("whisper", {})

    transcriber = WhisperTranscriber(
        model_size_or_path=model_name or whisper_cfg.get("model", "large-v3-turbo"),
        device=device or whisper_cfg.get("device", "cuda"),
        compute_type=compute_type or whisper_cfg.get("compute_type", "float16"),
        download_root=config.get("paths", {}).get("model_cache", "cache/models"),
        auto_offload=False # 子进程退出即销毁，无需显式 offload
    )
    res = transcriber.transcribe(audio_path)
    return res

def emit_result(res: dict, out_path: str = None):
    """结果输出协议：优先写临时文件（父进程不必把整份 JSON 读进堆里），stdout 仅保留兼容标记。"""
    if out_path:
        tmp = Path(out_path)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        print(f"[+] 转写结果已写入: {tmp} ({tmp.stat().st_size} bytes)")
        return
    print("===TRANSCRIBE_RESULT_START===")
    print(json.dumps(res, ensure_ascii=False))
    print("===TRANSCRIBE_RESULT_END===")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    target_audio = sys.argv[1]
    result_out = sys.argv[2] if len(sys.argv) > 2 else None
    emit_result(run_transcribe(target_audio), result_out)
