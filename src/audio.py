import subprocess
from pathlib import Path
import requests

class AudioExtractor:
    def __init__(self, cache_dir: str = "cache/audio"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def download_and_convert(self, audio_url: str, bvid: str, ffmpeg_path: str = "ffmpeg") -> Path:
        raw_audio_path = self.cache_dir / f"{bvid}_raw.m4s"
        wav_path = self.cache_dir / f"{bvid}.wav"

        if wav_path.exists() and wav_path.stat().st_size > 0:
            return wav_path

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com"
        }
        resp = requests.get(audio_url, headers=headers, stream=True, timeout=30)
        resp.raise_for_status()

        with open(raw_audio_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)

        cmd = [
            ffmpeg_path,
            "-y",
            "-i", str(raw_audio_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(wav_path)
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode('utf-8', errors='ignore')
            raise RuntimeError(f"FFmpeg 转码失败: {err_msg}")
        finally:
            if raw_audio_path.exists():
                try:
                    raw_audio_path.unlink()
                except Exception:
                    pass

        return wav_path

    def cleanup(self, bvid: str):
        wav_path = self.cache_dir / f"{bvid}.wav"
        if wav_path.exists():
            try:
                wav_path.unlink()
            except Exception:
                pass
