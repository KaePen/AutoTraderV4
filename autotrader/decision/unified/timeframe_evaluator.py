"""時間足別評価器"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import pandas as pd

from autotrader.core.enums import SignalType

from .config import EvaluatorConfig
from .strength_calculator import IndicatorStrength, IndicatorStrengthCalculator

if TYPE_CHECKING:
    from autotrader.core.entities import Candle
    from .mode_selector import TradingPlan


@dataclass(frozen=True)
class ScoreBreakdown:
    """スコア内訳（各指標の個別貢献値）

    Attributes:
        trend: トレンド判定スコア (0-5.0)
        adx: ADX強度ボーナス (0/+2.0)
        rsi: RSIフィルター (0/+1.0, 過熱=-999)
        macd_slope: MACDヒストグラム傾斜 (±2.5)
        divergence: ダイバージェンス (±2.0/±1.5)
        ema_cross: EMAクロス (±2.5/±0.5)
        stochastic: ストキャスティクス (±1.5/±0.5)
        htf: HTF整合性ボーナス
    """

    trend: float = 0.0
    adx: float = 0.0
    rsi: float = 0.0
    macd_slope: float = 0.0
    divergence: float = 0.0
    ema_cross: float = 0.0
    stochastic: float = 0.0
    htf: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """辞書変換"""
        return asdict(self)


@dataclass(frozen=True)
class TimeframeSignal:
    """時間足別シグナル

    Attributes:
        timeframe: 時間足
        direction: シグナル方向
        buy_strength: 買い強度（0.0 ~ 1.0）
        sell_strength: 売り強度（0.0 ~ 1.0）
        confidence: 確度（0.0 ~ 1.0）
        sl_pips: 損切りpips
        tp_pips: 利確pips
        reason: 判断理由
        indicator_strength: 指標強度詳細
    """

    timeframe: str
    direction: SignalType
    buy_strength: float
    sell_strength: float
    confidence: float
    sl_pips: float
    tp_pips: float
    reason: str
    indicator_strength: IndicatorStrength | None = None
    score_breakdown: ScoreBreakdown | None = None

    @property
    def net_strength(self) -> float:
        """純強度（正=買い、負=売り）

        Returns:
            float: 純強度
        """
        return self.buy_strength - self.sell_strength


class TimeframeEvaluator:
    """時間足別評価器

    特定の時間足のデータを評価し、シグナルを生成する。
    """

    # 時間足ごとのATR係数（長期足ほど広いSL/TP）
    ATR_MULTIPLIERS: dict[str, tuple[float, float]] = {
        "M1": (1.0, 1.5),
        "M5": (1.2, 1.8),
        "M15": (1.5, 2.0),
        "H1": (2.0, 3.0),
        "H4": (2.5, 4.0),
        "D1": (3.0, 5.0),
    }

    # 最大スコア（全指標満点）
    MAX_POSSIBLE_SCORE: float = 15.0

    # 時間足ごとの最小スコア閾値（正規化比率）
    # バランス型：取引数と勝率のバランス
    NORMALIZED_MIN_SCORES: dict[str, float] = {
        "M1": 0.10,   # 1.5点
        "M5": 0.12,   # 1.8点
        "M15": 0.14,  # 2.1点
        "H1": 0.16,   # 2.4点
        "H4": 0.18,   # 2.7点
        "D1": 0.20,   # 3.0点
    }

    # 後方互換性のためのプロパティ
    @property
    def MIN_SCORES(self) -> dict[str, float]:
        """時間足ごとの最小スコア閾値（絶対値）"""
        return {
            tf: ratio * self.MAX_POSSIBLE_SCORE
            for tf, ratio in self.NORMALIZED_MIN_SCORES.items()
        }

    def __init__(
        self,
        timeframe: str,
        config: EvaluatorConfig | None = None,
    ):
        """初期化

        Args:
            timeframe: 対象時間足
            config: 評価器設定
        """
        self.timeframe = timeframe
        self.config = config or EvaluatorConfig(timeframe=timeframe)
        self.calculator = IndicatorStrengthCalculator(
            self.config.strength_config
        )
        self._htf_data: dict[str, pd.DataFrame] = {}
        self._current_eval_time: pd.Timestamp | None = None

    def set_higher_tf_data(
        self,
        data: dict[str, pd.DataFrame],
    ) -> None:
        """上位時間足データを設定

        Args:
            data: 時間足別データフレーム
        """
        self._htf_data = data

    def evaluate(
        self,
        row: pd.Series,
        candle: Candle | None = None,
        plan: TradingPlan | None = None,
        current_time: pd.Timestamp | None = None,
    ) -> TimeframeSignal:
        """該当時間足のシグナル評価（トレンドフィルター付き）

        Args:
            row: 指標値を含むデータ行
            candle: ローソク足データ（オプション）
            plan: トレーディングプラン（オプション）
            current_time: 現在時刻（HTF参照用）

        Returns:
            TimeframeSignal: 時間足別シグナル
        """
        # 時間帯フィルター無効（トレード数確保のため）
        # 後で有効にする場合はコメントアウトを解除
        pass

        # 現在時刻を保持（HTFスコアリングで使用）
        self._current_eval_time = current_time

        # 指標強度を計算
        strength = self.calculator.calculate(row)

        # スコア計算
        buy_score, sell_score, reasons, breakdown = (
            self._calculate_score(row, candle, strength)
        )

        # SMCスコアリング（無効化 - 性能低下のため）
        # 将来的にフィルターとして活用検討
        pass

        # 方向決定
        direction, confidence = self._determine_direction(
            buy_score, sell_score
        )

        # 短期足ノイズフィルター適用
        direction = self._apply_noise_filter(row, direction)
        if direction == SignalType.HOLD:
            confidence = 0.0

        # SL/TP計算（planを渡してTP/SL比率を反映）
        sl_pips, tp_pips = self._calculate_sl_tp(row, strength, plan)

        return TimeframeSignal(
            timeframe=self.timeframe,
            direction=direction,
            buy_strength=min(buy_score / 10.0, 1.0),
            sell_strength=min(sell_score / 10.0, 1.0),
            confidence=confidence,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            reason=", ".join(reasons) if reasons else "条件不十分",
            indicator_strength=strength,
            score_breakdown=breakdown,
        )

    def _calculate_score(
        self,
        row: pd.Series,
        candle: Candle | None,
        strength: IndicatorStrength,
    ) -> tuple[float, float, list[str], ScoreBreakdown]:
        """買い/売りスコアを計算（MTF整合+モメンタム戦略）

        短期足と上位足のトレンドが一致し、
        モメンタムが確認できる場合にエントリー。

        Args:
            row: データ行
            candle: ローソク足データ
            strength: 指標強度

        Returns:
            tuple: (買いスコア, 売りスコア, 理由リスト, スコア内訳)
        """
        buy_score = 0.0
        sell_score = 0.0
        reasons: list[str] = []

        # スコア内訳追跡用
        _bd_trend = 0.0
        _bd_adx = 0.0
        _bd_rsi = 0.0
        _bd_macd_slope = 0.0
        _bd_divergence = 0.0
        _bd_ema_cross = 0.0
        _bd_stochastic = 0.0
        _bd_htf = 0.0

        # 現在足のトレンド判定
        close = row.get("close")
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        ema_12 = row.get("ema_12")
        ema_26 = row.get("ema_26")

        _empty_bd = ScoreBreakdown()
        if any(v is None or pd.isna(v) for v in [close, sma_20, sma_50]):
            return 0.0, 0.0, [], _empty_bd

        # トレンド判定
        uptrend = close > sma_20
        downtrend = close < sma_20
        full_uptrend = close > sma_20 > sma_50
        full_downtrend = close < sma_20 < sma_50

        if not uptrend and not downtrend:
            return 0.0, 0.0, ["トレンドなし"], _empty_bd

        # MACDモメンタム
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        macd_bullish = False
        macd_bearish = False
        if macd is not None and macd_signal is not None:
            if not pd.isna(macd) and not pd.isna(macd_signal):
                macd_bullish = macd > macd_signal
                macd_bearish = macd < macd_signal

        # ADXトレンド強度
        adx = row.get("adx")
        strong_trend = False
        if adx is not None and not pd.isna(adx):
            strong_trend = adx > 20

        # スコアリング（トレンド必須）
        if full_uptrend and macd_bullish:
            buy_score = 5.0
            _bd_trend = 5.0
            reasons.append("完全上昇+MACD↑")
            if strong_trend:
                buy_score += 2.0
                _bd_adx = 2.0
        elif full_downtrend and macd_bearish:
            sell_score = 5.0
            _bd_trend = 5.0
            reasons.append("完全下降+MACD↓")
            if strong_trend:
                sell_score += 2.0
                _bd_adx = 2.0
        elif uptrend and macd_bullish:
            buy_score = 4.0
            _bd_trend = 4.0
            reasons.append("上昇+MACD↑")
        elif downtrend and macd_bearish:
            sell_score = 4.0
            _bd_trend = 4.0
            reasons.append("下降+MACD↓")
        elif full_uptrend or uptrend:
            buy_score = 2.5
            _bd_trend = 2.5
            reasons.append("上昇トレンドのみ")
        elif full_downtrend or downtrend:
            sell_score = 2.5
            _bd_trend = 2.5
            reasons.append("下降トレンドのみ")
        else:
            return 0.0, 0.0, ["条件不十分"], _empty_bd

        # RSIフィルター（極端値のみ除外）
        rsi = row.get("rsi_14")
        if rsi is not None and not pd.isna(rsi):
            if buy_score > 0 and rsi > 80:  # 緩和
                _bd_rsi = -999.0
                return (
                    0.0, 0.0, ["RSI過熱"],
                    ScoreBreakdown(
                        trend=_bd_trend, adx=_bd_adx,
                        rsi=-999.0,
                    ),
                )
            if sell_score > 0 and rsi < 20:  # 緩和
                _bd_rsi = -999.0
                return (
                    0.0, 0.0, ["RSI過冷"],
                    ScoreBreakdown(
                        trend=_bd_trend, adx=_bd_adx,
                        rsi=-999.0,
                    ),
                )
            # 順方向のRSIはボーナス（対称範囲）
            if buy_score > 0 and 30 <= rsi <= 70:
                buy_score += 1.0
                _bd_rsi = 1.0
            elif sell_score > 0 and 30 <= rsi <= 70:
                sell_score += 1.0
                _bd_rsi = 1.0

        # MACDヒストグラム傾斜（モメンタム加速確認）
        macd_hist_slope = row.get("macd_hist_slope")
        if macd_hist_slope is not None and not pd.isna(
            macd_hist_slope
        ):
            if buy_score > 0 and macd_hist_slope > 0:
                buy_score += 2.5
                _bd_macd_slope = 2.5
                reasons.append("MACD加速↑")
            elif sell_score > 0 and macd_hist_slope < 0:
                sell_score += 2.5
                _bd_macd_slope = 2.5
                reasons.append("MACD加速↓")
            elif buy_score > 0 and macd_hist_slope < 0:
                buy_score -= 2.0
                _bd_macd_slope = -2.0
            elif sell_score > 0 and macd_hist_slope > 0:
                sell_score -= 2.0
                _bd_macd_slope = -2.0

        # ダイバージェンス（逆行はペナルティ）
        bull_div = row.get("is_bullish_div", False)
        bear_div = row.get("is_bearish_div", False)
        if bull_div and not pd.isna(bull_div) and bull_div:
            if sell_score > 0:
                sell_score -= 2.0
                _bd_divergence = -2.0
                reasons.append("強気ダイバ→売り抑制")
            elif buy_score > 0:
                buy_score += 1.5
                _bd_divergence = 1.5
                reasons.append("強気ダイバ+買い")
        if bear_div and not pd.isna(bear_div) and bear_div:
            if buy_score > 0:
                buy_score -= 2.0
                _bd_divergence = -2.0
                reasons.append("弱気ダイバ→買い抑制")
            elif sell_score > 0:
                sell_score += 1.5
                _bd_divergence = 1.5
                reasons.append("弱気ダイバ+売り")

        # EMAクロス確認
        if ema_12 is not None and ema_26 is not None:
            if not pd.isna(ema_12) and not pd.isna(ema_26):
                if buy_score > 0 and ema_12 > ema_26:
                    buy_score += 0.5
                    _bd_ema_cross = 0.5
                elif sell_score > 0 and ema_12 < ema_26:
                    sell_score += 0.5
                    _bd_ema_cross = 0.5
                elif buy_score > 0 and ema_12 < ema_26:
                    buy_score -= 2.5
                    _bd_ema_cross = -2.5
                elif sell_score > 0 and ema_12 > ema_26:
                    sell_score -= 2.5
                    _bd_ema_cross = -2.5

        # ストキャスティクス確認（過熱回避）
        stoch_k = row.get("stoch_k")
        if stoch_k is not None and not pd.isna(stoch_k):
            if buy_score > 0 and stoch_k > 80:
                buy_score -= 1.5
                _bd_stochastic = -1.5
                reasons.append("Stoch過買")
            elif sell_score > 0 and stoch_k < 20:
                sell_score -= 1.5
                _bd_stochastic = -1.5
                reasons.append("Stoch過売")
            elif buy_score > 0 and 20 <= stoch_k <= 50:
                buy_score += 0.5
                _bd_stochastic = 0.5
            elif sell_score > 0 and 50 <= stoch_k <= 80:
                sell_score += 0.5
                _bd_stochastic = 0.5

        # 上位時間足整合性（ボーナスのみ）
        htf_bonus, htf_reason = self._score_htf_alignment(
            buy_score, sell_score
        )
        if htf_bonus > 0:
            if buy_score > 0:
                buy_score += htf_bonus
            elif sell_score > 0:
                sell_score += htf_bonus
            _bd_htf = htf_bonus
            if htf_reason:
                reasons.append(htf_reason)

        breakdown = ScoreBreakdown(
            trend=_bd_trend,
            adx=_bd_adx,
            rsi=_bd_rsi,
            macd_slope=_bd_macd_slope,
            divergence=_bd_divergence,
            ema_cross=_bd_ema_cross,
            stochastic=_bd_stochastic,
            htf=_bd_htf,
        )
        return buy_score, sell_score, reasons, breakdown

    def _evaluate_smc_factors(
        self,
        row: pd.Series,
    ) -> tuple[float, float, list[str]]:
        """SMC（Smart Money Concept）要因のスコアリング

        BOS/CHoCH、流動性グラブなどのSMC指標を評価し、
        エントリーシグナルの質を向上させる。

        Args:
            row: データ行（SMC指標を含む）

        Returns:
            tuple[float, float, list[str]]: (買いボーナス, 売りボーナス, 理由)
        """
        buy_bonus = 0.0
        sell_bonus = 0.0
        reasons: list[str] = []

        # BOS（Break of Structure）検出 - ボーナスを控えめに
        bos_signal = row.get("bos_signal", 0)
        if bos_signal == 1:
            buy_bonus += 1.5
            reasons.append("強気BOS")
        elif bos_signal == -1:
            sell_bonus += 1.5
            reasons.append("弱気BOS")

        # CHoCH（Change of Character）検出 - 反転シグナルは重視
        choch_signal = row.get("choch_signal", 0)
        if choch_signal == 1:
            buy_bonus += 2.0
            sell_bonus -= 1.0  # 売りを抑制
            reasons.append("強気CHoCH")
        elif choch_signal == -1:
            sell_bonus += 2.0
            buy_bonus -= 1.0  # 買いを抑制
            reasons.append("弱気CHoCH")

        # 市場構造方向との整合性（逆方向のペナルティを追加）
        structure_direction = row.get("structure_direction", 0)
        if structure_direction == 1:
            buy_bonus += 0.5
            sell_bonus -= 1.0  # 上昇構造での売りペナルティ
        elif structure_direction == -1:
            sell_bonus += 0.5
            buy_bonus -= 1.0  # 下降構造での買いペナルティ

        # 流動性グラブ（確度の高いシグナル）
        liquidity_grab_bullish = row.get("liquidity_grab_bullish", False)
        liquidity_grab_bearish = row.get("liquidity_grab_bearish", False)

        if liquidity_grab_bullish:
            buy_bonus += 2.0
            reasons.append("流動性グラブ反発")
        if liquidity_grab_bearish:
            sell_bonus += 2.0
            reasons.append("流動性グラブ反落")

        # スイングレベル付近のエントリーボーナス
        close = row.get("close")
        last_swing_low = row.get("last_swing_low")
        last_swing_high = row.get("last_swing_high")
        atr = row.get("atr_14", 0)

        if close is not None and atr is not None and atr > 0:
            atr_buffer = atr * 1.5

            # 直近スイングロー付近での買い
            if (
                last_swing_low is not None
                and not pd.isna(last_swing_low)
                and abs(close - last_swing_low) < atr_buffer
            ):
                buy_bonus += 1.0
                reasons.append("スイングロー付近")

            # 直近スイングハイ付近での売り
            if (
                last_swing_high is not None
                and not pd.isna(last_swing_high)
                and abs(close - last_swing_high) < atr_buffer
            ):
                sell_bonus += 1.0
                reasons.append("スイングハイ付近")

        return buy_bonus, sell_bonus, reasons

    def _score_rsi(self, row: pd.Series) -> tuple[float, str]:
        """RSIスコアリング"""
        rsi = row.get("rsi_14")
        if rsi is None or pd.isna(rsi):
            return 0.0, ""

        oversold = self.config.strength_config.rsi_oversold
        overbought = self.config.strength_config.rsi_overbought

        # 極端な売られすぎ → 強い買いシグナル
        if rsi < oversold - 10:
            return 3.0, f"RSI極低({rsi:.1f})"
        # 売られすぎ → 買いシグナル
        elif rsi < oversold - 5:
            return 2.0, f"RSI低({rsi:.1f})"
        elif rsi < oversold:
            return 1.0, ""
        # 極端な買われすぎ → 強い売りシグナル
        elif rsi > overbought + 10:
            return -3.0, f"RSI極高({rsi:.1f})"
        # 買われすぎ → 売りシグナル
        elif rsi > overbought + 5:
            return -2.0, f"RSI高({rsi:.1f})"
        elif rsi > overbought:
            return -1.0, ""
        return 0.0, ""

    def _score_macd(self, row: pd.Series) -> tuple[float, str]:
        """MACDスコアリング"""
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        macd_hist = row.get("macd_histogram", 0)

        if macd is None or macd_signal is None:
            return 0.0, ""
        if pd.isna(macd) or pd.isna(macd_signal):
            return 0.0, ""

        score = 0.0
        reason = ""

        if macd > macd_signal:
            score = 1.0
            if macd > 0:
                score += 1.0
                reason = "MACD+プラス圏"
            # ヒストグラム増加中
            macd_slope = row.get("macd_hist_slope", 0)
            if not pd.isna(macd_slope) and macd_slope > 0 and macd_hist > 0:
                score += 1.0
        elif macd < macd_signal:
            score = -1.0
            if macd < 0:
                score -= 1.0
                reason = "MACD+マイナス圏"
            macd_slope = row.get("macd_hist_slope", 0)
            if not pd.isna(macd_slope) and macd_slope < 0 and macd_hist < 0:
                score -= 1.0

        return score, reason

    def _score_trend(
        self,
        row: pd.Series,
        candle: Candle | None,
    ) -> tuple[float, str]:
        """トレンドスコアリング"""
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")

        if sma_20 is None or sma_50 is None:
            return 0.0, ""
        if pd.isna(sma_20) or pd.isna(sma_50):
            return 0.0, ""

        close = candle.close if candle else row.get("close")
        if close is None or pd.isna(close):
            return 0.0, ""

        if close > sma_20 > sma_50:
            return 2.0, "上昇トレンド"
        elif close < sma_20 < sma_50:
            return -2.0, "下降トレンド"
        elif close > sma_20:
            return 1.0, ""
        elif close < sma_20:
            return -1.0, ""
        return 0.0, ""

    def _score_adx(
        self,
        row: pd.Series,
        buy_score: float,
        sell_score: float,
    ) -> float:
        """ADXスコアリング（強化版）"""
        adx = row.get("adx")
        if adx is None or pd.isna(adx):
            return 0.0

        if buy_score == sell_score:
            return 0.0

        # 強いトレンド時に大きなボーナス
        if adx > 40:
            return 3.0
        elif adx > 30:
            return 2.0
        elif adx > 25:
            return 1.5
        elif adx > 20:
            return 0.5
        return 0.0

    def _score_stochastic(self, row: pd.Series) -> tuple[float, str]:
        """ストキャスティクススコアリング"""
        stoch_k = row.get("stoch_k")
        stoch_d = row.get("stoch_d")

        if stoch_k is None or stoch_d is None:
            return 0.0, ""
        if pd.isna(stoch_k) or pd.isna(stoch_d):
            return 0.0, ""

        # 売られすぎ圏での上昇クロス → 買い
        if stoch_k < 25 and stoch_k > stoch_d:
            return 2.0, "Stoch売られすぎ上昇"
        elif stoch_k < 20:
            return 1.0, ""

        # 買われすぎ圏での下降クロス → 売り
        if stoch_k > 75 and stoch_k < stoch_d:
            return -2.0, "Stoch買われすぎ下降"
        elif stoch_k > 80:
            return -1.0, ""

        return 0.0, ""

    def _score_divergence(self, row: pd.Series) -> tuple[float, str]:
        """ダイバージェンススコアリング"""
        bullish_div = row.get("is_bullish_div", False)
        bearish_div = row.get("is_bearish_div", False)

        if bullish_div and not bearish_div:
            return 3.0, "強気ダイバージェンス"
        elif bearish_div and not bullish_div:
            return -3.0, "弱気ダイバージェンス"
        return 0.0, ""

    def _score_bollinger(self, row: pd.Series) -> tuple[float, str]:
        """ボリンジャーバンドスコアリング（逆張り）"""
        bb_percent_b = row.get("bb_percent_b")

        if bb_percent_b is None or pd.isna(bb_percent_b):
            return 0.0, ""

        # %B < 0: 下限割れ（買いシグナル）
        if bb_percent_b < 0:
            return 2.5, f"BB下限割れ(%B:{bb_percent_b:.2f})"
        elif bb_percent_b < 0.2:
            return 1.5, ""
        # %B > 1: 上限割れ（売りシグナル）
        elif bb_percent_b > 1:
            return -2.5, f"BB上限割れ(%B:{bb_percent_b:.2f})"
        elif bb_percent_b > 0.8:
            return -1.5, ""
        return 0.0, ""

    def _score_htf_alignment(
        self,
        buy_score: float,
        sell_score: float,
    ) -> tuple[float, str]:
        """上位時間足整合性スコアリング（ボーナスのみ版）

        HTF逆行ペナルティはBaseStrategy._calculate_htf_factor()が
        担当するため、ここではボーナスのみ付与する。
        """
        if not self._htf_data:
            return 0.0, ""

        aligned_count = 0
        dominant_direction = "BUY" if buy_score > sell_score else "SELL"

        current_time = self._current_eval_time

        for tf, df in self._htf_data.items():
            if df.empty:
                continue

            # 現在時刻基準でHTF行を取得
            latest = self._get_htf_row(df, current_time)
            if latest is None:
                continue

            sma_20 = latest.get("sma_20")
            sma_50 = latest.get("sma_50")
            close = latest.get("close")

            if any(pd.isna(v) for v in [sma_20, sma_50, close]
                   if v is not None):
                continue

            if close is None or sma_20 is None or sma_50 is None:
                continue

            # 上位足トレンド判定
            htf_bullish = close > sma_20 > sma_50
            htf_bearish = close < sma_20 < sma_50

            if dominant_direction == "BUY" and htf_bullish:
                aligned_count += 1
            elif dominant_direction == "SELL" and htf_bearish:
                aligned_count += 1

        # 整合性に応じたボーナスのみ
        if aligned_count >= 2:
            return 4.0, f"HTF強整合({aligned_count}TF)"
        elif aligned_count >= 1:
            return 2.0, f"HTF整合({aligned_count}TF)"
        return 0.0, ""

    def _get_htf_row(
        self,
        df: pd.DataFrame,
        current_time: pd.Timestamp | None,
    ) -> pd.Series | None:
        """HTFデータから現在時刻以前の最新行を取得

        Args:
            df: HTFデータフレーム
            current_time: 現在時刻

        Returns:
            pd.Series | None: 該当行
        """
        if df.empty:
            return None

        if current_time is None:
            return df.iloc[-1]

        import numpy as np

        ct = current_time.to_datetime64()

        # DatetimeIndexの場合
        if isinstance(df.index, pd.DatetimeIndex):
            time_arr = df.index.values
        elif "time" in df.columns:
            time_arr = df["time"].values
        elif "timestamp" in df.columns:
            time_arr = df["timestamp"].values
        else:
            return df.iloc[-1]

        # current_time以前の最新インデックスを取得
        idx = np.searchsorted(time_arr, ct, side="right") - 1
        if idx < 0:
            return None
        return df.iloc[idx]

    def _determine_direction(
        self,
        buy_score: float,
        sell_score: float,
    ) -> tuple[SignalType, float]:
        """方向と確度を決定

        Args:
            buy_score: 買いスコア
            sell_score: 売りスコア

        Returns:
            tuple[SignalType, float]: (方向, 確度)
        """
        min_score = self.MIN_SCORES.get(self.timeframe, 5.0)

        # 最小スコア未満はHOLD
        if max(buy_score, sell_score) < min_score:
            return SignalType.HOLD, 0.0

        # スコア差分の要件（方向性の明確さ）
        score_diff = abs(buy_score - sell_score)
        max_score = max(buy_score, sell_score)

        # 明確な方向性がない場合はHOLD
        if score_diff < min_score * 0.4:  # 厳しめ
            return SignalType.HOLD, 0.0

        # 確度計算
        confidence = min(score_diff / 15.0 + max_score / 30.0, 1.0)

        if buy_score > sell_score:
            return SignalType.BUY, confidence
        elif sell_score > buy_score:
            return SignalType.SELL, confidence
        return SignalType.HOLD, 0.0

    def _apply_noise_filter(
        self,
        row: pd.Series,
        direction: SignalType,
    ) -> SignalType:
        """短期足用ノイズフィルター

        M1/M5時間足の場合、ADXとボラティリティをチェックして
        ノイズシグナルをフィルタリングする。

        Args:
            row: データ行
            direction: 判定された方向

        Returns:
            SignalType: フィルター適用後の方向
        """
        # M1/M5以外は即座にパス
        if self.timeframe not in ["M1", "M5"]:
            return direction

        # HOLDはそのまま返す
        if direction == SignalType.HOLD:
            return direction

        # ADX最小値チェック（緩和: トレンドが弱くてもエントリー可能に）
        adx = row.get("adx")
        if adx is not None and not pd.isna(adx):
            min_adx = 10.0 if self.timeframe == "M1" else 8.0  # 緩和
            if adx < min_adx:
                return SignalType.HOLD

        # ボラティリティ範囲チェック（緩和）
        atr = row.get("atr_14")
        atr_ma = row.get("atr_ma_20")
        if atr is not None and atr_ma is not None:
            if not pd.isna(atr) and not pd.isna(atr_ma) and atr_ma > 0:
                atr_ratio = atr / atr_ma
                # 緩和: より広い範囲を許容
                if atr_ratio < 0.3 or atr_ratio > 3.5:
                    return SignalType.HOLD

        return direction

    def _calculate_sl_tp(
        self,
        row: pd.Series,
        strength: IndicatorStrength,
        plan: TradingPlan | None = None,
    ) -> tuple[float, float]:
        """ATRベースSL/TP計算

        SL距離のみ計算する。TP/SL比率の適用は
        BaseStrategy._calculate_sl_tp()が唯一のソースとする。
        ここではデフォルトTP = SL * 1.0（1:1）を暫定設定。

        Args:
            row: データ行
            strength: 指標強度
            plan: トレーディングプラン（オプション）

        Returns:
            tuple[float, float]: (SL pips, TP pips)
        """
        atr = row.get("atr_14")

        # SLマルチプライヤー（TF別ATR倍率）
        sl_multipliers = {
            "M1": 1.2,
            "M5": 1.3,
            "M15": 1.4,
            "H1": 1.5,
            "H4": 1.6,
            "D1": 1.8,
        }
        sl_mult = sl_multipliers.get(self.timeframe, 1.4)

        # ATRをpipsに変換
        if atr is not None and not pd.isna(atr):
            atr_pips = atr * 100
        else:
            atr_pips = 15.0

        # SL計算
        sl_pips = atr_pips * sl_mult

        # 最低/最大制限
        sl_pips = max(10.0, min(sl_pips, 50.0))

        # TF別デフォルトTP/SL比率（戦略のtp_sl_ratio_rangeで最終補正）
        _default_tp_ratios = {
            "M1": 1.2, "M5": 1.3, "M15": 1.4,
            "H1": 1.5, "H4": 1.6, "D1": 1.8,
        }
        tp_ratio = _default_tp_ratios.get(self.timeframe, 1.4)
        tp_pips = sl_pips * tp_ratio

        return sl_pips, tp_pips

    def _calculate_structure_based_sl(
        self,
        row: pd.Series,
        close: float | None,
        atr_pips: float,
        buffer_pips: float,
    ) -> float:
        """構造ベースのSL計算

        スイングポイントの外側にSLを設定。
        見つからない場合はATRベースにフォールバック。

        Args:
            row: データ行
            close: 現在価格
            atr_pips: ATR（pips）
            buffer_pips: バッファ（pips）

        Returns:
            float: SL（pips）
        """
        if close is None or pd.isna(close):
            return atr_pips * 1.5

        # 方向を判定（構造から）
        structure_direction = row.get("structure_direction", 0)
        last_swing_low = row.get("last_swing_low")
        last_swing_high = row.get("last_swing_high")

        # 買いの場合: スイングローの下にSL
        if structure_direction >= 0 and last_swing_low is not None:
            if not pd.isna(last_swing_low):
                sl_distance = abs(close - last_swing_low) * 100 + buffer_pips
                if 5.0 <= sl_distance <= 80.0:
                    return sl_distance

        # 売りの場合: スイングハイの上にSL
        if structure_direction <= 0 and last_swing_high is not None:
            if not pd.isna(last_swing_high):
                sl_distance = abs(last_swing_high - close) * 100 + buffer_pips
                if 5.0 <= sl_distance <= 80.0:
                    return sl_distance

        # フォールバック: ATRベース
        sl_mult = {
            "M1": 1.2,
            "M5": 1.3,
            "M15": 1.5,
            "H1": 1.8,
            "H4": 2.0,
            "D1": 2.5,
        }.get(self.timeframe, 1.5)

        return atr_pips * sl_mult

    def _calculate_liquidity_based_tp(
        self,
        row: pd.Series,
        close: float | None,
        sl_pips: float,
        min_rr_ratio: float,
        atr_pips: float,
    ) -> float:
        """流動性ベースのTP計算

        次の流動性ゾーンをTPターゲットとする。
        最低RR比を確保。

        Args:
            row: データ行
            close: 現在価格
            sl_pips: SL（pips）
            min_rr_ratio: 最低RR比
            atr_pips: ATR（pips）

        Returns:
            float: TP（pips）
        """
        if close is None or pd.isna(close):
            return sl_pips * min_rr_ratio

        structure_direction = row.get("structure_direction", 0)
        buy_side_liquidity = row.get("buy_side_liquidity")
        sell_side_liquidity = row.get("sell_side_liquidity")

        # 買いの場合: 上の流動性ゾーン（買い側流動性）がTP
        if structure_direction >= 0 and buy_side_liquidity is not None:
            if not pd.isna(buy_side_liquidity):
                tp_distance = abs(buy_side_liquidity - close) * 100
                if tp_distance >= sl_pips * min_rr_ratio:
                    return tp_distance

        # 売りの場合: 下の流動性ゾーン（売り側流動性）がTP
        if structure_direction <= 0 and sell_side_liquidity is not None:
            if not pd.isna(sell_side_liquidity):
                tp_distance = abs(close - sell_side_liquidity) * 100
                if tp_distance >= sl_pips * min_rr_ratio:
                    return tp_distance

        # フォールバック: RR比ベース
        return sl_pips * min_rr_ratio
