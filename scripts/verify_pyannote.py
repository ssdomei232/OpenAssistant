"""限时验证脚本：在受限沙箱里测试 pyannote embedding / diarization 推理。

沙箱会拒绝写入 mkdtemp(0700) 目录，这里放宽 mkdir 模式（同 scripts/win_pip.py）。
仅用于本地诊断，不属于产品代码。
"""
import os
import sys

_real_mkdir = os.mkdir


def _mkdir(path, mode=0o777, *, dir_fd=None):
    if dir_fd is not None:
        return _real_mkdir(path, mode, dir_fd=dir_fd)
    return _real_mkdir(path)


os.mkdir = _mkdir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import numpy as np  # noqa: E402
import torch  # noqa: E402
from app.config import get_settings  # noqa: E402

s = get_settings()
token = s.hf_token or None

# 1 秒 440Hz 正弦 + 静音，模拟 16k 输入
sr = 16000
t = np.arange(sr, dtype=np.float32) / sr
signal = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
waveform = torch.from_numpy(signal).unsqueeze(0)  # (channel, time)

print("embedding models:")
from pyannote.audio import Inference, Model  # noqa: E402

for name in ["pyannote/embedding", "pyannote/wespeaker-voxceleb-resnet34-LM"]:
    try:
        model = Model.from_pretrained(name, token=token)
        print(f"  {name}: loaded {type(model).__name__}")
        inference = Inference(model, window="whole")
        vector = np.asarray(inference({"waveform": waveform, "sample_rate": sr})).reshape(-1)
        norm = float(np.linalg.norm(vector))
        print(f"    embedding OK dim={vector.shape[0]} norm={norm:.3f}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: ERROR {type(exc).__name__}: {str(exc)[:300]}")

print("\ndiarization pipeline:")
try:
    from pyannote.audio import Pipeline  # noqa: E402

    pipeline = Pipeline.from_pretrained(s.diarization_model, token=token)
    print(f"  {s.diarization_model}: loaded {type(pipeline).__name__}")
    output = pipeline({"waveform": waveform, "sample_rate": sr})
    annotation = getattr(output, "speaker_diarization", output)
    turns = list(annotation.itertracks(yield_label=True))
    print(f"    diarize OK, turns={len(turns)}")
except Exception as exc:  # noqa: BLE001
    print(f"  {s.diarization_model}: ERROR {type(exc).__name__}: {str(exc)[:500]}")
