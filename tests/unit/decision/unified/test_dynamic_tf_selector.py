"""DynamicTFSelectorのユニットテスト"""

from __future__ import annotations


from autotrader.core.enums import SignalType
from autotrader.decision.unified.dynamic_tf_selector import (
    DynamicTFSelector,
)


class MockSignal:
    """テスト用モックシグナル"""

    def __init__(self, direction: SignalType, strength: float) -> None:
        self.direction = direction
        self.buy_strength = strength if direction == SignalType.BUY else 0.0
        self.sell_strength = (
            strength if direction == SignalType.SELL else 0.0
        )
        self.confidence = strength


class TestDynamicTFSelector:
    """DynamicTFSelectorのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.selector = DynamicTFSelector()

    def test_select_highest_strength_tf(self) -> None:
        """最も強いシグナルのTFが選択される"""
        tf_signals = {
            "M5": MockSignal(SignalType.BUY, 0.5),
            "H1": MockSignal(SignalType.BUY, 0.9),  # 最強
            "H4": MockSignal(SignalType.BUY, 0.7),
        }
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "H1"

    def test_empty_signals_returns_default(self) -> None:
        """シグナルなし時はデフォルト（M15）を返す"""
        result = self.selector.select({})
        assert result.selected_entry_tf == "M15"

    def test_hold_signals_excluded(self) -> None:
        """HOLDシグナルはスコア0で無視される"""
        tf_signals = {
            "M5": MockSignal(SignalType.HOLD, 0.9),
            "H1": MockSignal(SignalType.BUY, 0.6),
        }
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "H1"

    def test_holding_bars_by_entry_tf(self) -> None:
        """entry_tfに応じたmax_holding_barsが設定される"""
        # M1エントリー -> 90分 / 1分 = 90バー
        tf_signals = {"M1": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "M1"
        assert result.max_holding_bars == 90  # 90分 / 1分

    def test_dominant_direction_filter(self) -> None:
        """dominant_direction指定時はその方向のみ評価"""
        tf_signals = {
            "M5": MockSignal(SignalType.SELL, 0.9),  # SELL強い
            "H1": MockSignal(SignalType.BUY, 0.6),   # BUY
        }
        result = self.selector.select(
            tf_signals, dominant_direction=SignalType.BUY
        )
        assert result.selected_entry_tf == "H1"  # BUY方向のみ評価

    def test_primary_tf_is_one_level_higher(self) -> None:
        """primary_tfはentry_tfの1つ上"""
        tf_signals = {"M5": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "M5"
        assert result.selected_primary_tf == "M6"

    def test_all_hold_signals_returns_default(self) -> None:
        """全シグナルがHOLD時はデフォルトを返す"""
        tf_signals = {
            "M5": MockSignal(SignalType.HOLD, 0.9),
            "H1": MockSignal(SignalType.HOLD, 0.8),
        }
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "M15"

    def test_result_has_all_tf_scores(self) -> None:
        """結果にall_tf_scoresが含まれる"""
        tf_signals = {
            "M5": MockSignal(SignalType.BUY, 0.7),
            "H1": MockSignal(SignalType.BUY, 0.5),
        }
        result = self.selector.select(tf_signals)
        assert "M5" in result.all_tf_scores
        assert "H1" in result.all_tf_scores

    def test_tp_sl_ratio_range_by_entry_tf(self) -> None:
        """entry_tf別にTP/SL比率範囲が設定される"""
        tf_signals = {"D1": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "D1"
        # D1のTP/SL比率範囲
        assert result.tp_sl_ratio_range == (1.3, 1.8)

    def test_dominant_direction_all_opposite_returns_default(self) -> None:
        """dominant_directionと全シグナルが逆方向の場合デフォルト"""
        tf_signals = {
            "M5": MockSignal(SignalType.SELL, 0.9),
            "H1": MockSignal(SignalType.SELL, 0.8),
        }
        result = self.selector.select(
            tf_signals, dominant_direction=SignalType.BUY
        )
        # BUY方向シグナルがないのでデフォルト
        assert result.selected_entry_tf == "M15"
