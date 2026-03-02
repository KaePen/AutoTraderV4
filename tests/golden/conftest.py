"""ゴールデンテスト用フィクスチャ"""

from __future__ import annotations

from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def golden_dir() -> Path:
    """ゴールデンデータ格納ディレクトリ"""
    return GOLDEN_DIR
