"""アダプティブパラメータ調整モジュールのテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.decision.unified.adaptive import (
    AdaptiveOverrides,
    AdaptiveParameterTuner,
    TradeRecord,
    TunerConfig,
)


# --- ヘルパー ---

def _make_record(
    pnl: float = 100.0,
    exit_reason: str = "TRAIL_HIT",
    regime: str = "RANGE",
    consensus_score: float = 9.0,
    mfe_pips: float = 20.0,
    mae_pips: float = 5.0,
    sl_pips: float = 15.0,
    holding_minutes: float = 60.0,
) -> TradeRecord:
    """テスト用TradeRecord生成"""
    return TradeRecord(
        pnl=pnl,
        pnl_pips=pnl / 100,
        exit_reason=exit_reason,
        regime=regime,
        consensus_score=consensus_score,
        mfe_pips=mfe_pips,
        mae_pips=mae_pips,
        sl_pips=sl_pips,
        holding_minutes=holding_minutes,
        closed_at=datetime(2024, 1, 1, 12, 0),
    )


def _make_sl_hit(
    pnl: float = -150.0,
    mae_pips: float = 16.0,
    sl_pips: float = 15.0,
) -> TradeRecord:
    """SL_HITのTradeRecord"""
    return _make_record(
        pnl=pnl,
        exit_reason="SL_HIT",
        mfe_pips=3.0,
        mae_pips=mae_pips,
        sl_pips=sl_pips,
    )


def _make_stag(pnl: float = -50.0) -> TradeRecord:
    """STAGNATIONのTradeRecord"""
    return _make_record(
        pnl=pnl,
        exit_reason="STAGNATION",
        holding_minutes=120.0,
    )


# --- TradeRecord テスト ---

class TestTradeRecord:
    """TradeRecordのテスト"""

    def test_is_win(self) -> None:
        """勝ちトレード判定"""
        assert _make_record(pnl=100).is_win
        assert not _make_record(pnl=-100).is_win
        assert not _make_record(pnl=0).is_win

    def test_is_sl_hit(self) -> None:
        """SL_HIT判定"""
        assert _make_sl_hit().is_sl_hit
        assert not _make_record().is_sl_hit

    def test_is_stagnation(self) -> None:
        """STAGNATION判定"""
        assert _make_stag().is_stagnation
        assert not _make_record().is_stagnation


# --- AdaptiveOverrides テスト ---

class TestAdaptiveOverrides:
    """AdaptiveOverridesのテスト"""

    def test_neutral_is_inactive(self) -> None:
        """デフォルトは非アクティブ"""
        o = AdaptiveOverrides()
        assert not o.is_active

    def test_active_when_modified(self) -> None:
        """変更があればアクティブ"""
        o = AdaptiveOverrides(consensus_threshold_delta=0.5)
        assert o.is_active
        o2 = AdaptiveOverrides(sl_multiplier=1.1)
        assert o2.is_active

    def test_frozen(self) -> None:
        """frozen dataclass"""
        o = AdaptiveOverrides()
        with pytest.raises(AttributeError):
            o.sl_multiplier = 1.5  # type: ignore[misc]


# --- AdaptiveParameterTuner テスト ---

class TestAdaptiveParameterTuner:
    """AdaptiveParameterTunerのテスト"""

    def test_initial_state(self) -> None:
        """初期状態はオーバーライドなし"""
        tuner = AdaptiveParameterTuner()
        assert tuner.window_size == 0
        assert tuner.eval_count == 0
        assert not tuner.get_overrides().is_active

    def test_no_eval_before_min_samples(self) -> None:
        """最小サンプル未満では評価しない"""
        config = TunerConfig(
            window_size=30,
            eval_interval=5,
            min_samples=10,
        )
        tuner = AdaptiveParameterTuner(config)

        # 5件だけ追加（eval_intervalに到達）
        for _ in range(5):
            tuner.record_trade(_make_record())

        # 評価は実行されたがmin_samples未満→調整なし
        assert tuner.eval_count == 1
        assert not tuner.get_overrides().is_active

    def test_eval_triggers_after_interval(self) -> None:
        """eval_interval件ごとに評価"""
        config = TunerConfig(
            window_size=30,
            eval_interval=5,
            min_samples=5,
        )
        tuner = AdaptiveParameterTuner(config)

        # 全勝の10件
        for _ in range(10):
            tuner.record_trade(_make_record(pnl=200))

        # 2回評価されるはず
        assert tuner.eval_count == 2

    def test_high_sl_hit_rate_increases_sl(self) -> None:
        """SL_HIT率が高いとSL乗数が上がる"""
        config = TunerConfig(
            window_size=20,
            eval_interval=5,
            min_samples=5,
            sl_hit_rate_high=0.35,
        )
        tuner = AdaptiveParameterTuner(config)

        # 80% SL_HIT（ニアミス）
        for i in range(10):
            if i < 8:
                tuner.record_trade(_make_sl_hit(
                    mae_pips=16.0,
                    sl_pips=15.0,
                ))
            else:
                tuner.record_trade(_make_record(pnl=200))

        o = tuner.get_overrides()
        assert o.sl_multiplier > 1.0

    def test_low_winrate_increases_threshold(self) -> None:
        """低勝率でコンセンサス閾値が上がる"""
        config = TunerConfig(
            window_size=20,
            eval_interval=5,
            min_samples=5,
            winrate_low=0.40,
        )
        tuner = AdaptiveParameterTuner(config)

        # 20%勝率
        for i in range(10):
            if i < 2:
                tuner.record_trade(_make_record(pnl=200))
            else:
                tuner.record_trade(_make_sl_hit())

        o = tuner.get_overrides()
        assert o.consensus_threshold_delta > 0

    def test_good_performance_no_change(self) -> None:
        """高勝率・低SL_HITなら大きな変化なし"""
        config = TunerConfig(
            window_size=20,
            eval_interval=5,
            min_samples=5,
        )
        tuner = AdaptiveParameterTuner(config)

        # 80%勝率、SL_HITなし
        for i in range(10):
            if i < 8:
                tuner.record_trade(_make_record(pnl=200))
            else:
                tuner.record_trade(_make_stag(pnl=-30))

        o = tuner.get_overrides()
        assert o.consensus_threshold_delta == 0.0
        assert 0.9 <= o.sl_multiplier <= 1.1

    def test_high_stag_loss_reduces_time(self) -> None:
        """STAGNATION損失率高で時間乗数が下がる"""
        config = TunerConfig(
            window_size=20,
            eval_interval=5,
            min_samples=5,
            stag_loss_rate_high=0.30,
        )
        tuner = AdaptiveParameterTuner(config)

        # STAGNATION多め（70%が損失STAG）
        for i in range(10):
            if i < 7:
                tuner.record_trade(_make_stag(pnl=-80))
            else:
                tuner.record_trade(_make_record(pnl=200))

        o = tuner.get_overrides()
        assert o.stagnation_multiplier < 1.0

    def test_reset_clears_state(self) -> None:
        """リセットで状態クリア"""
        tuner = AdaptiveParameterTuner()
        for _ in range(10):
            tuner.record_trade(_make_record())

        tuner.reset()
        assert tuner.window_size == 0
        assert tuner.eval_count == 0
        assert not tuner.get_overrides().is_active

    def test_get_stats(self) -> None:
        """統計取得"""
        tuner = AdaptiveParameterTuner()
        for _ in range(5):
            tuner.record_trade(_make_record(pnl=100))
        for _ in range(5):
            tuner.record_trade(_make_sl_hit())

        stats = tuner.get_stats()
        assert stats["window_size"] == 10
        assert stats["win_rate"] == 0.5
        assert stats["sl_hit_rate"] == 0.5

    def test_window_overflow(self) -> None:
        """ウィンドウサイズ超過で古い記録が消える"""
        config = TunerConfig(window_size=5)
        tuner = AdaptiveParameterTuner(config)

        for _ in range(10):
            tuner.record_trade(_make_record())

        assert tuner.window_size == 5

    def test_clamp_ranges(self) -> None:
        """調整値がクランプ範囲内に収まる"""
        config = TunerConfig(
            window_size=20,
            eval_interval=5,
            min_samples=5,
            ct_delta_min=-1.0,
            ct_delta_max=2.0,
            sl_mult_min=0.8,
            sl_mult_max=1.4,
        )
        tuner = AdaptiveParameterTuner(config)

        # 極端なデータ（全損失）
        for _ in range(20):
            tuner.record_trade(_make_sl_hit())

        o = tuner.get_overrides()
        assert o.consensus_threshold_delta <= 2.0
        assert o.sl_multiplier <= 1.4
        assert o.sl_multiplier >= 0.8
