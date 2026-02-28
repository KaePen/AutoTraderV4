"""低ボラフィルタ機能のテスト"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from autotrader.constraint.filters.session_filter import SessionFilter
from autotrader.decision.unified.config import UnifiedBotConfig


class TestBBWidthFilter:
    """BB幅ベース動的フィルタのテスト"""

    def test_ultra_low_bbw_threshold_default(self):
        """デフォルト設定の確認"""
        config = UnifiedBotConfig()
        assert config.ultra_low_bbw_threshold == 0.08
        assert config.ultra_low_bbw_score_add == 1.0

    def test_ultra_low_bbw_custom_values(self):
        """カスタム値の設定確認"""
        config = UnifiedBotConfig(
            ultra_low_bbw_threshold=0.10,
            ultra_low_bbw_score_add=2.0,
        )
        assert config.ultra_low_bbw_threshold == 0.10
        assert config.ultra_low_bbw_score_add == 2.0


class TestTokyoLowVolBlock:
    """TOKYO×低ボラブロックのテスト"""

    def test_tokyo_low_vol_block(self):
        """TOKYO×低ボラでブロックされることを確認"""
        session_filter = SessionFilter(
            allowed_sessions=["TOKYO", "LONDON", "NY"]
        )

        # TOKYO時間（UTC 0-9時）
        tokyo_time = datetime(2024, 1, 1, 3, 0)
        bb_width = 0.08  # 低ボラ

        result = session_filter.check_tokyo_low_vol(tokyo_time, bb_width)
        assert result is False  # ブロック

    def test_tokyo_normal_vol_allowed(self):
        """TOKYO×通常ボラは許可されることを確認"""
        session_filter = SessionFilter(
            allowed_sessions=["TOKYO", "LONDON", "NY"]
        )

        # TOKYO時間（UTC 0-9時）
        tokyo_time = datetime(2024, 1, 1, 3, 0)
        bb_width = 0.12  # 通常ボラ

        result = session_filter.check_tokyo_low_vol(tokyo_time, bb_width)
        assert result is True  # 許可

    def test_london_low_vol_allowed(self):
        """LONDON×低ボラは許可されることを確認"""
        session_filter = SessionFilter(
            allowed_sessions=["TOKYO", "LONDON", "NY"]
        )

        # LONDON時間（UTC 9-17時）
        london_time = datetime(2024, 1, 1, 12, 0)
        bb_width = 0.08  # 低ボラ

        result = session_filter.check_tokyo_low_vol(london_time, bb_width)
        assert result is True  # TOKYO以外は許可


class TestEarlyPartial03R:
    """0.3R早期部分利確のテスト"""

    def test_early_partial_0_3r_config_default(self):
        """デフォルト設定の確認"""
        from autotrader.decision.unified.position_manager import (
            PositionManagerConfig,
        )

        config = PositionManagerConfig()
        assert config.early_partial_0_3r_enabled is False
        assert config.early_partial_0_3r_ratio == 0.20

    def test_early_partial_0_3r_config_custom(self):
        """カスタム設定の確認"""
        from autotrader.decision.unified.position_manager import (
            PositionManagerConfig,
        )

        config = PositionManagerConfig(
            early_partial_0_3r_enabled=True,
            early_partial_0_3r_ratio=0.30,
        )
        assert config.early_partial_0_3r_enabled is True
        assert config.early_partial_0_3r_ratio == 0.30
