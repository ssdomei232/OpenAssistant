"""安静处理第三方模型内部的「已知无害」数值告警。

pyannote / torch 在**极短音频**上会出现数值退化：池化层 `std(correction=1)` 在
时间维只剩 1 帧时提示 ``std(): degrees of freedom is <= 0``，聚类阶段对空簇求均值
时 numpy 提示 ``Mean of empty slice`` / ``invalid value encountered in divide``。
这些告警不影响结果（上层已有短音频门控与声纹兜底），但会污染服务日志，
因此在调用模型的最小范围内显式过滤，其他告警仍然照常输出。
"""
from __future__ import annotations

import re
import warnings
from contextlib import contextmanager

_KNOWN_NUMERICAL_WARNINGS = (
    (UserWarning, "std(): degrees of freedom is <= 0"),
    (RuntimeWarning, "Mean of empty slice"),
    (RuntimeWarning, "invalid value encountered in divide"),
    (RuntimeWarning, "invalid value encountered in scalar divide"),
)


@contextmanager
def quiet_numerical_warnings():
    with warnings.catch_warnings():
        for category, message in _KNOWN_NUMERICAL_WARNINGS:
            warnings.filterwarnings("ignore", message=re.escape(message), category=category)
        yield
