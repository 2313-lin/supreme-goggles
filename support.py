"""测试辅助：每个用例一套独立的内存库 / 审计文件。"""
from __future__ import annotations

import os
import tempfile

from nds.api import NdsApp
from nds.config import Settings


def make_settings(tmpdir: str, **overrides) -> Settings:
    settings = Settings.load()
    settings.memory_db = os.path.join(tmpdir, "memory.db")
    settings.audit_log = os.path.join(tmpdir, "audit.jsonl")
    settings.rate_limit_per_min = 1000
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def make_app(**overrides):
    """返回 (app, tempdir)；调用方负责 app.close() 与 tempdir.cleanup()。"""
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    return NdsApp(make_settings(tmp.name, **overrides)), tmp
