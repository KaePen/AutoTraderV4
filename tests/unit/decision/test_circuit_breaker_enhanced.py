"""サーキットブレーカー強化のユニットテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.decision.unified.config import RiskConfig


class TestConsecutiveLossBreakerConfig:
    """連続敗戦サーキットブレーカー設定テスト"""

    def test_default_config(self) -> None:
        """デフォルト設定"""
        cfg = RiskConfig()
        assert cfg.consecutive_loss_breaker_enabled is True
        assert cfg.consecutive_loss_breaker_threshold == 8
        assert cfg.consecutive_loss_breaker_pause_minutes == 60

    def test_custom_config(self) -> None:
        """カスタム設定"""
        cfg = RiskConfig(
            consecutive_loss_breaker_threshold=3,
            consecutive_loss_breaker_pause_minutes=120,
        )
        assert cfg.consecutive_loss_breaker_threshold == 3
        assert cfg.consecutive_loss_breaker_pause_minutes == 120

    def test_disabled(self) -> None:
        """無効化"""
        cfg = RiskConfig(
            consecutive_loss_breaker_enabled=False,
        )
        assert cfg.consecutive_loss_breaker_enabled is False
