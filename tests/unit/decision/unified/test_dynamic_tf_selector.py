"""DynamicTFSelectorのユニットテスト"""

from __future__ import annotations


from autotrader.core.enums import SignalType
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.dynamic_tf_selector import (
    MAJOR_TF_LADDER,
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

    def test_primary_tf_uses_major_ladder(self) -> None:
        """primary_tfはメジャーTFラダーで1段上"""
        tf_signals = {"M5": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "M5"
        # メジャーTFラダー: M1→M5→M15→H1→H4→D1
        # M5の1段上はM15
        assert result.selected_primary_tf == "M15"

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


class TestDeriveAllRoles:
    """_derive_all_rolesのテスト（メジャーTFラダー）"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.selector = DynamicTFSelector()

    def test_m5_entry_roles(self) -> None:
        """M5エントリー時の全ロール導出"""
        tf_signals = {"M5": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "M5"
        assert result.selected_primary_tf == "M15"
        assert result.selected_manage_tf == "M15"
        assert result.selected_regime_tf == "H1"
        assert result.selected_htf_alignment_tfs == ["H4", "D1"]

    def test_m15_entry_roles(self) -> None:
        """M15エントリー時の全ロール導出"""
        tf_signals = {"M15": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "M15"
        assert result.selected_primary_tf == "H1"
        assert result.selected_manage_tf == "H1"
        assert result.selected_regime_tf == "H4"
        assert result.selected_htf_alignment_tfs == ["D1"]

    def test_h1_entry_roles(self) -> None:
        """H1エントリー時の全ロール導出"""
        tf_signals = {"H1": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "H1"
        assert result.selected_primary_tf == "H4"
        assert result.selected_manage_tf == "H4"
        assert result.selected_regime_tf == "D1"
        # regime_tf(D1)より上がないので[D1]保証
        assert result.selected_htf_alignment_tfs == ["D1"]

    def test_h4_entry_roles(self) -> None:
        """H4エントリー時の全ロール導出（上限近く）"""
        tf_signals = {"H4": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "H4"
        assert result.selected_primary_tf == "D1"
        assert result.selected_manage_tf == "D1"
        assert result.selected_regime_tf == "D1"
        assert result.selected_htf_alignment_tfs == ["D1"]

    def test_d1_entry_roles(self) -> None:
        """D1エントリー時の全ロール導出（最上段）"""
        tf_signals = {"D1": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "D1"
        # 全て最上段にキャップ
        assert result.selected_primary_tf == "D1"
        assert result.selected_manage_tf == "D1"
        assert result.selected_regime_tf == "D1"
        assert result.selected_htf_alignment_tfs == ["D1"]

    def test_m1_entry_roles(self) -> None:
        """M1エントリー時の全ロール導出（最下段）"""
        tf_signals = {"M1": MockSignal(SignalType.BUY, 0.9)}
        result = self.selector.select(tf_signals)
        assert result.selected_entry_tf == "M1"
        assert result.selected_primary_tf == "M5"
        assert result.selected_manage_tf == "M5"
        assert result.selected_regime_tf == "M15"
        assert result.selected_htf_alignment_tfs == ["H1", "H4", "D1"]


class TestSnapToLadder:
    """_snap_to_ladderのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.selector = DynamicTFSelector()

    def test_exact_match(self) -> None:
        """ラダーに完全一致するTF"""
        for i, tf in enumerate(MAJOR_TF_LADDER):
            assert self.selector._snap_to_ladder(tf) == i

    def test_intermediate_tf_snaps_down(self) -> None:
        """中間TFは最も近い以下のラダー段に吸着"""
        # M10 → M5段（idx=1）
        assert self.selector._snap_to_ladder("M10") == 1
        # M30 → M15段（idx=2）
        assert self.selector._snap_to_ladder("M30") == 2
        # H2 → H1段（idx=3）
        assert self.selector._snap_to_ladder("H2") == 3
        # H8 → H4段（idx=4）
        assert self.selector._snap_to_ladder("H8") == 4


class TestFallbackWithConfig:
    """config参照フォールバックのテスト"""

    def test_fallback_uses_config_defaults(self) -> None:
        """フォールバック時にconfigのデフォルト値が使われる"""
        config = UnifiedBotConfig(
            default_entry_tf="H1",
            default_primary_tf="H4",
            default_manage_tf="H4",
        )
        selector = DynamicTFSelector(bot_config=config)
        result = selector.select({})
        assert result.selected_entry_tf == "H1"
        assert result.selected_primary_tf == "H4"
        assert result.selected_manage_tf == "H4"

    def test_fallback_without_config(self) -> None:
        """config未設定時のハードコードデフォルト"""
        selector = DynamicTFSelector()
        result = selector.select({})
        assert result.selected_entry_tf == "M15"
        assert result.selected_primary_tf == "H1"
        assert result.selected_manage_tf == "M15"
        assert result.selected_regime_tf == "H1"
        assert result.selected_htf_alignment_tfs == ["H4", "D1"]
