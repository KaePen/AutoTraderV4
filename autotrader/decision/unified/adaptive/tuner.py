"""アダプティブパラメータ調整器

直近Nトレードのローリングウィンドウを評価し、
パラメータのオーバーライド値を算出する。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from autotrader.decision.unified.adaptive.overrides import (
    AdaptiveOverrides,
)
from autotrader.decision.unified.adaptive.trade_record import (
    TradeRecord,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TunerConfig:
    """アダプティブ調整設定

    Attributes:
        window_size: ローリングウィンドウサイズ
        eval_interval: 再評価間隔（トレード数）
        min_samples: 最小サンプル数
        ct_delta_min: consensus閾値の最大緩和
        ct_delta_max: consensus閾値の最大厳格化
        sl_mult_min: SL乗数の下限
        sl_mult_max: SL乗数の上限
        sl_hit_rate_high: SL_HIT率高 → SL拡大
        sl_hit_rate_low: SL_HIT率低 → SL縮小
        winrate_low: 勝率低 → 閾値厳格化
        stag_loss_rate_high: STAGNATION損失率高 → 時間短縮
    """

    window_size: int = 30
    eval_interval: int = 5
    min_samples: int = 10
    # consensus threshold 調整範囲
    ct_delta_min: float = -1.0
    ct_delta_max: float = 2.0
    # SL乗数範囲
    sl_mult_min: float = 0.8
    sl_mult_max: float = 1.4
    # 調整トリガー条件
    sl_hit_rate_high: float = 0.35
    sl_hit_rate_low: float = 0.10
    winrate_low: float = 0.40
    winrate_high: float = 0.55
    stag_loss_rate_high: float = 0.30
    # ペナルティ調整
    penalty_delta_min: float = -0.1
    penalty_delta_max: float = 0.1


class AdaptiveParameterTuner:
    """アダプティブパラメータ調整器

    直近Nトレードの結果を分析し、パラメータの
    オーバーライド値を生成する。

    バックテストとリアルトレードの両方で同じロジック。
    """

    def __init__(
        self,
        config: TunerConfig | None = None,
    ) -> None:
        self._config = config or TunerConfig()
        self._window: deque[TradeRecord] = deque(
            maxlen=self._config.window_size,
        )
        self._trades_since_eval: int = 0
        self._current: AdaptiveOverrides = AdaptiveOverrides()
        self._eval_count: int = 0

    @property
    def config(self) -> TunerConfig:
        """調整設定"""
        return self._config

    @property
    def current_overrides(self) -> AdaptiveOverrides:
        """現在のオーバーライド値"""
        return self._current

    @property
    def window_size(self) -> int:
        """現在のウィンドウ内トレード数"""
        return len(self._window)

    @property
    def eval_count(self) -> int:
        """評価実行回数"""
        return self._eval_count

    def record_trade(self, record: TradeRecord) -> None:
        """トレード結果を記録し、必要なら再評価

        Args:
            record: トレード記録
        """
        self._window.append(record)
        self._trades_since_eval += 1

        if self._trades_since_eval >= self._config.eval_interval:
            self._current = self._evaluate()
            self._trades_since_eval = 0
            self._eval_count += 1

    def get_overrides(self) -> AdaptiveOverrides:
        """現在のオーバーライド値を取得"""
        return self._current

    def reset(self) -> None:
        """状態をリセット（年次境界等）"""
        self._window.clear()
        self._trades_since_eval = 0
        self._current = AdaptiveOverrides()
        self._eval_count = 0

    def _evaluate(self) -> AdaptiveOverrides:
        """パフォーマンスを評価してオーバーライドを算出"""
        trades = list(self._window)
        n = len(trades)

        if n < self._config.min_samples:
            return AdaptiveOverrides()

        return AdaptiveOverrides(
            consensus_threshold_delta=self._calc_ct_delta(trades),
            sl_multiplier=self._calc_sl_mult(trades),
            stagnation_multiplier=self._calc_stag_mult(trades),
            penalty_cap_delta=self._calc_penalty_delta(trades),
        )

    def _calc_ct_delta(
        self,
        trades: list[TradeRecord],
    ) -> float:
        """consensus_threshold の調整量を算出

        - 勝率 < winrate_low → 閾値を上げて低品質排除
        - 勝率 > winrate_high → 現状維持（安全設計）
        - 中間 → 段階的に調整

        低スコアトレードの損失率も加味する。
        """
        n = len(trades)
        wins = sum(1 for t in trades if t.is_win)
        winrate = wins / n

        # 低スコア帯（下位1/3）のパフォーマンス
        scores = sorted(t.consensus_score for t in trades)
        low_threshold = scores[n // 3] if n >= 3 else 0.0
        low_score_trades = [
            t for t in trades
            if t.consensus_score <= low_threshold
        ]
        low_score_pnl = (
            sum(t.pnl for t in low_score_trades)
            if low_score_trades
            else 0.0
        )

        delta = 0.0

        # 勝率ベースの調整
        if winrate < self._config.winrate_low:
            # 勝率不足 → 厳格化（最大+1.0）
            ratio = (
                (self._config.winrate_low - winrate)
                / self._config.winrate_low
            )
            delta += min(ratio * 2.0, 1.0)

        # 低スコア帯が損失源 → さらに厳格化
        if low_score_pnl < 0 and n >= self._config.min_samples:
            delta += 0.3

        # クランプ
        return max(
            self._config.ct_delta_min,
            min(delta, self._config.ct_delta_max),
        )

    def _calc_sl_mult(
        self,
        trades: list[TradeRecord],
    ) -> float:
        """SL乗数を算出

        - SL_HIT率高 + MAEがSLギリギリ → SL拡大
        - SL_HIT率低 → SL縮小で利益効率化
        """
        n = len(trades)
        sl_hits = [t for t in trades if t.is_sl_hit]
        sl_hit_rate = len(sl_hits) / n

        mult = 1.0

        if sl_hit_rate > self._config.sl_hit_rate_high:
            # SL_HIT率が高い
            # MAEがSLを僅かに超過するケースが多いか判定
            near_miss = sum(
                1 for t in sl_hits
                if t.mae_pips > 0
                and t.mae_pips <= t.sl_pips * 1.3
            )
            near_miss_rate = (
                near_miss / len(sl_hits)
                if sl_hits
                else 0.0
            )

            if near_miss_rate > 0.4:
                # ギリギリ刈られが多い → SL拡大
                mult = 1.0 + (sl_hit_rate - 0.2) * 0.5
            else:
                # 大幅負け → SLだけでなく品質の問題
                mult = 1.0 + (sl_hit_rate - 0.3) * 0.3

        elif sl_hit_rate < self._config.sl_hit_rate_low:
            # SL_HITが少ない → 少し縮小も可
            mult = 0.95

        return max(
            self._config.sl_mult_min,
            min(mult, self._config.sl_mult_max),
        )

    def _calc_stag_mult(
        self,
        trades: list[TradeRecord],
    ) -> float:
        """stagnation exit 乗数を算出

        - STAGNATION損失率高 → 時間短縮（乗数<1）
        - STAGNATION損失率低 → 現状維持
        """
        stag_trades = [t for t in trades if t.is_stagnation]
        if not stag_trades:
            return 1.0

        stag_loss_count = sum(
            1 for t in stag_trades if t.pnl < 0
        )
        stag_loss_rate = stag_loss_count / len(stag_trades)

        if stag_loss_rate > self._config.stag_loss_rate_high:
            # 損失STAGNATION率高 → 時間短縮
            return max(0.7, 1.0 - (stag_loss_rate - 0.3) * 0.5)

        return 1.0

    def _calc_penalty_delta(
        self,
        trades: list[TradeRecord],
    ) -> float:
        """ペナルティ上限の調整

        勝率が低い場合、ペナルティ上限を下げて
        よりクリーンなエントリーのみ許可する。
        """
        n = len(trades)
        wins = sum(1 for t in trades if t.is_win)
        winrate = wins / n

        if winrate < self._config.winrate_low:
            # ペナルティ上限を下げる（厳格化）
            return min(
                self._config.penalty_delta_max,
                (self._config.winrate_low - winrate) * 0.3,
            )

        return 0.0

    def get_stats(self) -> dict[str, float]:
        """現在のウィンドウ統計（デバッグ用）"""
        trades = list(self._window)
        n = len(trades)
        if n == 0:
            return {
                "window_size": 0,
                "win_rate": 0.0,
                "sl_hit_rate": 0.0,
                "stag_rate": 0.0,
                "avg_pnl": 0.0,
            }

        wins = sum(1 for t in trades if t.is_win)
        sl_hits = sum(1 for t in trades if t.is_sl_hit)
        stag = sum(1 for t in trades if t.is_stagnation)

        return {
            "window_size": n,
            "win_rate": wins / n,
            "sl_hit_rate": sl_hits / n,
            "stag_rate": stag / n,
            "avg_pnl": sum(t.pnl for t in trades) / n,
            "eval_count": self._eval_count,
        }
