"""Windows 下为 ctranslate2/faster-whisper 准备 CUDA 12 / cuDNN 9 的 DLL 搜索路径。

ctranslate2 的 CUDA 加速依赖 ``cublas64_12.dll`` / ``cublasLt64_12.dll`` /
``cudnn*_9.dll``；Windows 上这些 DLL 不在默认搜索路径时，会在推理阶段报
``Library cublas64_12.dll is not found or cannot be loaded``。
本模块按常见安装位置自动探测并加入 ``os.add_dll_directory`` + ``PATH``：

* ``OA_CUDA_DLL_DIRS``（分号分隔的显式目录）
* 系统 CUDA Toolkit: ``C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v12*\\bin``
* NVIDIA cuDNN: ``C:\\Program Files\\NVIDIA\\CUDNN\\v9*\\bin``
* pip 安装的 ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12``（site-packages/nvidia/*/bin）
* 常见自带 CUDA 运行时的应用（如 DaVinci Resolve，含 CUDA 12 + cuDNN 9）

探测失败不会影响服务启动，ASR 会自动回退 CPU int8。
"""
from __future__ import annotations

import glob
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_added_dirs: list[str] = []
_handles: list[object] = []


def _candidate_dirs(extra_dirs: str = "") -> list[str]:
    candidates: list[str] = []
    if extra_dirs:
        for part in extra_dirs.replace(",", ";").split(";"):
            part = part.strip().strip('"')
            if part:
                candidates.append(part)
    if os.name != "nt":
        return candidates
    candidates += [
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
        r"C:\Program Files\NVIDIA\CUDNN\v9.8\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.7\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.6\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.5\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.4\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.3\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.2\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.1\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9.0\bin",
    ]
    for pattern in (
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12*\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13*\bin",
        r"C:\Program Files\NVIDIA\CUDNN\v9*\bin",
    ):
        candidates.extend(sorted(glob.glob(pattern), reverse=True))
    for base in sys.path:
        if not base:
            continue
        try:
            candidates.extend(str(p) for p in (Path(base) / "nvidia").glob("*/bin"))
        except OSError:
            continue
    return candidates


def _has_cuda_dlls(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    try:
        names = {entry.name.lower() for entry in directory.iterdir() if entry.is_file()}
    except OSError:
        return False
    return any(name.startswith("cublas64_") or name.startswith("cudnn") for name in names)


def prepare_cuda_dlls(extra_dirs: str = "") -> list[str]:
    """把探测到的 CUDA DLL 目录加入进程搜索路径，返回实际新增的目录列表（幂等）。"""
    added: list[str] = []
    for raw in _candidate_dirs(extra_dirs):
        try:
            directory = Path(raw)
            resolved = str(directory.resolve())
        except OSError:
            continue
        if resolved in _added_dirs or not _has_cuda_dlls(directory):
            continue
        try:
            if hasattr(os, "add_dll_directory"):
                _handles.append(os.add_dll_directory(resolved))
            os.environ["PATH"] = resolved + os.pathsep + os.environ.get("PATH", "")
            _added_dirs.append(resolved)
            added.append(resolved)
        except OSError:
            continue
    if added:
        logger.info("added CUDA DLL directories: %s", added)
    return added
