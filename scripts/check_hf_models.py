"""检查 ASR / pyannote 模型的可访问性、gated 状态与本地依赖。

用法（在 server/ 目录或用 PYTHONPATH 指向 server）：
    python scripts/check_hf_models.py

ASR 默认使用 FunASR（ModelScope，见 ``OA_FUNASR_*``）；本脚本会打印 FunASR 配置与
依赖是否就绪，并读取 ``server/.env`` 中的 ``OA_HF_TOKEN``，用 HuggingFace API 查询
pyannote（以及可选回退的 faster-whisper）模型是否 gated，便于排查 403 / 模型名错误。
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from app.config import get_settings  # noqa: E402

s = get_settings()
token = s.hf_token

repos = [
    s.diarization_model,
    "pyannote/speaker-diarization-community-1",
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    s.embedding_model,
    "pyannote/embedding",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
    "Systran/faster-whisper-large-v3",
]


def check(repo: str):
    url = f"https://huggingface.co/api/models/{repo}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            data = json.loads(r.read().decode())
            return data.get("gated"), data.get("private"), None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {str(exc)[:120]}"


def main() -> int:
    print("OA_HF_TOKEN:", "set" if token else "(empty)")
    print("OA_DIARIZATION_MODEL:", s.diarization_model)
    print("OA_EMBEDDING_MODEL:", s.embedding_model)
    print()
    print("ASR（FunASR）：")
    print("  OA_ASR_PROVIDER:", s.asr_provider)
    print("  OA_FUNASR_MODEL:", s.funasr_model)
    print("  OA_FUNASR_VAD_MODEL:", s.funasr_vad_model or "(off)")
    print("  OA_FUNASR_PUNC_MODEL:", s.funasr_punc_model or "(off)")
    print("  OA_FUNASR_DEVICE:", s.funasr_device)
    print("  OA_FUNASR_HUB:", s.funasr_hub)
    try:
        import funasr  # type: ignore  # noqa: F401

        print("  funasr package: installed")
    except Exception as exc:  # noqa: BLE001
        print("  funasr package: NOT installed ->", exc)
    print()
    print("HuggingFace 模型状态（gated=auto/true 表示需要网页接受条款）：")
    for repo in dict.fromkeys(repos):
        print(f"  {repo} -> {check(repo)}")

    print("\nfaster-whisper model size -> HF repo（可选回退）：")
    try:
        from faster_whisper.utils import _MODELS

        for key, value in _MODELS.items():
            print(f"  {key} -> {value}")
    except Exception as exc:  # noqa: BLE001
        print("  (faster-whisper 未安装或版本不同)", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
