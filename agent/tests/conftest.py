"""pytest 全局配置。

沙箱/树莓派 CI 上系统临时目录可能不可写，因此把 ``tmp_path`` 重定向到
仓库内的 ``agent/.pytest-tmp``，每个测试独立目录并在结束后清理。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

TMP_ROOT = Path(__file__).resolve().parent.parent / ".pytest-tmp"


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in request.node.name
    )[:48]
    path = TMP_ROOT / f"{safe_name}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
