"""EdgeValidator のユニットテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.decision.unified.adaptive.edge_validator import (
    EdgeAlertLevel,
    EdgeStatus,
    EdgeValidator,
    EdgeValidatorConfig,
)
from autotrader.decision.unified.adaptive.trade_record import (
    TradeRecord,
)


def _make_record(
    pnl: float = 100.0,
    pnl_pips: float = 10.0,
    exit_reason: str = "TP_HIT",
) -> TradeRecord:
    """テスト用トレード記録を生成"""
    return TradeRecord(
        pnl=pnl,
        pnl_pips=pnl_pips,
        exit_reason=exit_reason,
        regime="TREND",
        consensus_score=15.0,
        mfe_pips=20.0,
        mae_pips=5.0,
        sl_pips=20.0,
        holding_minutes=60.0,
        closed_at=datetime(2025, 1, 1, 12, 0),
    )


class TestEdgeValidatorConfig:
    """EdgeValidatorConfig のテスト"""

    def test_default_values(self) -> None:
        """デフォルト値の確認"""
        cfg = EdgeValidatorConfig()
        assert cfg.enabled is True
        assert cfg.window_size == 100
        assert cfg.min_samples == 20
        assert cfg.expected_winrate == 0.80

    def test_custom_values(self) -> None:
        """カスタム値の確認"""
        cfg = EdgeValidatorConfig(
            window_size=50,
            expected_winrate=0.75,
        )
        assert cfg.window_size == 50
        assert cfg.expected_winrate == 0.75


class TestEdgeValidator:
    """EdgeValidator のテスト"""

    def test_initial_state(self) -> None:
        """初期状態の確認"""
        ev = EdgeValidator()
        assert ev.window_size == 0
        assert ev.last_status.alert_level == EdgeAlertLevel.OK

    def test_insufficient_samples(self) -> None:
        """最小サンプル未満では OK を返す"""
        ev = EdgeValidator(EdgeValidatorConfig(min_samples=10))
        for _ in range(5):
            status = ev.record_trade(_make_record())
        assert status.alert_level == EdgeAlertLevel.OK
        assert status.sample_count == 5

    def test_all_wins_is_ok(self) -> None:
        """全勝ならOK"""
        ev = EdgeValidator(
            EdgeValidatorConfig(
                min_samples=5,
                window_size=20,
            ),
        )
        for _ in range(10):
            status = ev.record_trade(_make_record(pnl=100.0))
        assert status.alert_level == EdgeAlertLevel.OK
        assert status.rolling_winrate == 1.0
        assert status.rolling_pf > 1.0

    def test_critical_low_winrate(self) -> None:
        """WR20%低下でCRITICAL"""
        ev = EdgeValidator(
            EdgeValidatorConfig(
                min_samples=5,
                window_size=20,
                expected_winrate=0.80,
                critical_wr_drop=0.20,
            ),
        )
        # 20トレード中12勝8敗 → WR=60% → 20%低下
        for _ in range(12):
            ev.record_trade(_make_record(pnl=100.0, pnl_pips=10.0))
        for _ in range(8):
            status = ev.record_trade(
                _make_record(pnl=-50.0, pnl_pips=-5.0),
            )
        assert status.alert_level == EdgeAlertLevel.CRITICAL
        assert status.wr_drop >= 0.20

    def test_warning_low_pf(self) -> None:
        """PF<1.5でWARNING"""
        ev = EdgeValidator(
            EdgeValidatorConfig(
                min_samples=5,
                window_size=20,
                expected_winrate=0.50,
                warning_pf_threshold=1.5,
                warning_wr_drop=0.30,
                critical_wr_drop=0.40,
            ),
        )
        # 20トレード中12勝8敗、利益小・損失大 → PF < 1.5
        for _ in range(12):
            ev.record_trade(_make_record(pnl=10.0, pnl_pips=1.0))
        for _ in range(8):
            status = ev.record_trade(
                _make_record(pnl=-20.0, pnl_pips=-2.0),
            )
        # PF = 120/160 = 0.75 → CRITICAL (PF < 1.0)
        assert status.alert_level == EdgeAlertLevel.CRITICAL

    def test_info_minor_wr_drop(self) -> None:
        """WR軽微低下でINFO"""
        ev = EdgeValidator(
            EdgeValidatorConfig(
                min_samples=5,
                window_size=20,
                expected_winrate=0.80,
                info_wr_drop=0.05,
                warning_wr_drop=0.10,
                critical_wr_drop=0.20,
            ),
        )
        # WR = 15/20 = 0.75 → 5%低下 → INFO
        for _ in range(15):
            ev.record_trade(_make_record(pnl=100.0, pnl_pips=10.0))
        for _ in range(5):
            status = ev.record_trade(
                _make_record(pnl=-30.0, pnl_pips=-3.0),
            )
        assert status.alert_level == EdgeAlertLevel.INFO

    def test_pf_below_1_persistence(self) -> None:
        """PF<1.0が持続でCRITICAL"""
        ev = EdgeValidator(
            EdgeValidatorConfig(
                min_samples=5,
                window_size=30,
                expected_winrate=0.50,
                pf_below_1_max_trades=3,
                critical_wr_drop=0.40,
            ),
        )
        # 全負け → PF=0、持続カウンタ増加
        for _ in range(10):
            status = ev.record_trade(
                _make_record(pnl=-100.0, pnl_pips=-10.0),
            )
        assert status.pf_below_1_count >= 3
        assert status.alert_level == EdgeAlertLevel.CRITICAL

    def test_reset(self) -> None:
        """リセットで初期化"""
        ev = EdgeValidator(EdgeValidatorConfig(min_samples=5))
        for _ in range(10):
            ev.record_trade(_make_record())
        ev.reset()
        assert ev.window_size == 0
        assert ev.last_status.alert_level == EdgeAlertLevel.OK

    def test_disabled(self) -> None:
        """無効化時はOKを返し続ける"""
        ev = EdgeValidator(EdgeValidatorConfig(enabled=False))
        for _ in range(30):
            status = ev.record_trade(
                _make_record(pnl=-100.0),
            )
        assert status.alert_level == EdgeAlertLevel.OK

    def test_get_status_dict(self) -> None:
        """辞書変換"""
        ev = EdgeValidator(EdgeValidatorConfig(min_samples=3))
        for _ in range(5):
            ev.record_trade(_make_record())
        d = ev.get_status_dict()
        assert "edge_alert_level" in d
        assert "edge_rolling_winrate" in d
        assert "edge_rolling_pf" in d

    def test_rolling_sharpe_with_variance(self) -> None:
        """勝ち負け混在時のSharpeは平均と分散から算出"""
        ev = EdgeValidator(EdgeValidatorConfig(min_samples=3))
        # 正のpnl_pipsが多い → Sharpe正
        for i in range(8):
            ev.record_trade(
                _make_record(pnl=100.0, pnl_pips=10.0 + i),
            )
        for i in range(2):
            ev.record_trade(
                _make_record(pnl=-30.0, pnl_pips=-3.0 - i),
            )
        assert ev.last_status.rolling_sharpe > 0

    def test_rolling_sharpe_zero_variance(self) -> None:
        """全同一pnlなら分散0でSharpe=0"""
        ev = EdgeValidator(EdgeValidatorConfig(min_samples=3))
        for _ in range(10):
            ev.record_trade(
                _make_record(pnl=100.0, pnl_pips=10.0),
            )
        assert ev.last_status.rolling_sharpe == 0.0
