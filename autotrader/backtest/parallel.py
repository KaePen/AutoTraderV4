"""並列マルチタイムフレーム評価モジュール"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import DEFAULT_SCORING, DEFAULT_TF_SCORING
from ..config.tf_params_registry import (
    get_sl_base_mult,
    get_tf_min_score,
    get_tf_weight,
)

if TYPE_CHECKING:


    from .events import CandleEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationResult:
    """評価結果（pickle可能）

    Attributes:
        timeframe: タイムフレーム
        direction: シグナル方向 (BUY/SELL/HOLD)
        buy_strength: 買い強度 (0-1)
        sell_strength: 売り強度 (0-1)
        confidence: 確度 (0-1)
        sl_pips: SL距離
        tp_pips: TP距離
        reason: 判断理由
    """

    timeframe: str
    direction: str
    buy_strength: float
    sell_strength: float
    confidence: float
    sl_pips: float
    tp_pips: float
    reason: str


@dataclass
class EvaluatorParams:
    """評価器パラメータ（pickle可能）

    Attributes:
        timeframe: タイムフレーム
        rsi_oversold: RSI売られすぎ閾値
        rsi_overbought: RSI買われすぎ閾値
        stoch_oversold: ストキャスティクス売られすぎ閾値
        stoch_overbought: ストキャスティクス買われすぎ閾値
        atr_sl_multiplier: ATR SL乗数
    """

    timeframe: str
    rsi_oversold: float = DEFAULT_SCORING.rsi_oversold
    rsi_overbought: float = DEFAULT_SCORING.rsi_overbought
    stoch_oversold: float = DEFAULT_SCORING.stoch_oversold
    stoch_overbought: float = DEFAULT_SCORING.stoch_overbought
    atr_sl_multiplier: float = 1.5
    pip_unit: float = 0.01


# 時間足ごとの最小スコア閾値（中央設定から参照）
# Note: バックテスト用にスケール調整（%ではなく点数）
MIN_SCORES: dict[str, float] = {
    tf: score * 1.5  # TF_SCORINGは%、ここは点数（スケール差を調整）
    for tf, score in DEFAULT_TF_SCORING.min_scores.items()
}

# 時間足の重み（中央設定から参照）
TF_WEIGHTS: dict[str, float] = DEFAULT_TF_SCORING.weights


def evaluate_timeframe_signal(
    timeframe: str,
    row_data: dict[str, float],
    candle_data: dict[str, float],
    params: EvaluatorParams,
    htf_trend: str | None = None,
) -> EvaluationResult:
    """タイムフレームシグナルを評価（ProcessPoolExecutor用）

    この関数はサブプロセスで実行されるため、pickle可能な型のみ使用。

    Args:
        timeframe: タイムフレーム
        row_data: 指標値を含むデータ
        candle_data: OHLCV データ
        params: 評価器パラメータ
        htf_trend: 上位時間足トレンド (BUY/SELL/None)

    Returns:
        EvaluationResult: 評価結果
    """
    buy_score = 0.0
    sell_score = 0.0
    reasons: list[str] = []

    # RSIスコア（最大+3点）
    rsi = row_data.get("rsi_14")
    if rsi is not None:
        if rsi < params.rsi_oversold - 10:
            buy_score += 3.0
            reasons.append(f"RSI極低({rsi:.1f})")
        elif rsi < params.rsi_oversold - 5:
            buy_score += 2.0
            reasons.append(f"RSI低({rsi:.1f})")
        elif rsi < params.rsi_oversold:
            buy_score += 1.0
        elif rsi > params.rsi_overbought + 10:
            sell_score += 3.0
            reasons.append(f"RSI極高({rsi:.1f})")
        elif rsi > params.rsi_overbought + 5:
            sell_score += 2.0
            reasons.append(f"RSI高({rsi:.1f})")
        elif rsi > params.rsi_overbought:
            sell_score += 1.0

    # MACDスコア（最大+3点）
    macd = row_data.get("macd")
    macd_signal = row_data.get("macd_signal")
    macd_hist = row_data.get("macd_histogram", 0)

    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            buy_score += 1.0
            if macd > 0:
                buy_score += 1.0
                reasons.append("MACD+プラス圏")
            macd_slope = row_data.get("macd_hist_slope", 0)
            if macd_slope and macd_slope > 0 and macd_hist > 0:
                buy_score += 1.0
        elif macd < macd_signal:
            sell_score += 1.0
            if macd < 0:
                sell_score += 1.0
                reasons.append("MACD+マイナス圏")
            macd_slope = row_data.get("macd_hist_slope", 0)
            if macd_slope and macd_slope < 0 and macd_hist < 0:
                sell_score += 1.0

    # トレンドスコア（最大+2点）
    sma_20 = row_data.get("sma_20")
    sma_50 = row_data.get("sma_50")
    close = candle_data.get("close")

    if sma_20 is not None and sma_50 is not None and close is not None:
        if close > sma_20 > sma_50:
            buy_score += 2.0
            reasons.append("上昇トレンド")
        elif close < sma_20 < sma_50:
            sell_score += 2.0
            reasons.append("下降トレンド")
        elif close > sma_20:
            buy_score += 1.0
        elif close < sma_20:
            sell_score += 1.0

    # ADXスコア（最大+2点）
    adx = row_data.get("adx")
    if adx is not None and buy_score != sell_score:
        if adx > 30:
            if buy_score > sell_score:
                buy_score += 2.0
            else:
                sell_score += 2.0
            reasons.append(f"強トレンド(ADX:{adx:.1f})")
        elif adx > 25:
            if buy_score > sell_score:
                buy_score += 1.0
            else:
                sell_score += 1.0

    # ストキャスティクススコア（最大+2点）
    stoch_k = row_data.get("stoch_k")
    if stoch_k is not None:
        if stoch_k < params.stoch_oversold:
            buy_score += 2.0
            reasons.append(f"Stoch売られすぎ({stoch_k:.1f})")
        elif stoch_k < 30:
            buy_score += 1.0
        elif stoch_k > params.stoch_overbought:
            sell_score += 2.0
            reasons.append(f"Stoch買われすぎ({stoch_k:.1f})")
        elif stoch_k > 70:
            sell_score += 1.0

    # ダイバージェンススコア（最大+3点）
    bullish_div = row_data.get("is_bullish_div", 0)
    bearish_div = row_data.get("is_bearish_div", 0)

    if bullish_div and not bearish_div:
        buy_score += 3.0
        reasons.append("強気ダイバージェンス")
    elif bearish_div and not bullish_div:
        sell_score += 3.0
        reasons.append("弱気ダイバージェンス")

    # 上位時間足整合性ボーナス（最大+2点）
    if htf_trend is not None:
        if htf_trend == "BUY" and buy_score > sell_score:
            buy_score += 2.0
            reasons.append("HTF整合")
        elif htf_trend == "SELL" and sell_score > buy_score:
            sell_score += 2.0
            reasons.append("HTF整合")

    # 方向と確度を決定
    min_score = MIN_SCORES.get(
        timeframe, get_tf_min_score(timeframe) * 1.5
    )
    max_score = max(buy_score, sell_score)

    if max_score < min_score:
        direction = "HOLD"
        confidence = 0.0
    else:
        score_diff = abs(buy_score - sell_score)
        confidence = min(score_diff / 15.0 + max_score / 30.0, 1.0)

        if buy_score > sell_score:
            direction = "BUY"
        elif sell_score > buy_score:
            direction = "SELL"
        else:
            direction = "HOLD"
            confidence = 0.0

    # SL/TP計算
    atr = row_data.get("atr_14")
    if atr is not None and close is not None and close > 0:
        atr_pips = atr / params.pip_unit

        base_mult = get_sl_base_mult(timeframe)

        sl_pips = max(20.0, min(atr_pips * base_mult, 50.0))
        tp_pips = sl_pips  # 勝率重視: TP = SL
    else:
        default_sl = params.atr_sl_multiplier * 10.0
        sl_pips = default_sl
        tp_pips = default_sl

    return EvaluationResult(
        timeframe=timeframe,
        direction=direction,
        buy_strength=min(buy_score / 10.0, 1.0),
        sell_strength=min(sell_score / 10.0, 1.0),
        confidence=confidence,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        reason=", ".join(reasons) if reasons else "条件不十分",
    )


class ParallelSignalEvaluator:
    """並列シグナル評価器

    複数タイムフレームを並列で評価する。
    """

    def __init__(
        self,
        max_workers: int = 6,
        params: EvaluatorParams | None = None,
    ):
        """初期化

        Args:
            max_workers: 最大ワーカー数
            params: 評価器パラメータ（デフォルト値使用可）
        """
        self._max_workers = max_workers
        self._default_params = params or EvaluatorParams(timeframe="")

        # 上位足の最新トレンドをキャッシュ
        self._htf_trends: dict[str, str] = {}

    def update_htf_trend(self, timeframe: str, direction: str) -> None:
        """上位時間足のトレンドを更新

        Args:
            timeframe: タイムフレーム
            direction: 方向 (BUY/SELL/HOLD)
        """
        if direction in ("BUY", "SELL"):
            self._htf_trends[timeframe] = direction

    def get_htf_trend(self, timeframe: str) -> str | None:
        """指定TFより上位のトレンドを取得

        Args:
            timeframe: 基準タイムフレーム

        Returns:
            str | None: 上位足の合意トレンド
        """
        from autotrader.core.enums import Timeframe as _TF
        tf_order = [tf.value for tf in _TF.all_trading()]
        try:
            idx = tf_order.index(timeframe)
        except ValueError:
            return None

        # 上位足のトレンドを確認
        htf_list = tf_order[idx + 1:]
        buy_count = 0
        sell_count = 0

        for htf in htf_list:
            trend = self._htf_trends.get(htf)
            if trend == "BUY":
                buy_count += 1
            elif trend == "SELL":
                sell_count += 1

        # 過半数で決定
        if buy_count > sell_count and buy_count >= 1:
            return "BUY"
        elif sell_count > buy_count and sell_count >= 1:
            return "SELL"
        return None

    def evaluate_batch(
        self,
        events: list["CandleEvent"],
    ) -> dict[str, EvaluationResult]:
        """イベントバッチを評価

        単一イベント→直接評価
        複数イベント→ProcessPoolExecutorで並列

        Args:
            events: CandleEvent のリスト

        Returns:
            dict[str, EvaluationResult]: タイムフレーム別評価結果
        """
        if not events:
            return {}

        # 単一イベントは直接評価
        if len(events) == 1:
            event = events[0]
            htf_trend = self.get_htf_trend(event.timeframe)
            params = EvaluatorParams(timeframe=event.timeframe)

            result = evaluate_timeframe_signal(
                timeframe=event.timeframe,
                row_data=event.row_data,
                candle_data=event.candle_data,
                params=params,
                htf_trend=htf_trend,
            )

            # 上位足トレンドを更新
            if result.direction in ("BUY", "SELL"):
                self.update_htf_trend(event.timeframe, result.direction)

            return {event.timeframe: result}

        # 複数イベント→並列評価
        results: dict[str, EvaluationResult] = {}

        # イベント数が少ない場合は直列評価（オーバーヘッド回避）
        if len(events) <= 2:
            for event in events:
                htf_trend = self.get_htf_trend(event.timeframe)
                params = EvaluatorParams(timeframe=event.timeframe)

                result = evaluate_timeframe_signal(
                    timeframe=event.timeframe,
                    row_data=event.row_data,
                    candle_data=event.candle_data,
                    params=params,
                    htf_trend=htf_trend,
                )
                results[event.timeframe] = result

                if result.direction in ("BUY", "SELL"):
                    self.update_htf_trend(event.timeframe, result.direction)

            return results

        # 並列評価
        with ProcessPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {}

            for event in events:
                htf_trend = self.get_htf_trend(event.timeframe)
                params = EvaluatorParams(timeframe=event.timeframe)

                future = executor.submit(
                    evaluate_timeframe_signal,
                    timeframe=event.timeframe,
                    row_data=event.row_data,
                    candle_data=event.candle_data,
                    params=params,
                    htf_trend=htf_trend,
                )
                futures[future] = event.timeframe

            for future in as_completed(futures):
                tf = futures[future]
                try:
                    result = future.result()
                    results[tf] = result

                    if result.direction in ("BUY", "SELL"):
                        self.update_htf_trend(tf, result.direction)
                except Exception as e:
                    logger.warning(f"並列評価エラー ({tf}): {e}")

        return results

    def evaluate_sequential(
        self,
        events: list["CandleEvent"],
    ) -> dict[str, EvaluationResult]:
        """イベントバッチを順次評価（デバッグ用）

        Args:
            events: CandleEvent のリスト

        Returns:
            dict[str, EvaluationResult]: タイムフレーム別評価結果
        """
        results: dict[str, EvaluationResult] = {}

        for event in events:
            htf_trend = self.get_htf_trend(event.timeframe)
            params = EvaluatorParams(timeframe=event.timeframe)

            result = evaluate_timeframe_signal(
                timeframe=event.timeframe,
                row_data=event.row_data,
                candle_data=event.candle_data,
                params=params,
                htf_trend=htf_trend,
            )
            results[event.timeframe] = result

            if result.direction in ("BUY", "SELL"):
                self.update_htf_trend(event.timeframe, result.direction)

        return results


@dataclass
class PriorityEvaluationResult:
    """優先度評価結果

    全TFを同時評価し、重み付けスコアで最適なエントリーTFを決定。

    Attributes:
        tf_results: TF別評価結果
        best_entry_tf: 最適エントリーTF
        consensus_direction: コンセンサス方向
        weighted_score: 重み付けスコア
        should_enter: エントリーすべきか
        sl_pips: SL距離
        tp_pips: TP距離
        reasoning: 判断理由
    """

    tf_results: dict[str, EvaluationResult]
    best_entry_tf: str | None
    consensus_direction: str
    weighted_score: float
    should_enter: bool
    sl_pips: float
    tp_pips: float
    reasoning: str


class PriorityBasedEvaluator:
    """優先度ベース評価器

    全TFを同時計算し、重み付けスコアで優先度判断。
    モード分けなしで動的にエントリーTFを決定。
    """

    # 閾値設定
    MIN_WEIGHTED_SCORE: float = 4.0  # 最小重み付けスコア
    MIN_ALIGNMENT_RATIO: float = 0.5  # 最小整合率

    def __init__(
        self,
        max_workers: int = 6,
        min_weighted_score: float = 4.0,
        min_alignment_ratio: float = 0.5,
    ):
        """初期化

        Args:
            max_workers: 最大ワーカー数
            min_weighted_score: 最小重み付けスコア
            min_alignment_ratio: 最小整合率
        """
        self._signal_evaluator = ParallelSignalEvaluator(max_workers=max_workers)
        self._min_weighted_score = min_weighted_score
        self._min_alignment_ratio = min_alignment_ratio

    def evaluate_all_timeframes(
        self,
        events: list["CandleEvent"],
    ) -> PriorityEvaluationResult:
        """全TFを同時評価して優先度判断

        Args:
            events: CandleEventのリスト（複数TF同時確定可能）

        Returns:
            PriorityEvaluationResult: 優先度評価結果
        """
        if not events:
            return PriorityEvaluationResult(
                tf_results={},
                best_entry_tf=None,
                consensus_direction="HOLD",
                weighted_score=0.0,
                should_enter=False,
                sl_pips=0.0,
                tp_pips=0.0,
                reasoning="イベントなし",
            )

        # 全TF並列評価
        tf_results = self._signal_evaluator.evaluate_batch(events)

        if not tf_results:
            return PriorityEvaluationResult(
                tf_results={},
                best_entry_tf=None,
                consensus_direction="HOLD",
                weighted_score=0.0,
                should_enter=False,
                sl_pips=0.0,
                tp_pips=0.0,
                reasoning="評価結果なし",
            )

        # 方向別スコア計算（重み付け）
        buy_score = 0.0
        sell_score = 0.0
        buy_tfs: list[str] = []
        sell_tfs: list[str] = []

        for tf, result in tf_results.items():
            weight = TF_WEIGHTS.get(tf, get_tf_weight(tf))
            strength = max(result.buy_strength, result.sell_strength)

            if result.direction == "BUY":
                buy_score += weight * strength
                buy_tfs.append(tf)
            elif result.direction == "SELL":
                sell_score += weight * strength
                sell_tfs.append(tf)

        # コンセンサス方向決定
        if buy_score > sell_score:
            consensus_direction = "BUY"
            weighted_score = buy_score
            aligned_tfs = buy_tfs
        elif sell_score > buy_score:
            consensus_direction = "SELL"
            weighted_score = sell_score
            aligned_tfs = sell_tfs
        else:
            return PriorityEvaluationResult(
                tf_results=tf_results,
                best_entry_tf=None,
                consensus_direction="HOLD",
                weighted_score=0.0,
                should_enter=False,
                sl_pips=0.0,
                tp_pips=0.0,
                reasoning="方向不明（BUY/SELL同点）",
            )

        # 整合率チェック
        total_tfs = len(tf_results)
        alignment_ratio = len(aligned_tfs) / total_tfs if total_tfs > 0 else 0

        # 閾値チェック
        if weighted_score < self._min_weighted_score:
            return PriorityEvaluationResult(
                tf_results=tf_results,
                best_entry_tf=None,
                consensus_direction="HOLD",
                weighted_score=weighted_score,
                should_enter=False,
                sl_pips=0.0,
                tp_pips=0.0,
                reasoning=f"スコア不足: {weighted_score:.2f} < {self._min_weighted_score}",
            )

        if alignment_ratio < self._min_alignment_ratio:
            return PriorityEvaluationResult(
                tf_results=tf_results,
                best_entry_tf=None,
                consensus_direction="HOLD",
                weighted_score=weighted_score,
                should_enter=False,
                sl_pips=0.0,
                tp_pips=0.0,
                reasoning=f"整合率不足: {alignment_ratio:.1%} < {self._min_alignment_ratio:.0%}",
            )

        # 最適エントリーTFを決定（確定したTFの中で最も信頼度が高いもの）
        best_entry_tf = None
        best_confidence = 0.0

        for tf in aligned_tfs:
            result = tf_results[tf]
            # 今回確定したTFのみ対象
            if any(e.timeframe == tf for e in events):
                if result.confidence > best_confidence:
                    best_confidence = result.confidence
                    best_entry_tf = tf

        if best_entry_tf is None:
            return PriorityEvaluationResult(
                tf_results=tf_results,
                best_entry_tf=None,
                consensus_direction="HOLD",
                weighted_score=weighted_score,
                should_enter=False,
                sl_pips=0.0,
                tp_pips=0.0,
                reasoning="確定TFに整合シグナルなし",
            )

        # SL/TPはエントリーTFの値を使用
        entry_result = tf_results[best_entry_tf]
        sl_pips = entry_result.sl_pips
        tp_pips = entry_result.tp_pips

        reasoning = (
            f"{consensus_direction}シグナル: "
            f"score={weighted_score:.2f}, "
            f"alignment={alignment_ratio:.0%}, "
            f"entry_tf={best_entry_tf}, "
            f"aligned={aligned_tfs}"
        )

        return PriorityEvaluationResult(
            tf_results=tf_results,
            best_entry_tf=best_entry_tf,
            consensus_direction=consensus_direction,
            weighted_score=weighted_score,
            should_enter=True,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            reasoning=reasoning,
        )


# 後方互換性のためのエイリアス
ModeAwareEvaluationResult = PriorityEvaluationResult


class ModeAwareParallelEvaluator:
    """モード対応並列評価器（後方互換性のためのラッパー）

    実際にはPriorityBasedEvaluatorを使用。
    """

    def __init__(
        self,
        max_workers: int = 6,
    ):
        """初期化

        Args:
            max_workers: 最大ワーカー数
        """
        self._evaluator = PriorityBasedEvaluator(max_workers=max_workers)

    def evaluate_with_mode(
        self,
        events: list["CandleEvent"],
        h1_row_data: dict[str, float] | None = None,
    ) -> PriorityEvaluationResult:
        """モードを考慮した評価（実際は優先度ベース）

        Args:
            events: CandleEventのリスト
            h1_row_data: H1データ（未使用、後方互換性のため）

        Returns:
            PriorityEvaluationResult: 評価結果
        """
        return self._evaluator.evaluate_all_timeframes(events)


# 以下は既存の __init__.py との互換性のためのスタブ
# TODO: 将来的に実装または削除

class ParallelYearExecutor:
    """年別並列実行器（スタブ）

    将来の並列年処理用。現在は未実装。
    """

    def __init__(self, max_workers: int = 4):
        """初期化

        Args:
            max_workers: 最大ワーカー数
        """
        self._max_workers = max_workers

    def execute(self, *args: any, **kwargs: any) -> any:
        """実行（未実装）"""
        raise NotImplementedError("ParallelYearExecutor is not implemented")


class ParallelDataLoader:
    """並列データローダー（スタブ）

    将来の並列データ読込用。現在は未実装。
    """

    def __init__(self, max_workers: int = 4):
        """初期化

        Args:
            max_workers: 最大ワーカー数
        """
        self._max_workers = max_workers

    def load(self, *args: any, **kwargs: any) -> any:
        """読み込み（未実装）"""
        raise NotImplementedError("ParallelDataLoader is not implemented")


class ParallelStrategyComparator:
    """並列戦略比較器（スタブ）

    将来の並列戦略比較用。現在は未実装。
    """

    def __init__(self, max_workers: int = 4):
        """初期化

        Args:
            max_workers: 最大ワーカー数
        """
        self._max_workers = max_workers

    def compare(self, *args: any, **kwargs: any) -> any:
        """比較（未実装）"""
        raise NotImplementedError(
            "ParallelStrategyComparator is not implemented"
        )
