"""检查 HuggingFace 模型（pyannote / faster-whisper）的可访问性与 gated 状态。

用法（在 server/ 目录或用 PYTHONPATH 指向 server）：
    python scripts/check_hf_models.py

读取 server/.env 中的 OA_HF_TOKEN，用 HuggingFace API 查询模型是否 gated，
并打印 faster-whisper 的 model size → HF repo 映射，便于排查 403 / 模型名错误。
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
    print("HuggingFace 模型状态（gated=auto/true 表示需要网页接受条款）：")
    for repo in dict.fromkeys(repos):
        print(f"  {repo} -> {check(repo)}")

    print("\nfaster-whisper model size -> HF repo：")
    try:
        from faster_whisper.utils import _MODELS

        for key, value in _MODELS.items():
            print(f"  {key} -> {value}")
    except Exception as exc:  # noqa: BLE001
        print("  (faster-whisper 未安装或版本不同)", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
