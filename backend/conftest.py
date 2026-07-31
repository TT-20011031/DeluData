"""Backend 根级 conftest.py。

主要目的：把 backend 目录加入 sys.path，让所有测试文件顶部的
`from app.xxx import ...` 在 pytest collection 阶段就能解析。

pytest 默认 prepend 模式只会把测试文件所在目录（如 app/tests/）加入
sys.path，而不会自动加入项目根，因此需要这一步显式补齐。
"""
from __future__ import annotations

import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
