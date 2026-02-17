"""改良版シグナル生成器

勝率60%、収益率15%を目指した改良版。
価格アクション分析、厳格なMTFフィルター、
勢い確認強化を統合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np
import pandas as pd

from autotrader.core.enums import SignalType

if TYPE_CHECKING:
    from autotrader.core.entities import Candle, Signal
    from autotrader.core.enums import Timeframe


class TradeSetup(Enum):
    """トレードセットアップ種別"""

    NONE = "none"
    TREND_CONTINUATION = "trend_continuation"  # トレンド継続
    PULLBACK_ENTRY = "pullback_entry"  # 押し目/戻り
    REVERSAL = "reversal"  # 転換
    BREAKOUT = "breakout"  # ブレイクアウト
    DIVERGENCE = "divergence"  # ダイバージェンス


@dataclass(frozen=True)
class SignalQuality:
    """シグナル品質評価

    Attributes:
        score: 総合スコア（0-1）
        setup_type: セットアップ種別
        mtf_aligned: MTF一致度
        momentum_confirmed: 勢い確認
        pa_confirmed: 価格アクション確認
        reasons: 判断理由リスト
    """

    score: float
    setup_type: TradeSetup
    mtf_aligned: float
    momentum_confirmed: bool
    pa_confirmed: bool
    reasons: list[str]


@dataclass
class ImprovedSignalConfig:
    """改良版シグナル設定

    Attributes:
        min_score_threshold: 最小スコア閾値
        require_mtf_alignment: MTF一致必須
        require_momentum: 勢い確認必須
        require_pa_confirmation: 価格アクション確認必須
        adx_threshold: ADX閾値
        rsi_oversold: RSI売られすぎ
        rsi_overbought: RSI買われすぎ
        sl_atr_mult: SL ATR倍率
        tp_atr_mult: TP ATR倍率
        cooldown_minutes: クールダウン時間（分）
        max_spread_atr_ratio: 最大スプレッド/ATR比率
    """

    min_score_threshold: float = 0.55
    require_mtf_alignment: bool = True
    require_momentum: bool = True
    require_pa_confirmation: bool = False
    adx_threshold: float = 18.0
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5
    cooldown_minutes: int = 60
    max_spread_atr_ratio: float = 0.25

    @classmethod
    def for_h1(cls) -> "ImprovedSignalConfig":
        """H1用設定"""
        return cls(
            min_score_threshold=0.55,
            require_mtf_alignment=True,
            require_momentum=True,
            require_pa_confirmation=False,
            adx_threshold=18.0,
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            sl_atr_mult=1.5,
            tp_atr_mult=2.5,
            cooldown_minutes=60,
        )

    @classmethod
    def for_m15(cls) -> "ImprovedSignalConfig":
        """M15用設定"""
        return cls(
            min_score_threshold=0.60,
            require_mtf_alignment=True,
            require_momentum=True,
            require_pa_confirmation=True,
            adx_threshold=20.0,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            sl_atr_mult=1.2,
            tp_atr_mult=2.0,
            cooldown_minutes=30,
        )

    @classmethod
    def for_m5(cls) -> "ImprovedSignalConfig":
        """M5用設定"""
        return cls(
            min_score_threshold=0.65,
            require_mtf_alignment=True,
            require_momentum=True,
            require_pa_confirmation=True,
            adx_threshold=22.0,
            rsi_oversold=25.0,
            rsi_overbought=75.0,
            sl_atr_mult=1.0,
            tp_atr_mult=1.8,
            cooldown_minutes=15,
            max_spread_atr_ratio=0.20,
        )


class ImprovedSignalGenerator:
    """改良版シグナル生成器

    勝率向上のための厳格なフィルタリングを適用。

    改良ポイント:
    1. 複数時間足の厳格な一致確認
    2. 勢い（モメンタム）の確認強化
    3. 価格アクションパターンの統合
    4. ダイバージェンス検出の活用
    5. サポート/レジスタンス付近でのエントリー改善

    Args:
        config: シグナル設定
    """

    # 各要素の重み
    WEIGHTS = {
        "trend": 0.20,  # トレンド整列
        "mtf": 0.20,  # MTF一致
        "momentum": 0.20,  # モメンタム
        "rsi": 0.15,  # RSI
        "macd": 0.15,  # MACD
        "pa": 0.10,  # 価格アクション
    }

    def __init__(self, config: ImprovedSignalConfig | None = None) -> None:
        self.config = config or ImprovedSignalConfig()
        self._last_signal_time: datetime | None = None
        self._higher_tf_data: dict[str, pd.DataFrame] = {}

    def set_higher_tf_data(self, timeframe: str, df: pd.DataFrame) -> None:
        """上位足データを設定

        Args:
            timeframe: 時間足名
            df: データフレーム
        """
        self._higher_tf_data[timeframe] = df

    def reset(self) -> None:
        """状態をリセット"""
        self._last_signal_time = None

    def _get_higher_tf_trend(
        self,
        tf_name: str,
        current_time: datetime,
    ) -> tuple[str, float]:
        """上位足トレンドを取得

        Args:
            tf_name: 時間足名
            current_time: 現在時刻

        Returns:
            tuple[str, float]: (方向, 強度)
        """
        if tf_name not in self._higher_tf_data:
            return "neutral", 0.0

        df = self._higher_tf_data[tf_name]
        mask = df["time"] <= current_time
        if not mask.any():
            return "neutral", 0.0

        row = df[mask].iloc[-1]

        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        close = row.get("close")
        adx = row.get("adx", 25.0)

        if pd.isna(sma_20) or pd.isna(sma_50) or pd.isna(close):
            return "neutral", 0.0

        # 強度を計算（ADXベース）
        strength = min(1.0, (adx - 15) / 30) if not pd.isna(adx) else 0.5

        # 方向判定
        if close > sma_20 > sma_50:
            return "up", strength
        elif close > sma_20 and close > sma_50:
            return "up", strength * 0.8
        elif close < sma_20 < sma_50:
            return "down", strength
        elif close < sma_20 and close < sma_50:
            return "down", strength * 0.8

        return "neutral", 0.0

    def _calculate_trend_score(
        self,
        row: pd.Series,
        candle: "Candle",
    ) -> tuple[float, float, str]:
        """トレンドスコアを計算

        Args:
            row: 指標付きデータ行
            candle: 現在のキャンドル

        Returns:
            tuple[float, float, str]: (買いスコア, 売りスコア, 方向)
        """
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        adx = row.get("adx", 25.0)

        if pd.isna(sma_20) or pd.isna(sma_50):
            return 0.0, 0.0, "neutral"

        # ADX閾値チェック
        if adx is not None and not pd.isna(adx):
            if adx < self.config.adx_threshold:
                return 0.0, 0.0, "neutral"

        # トレンド方向判定
        if candle.close > sma_20 > sma_50:
            score = min(1.0, (adx - 15) / 25) if adx else 0.6
            return score, 0.0, "up"
        elif candle.close < sma_20 < sma_50:
            score = min(1.0, (adx - 15) / 25) if adx else 0.6
            return 0.0, score, "down"

        return 0.0, 0.0, "neutral"

    def _calculate_mtf_score(
        self,
        candle: "Candle",
        base_direction: str,
    ) -> tuple[float, float, float]:
        """MTFスコアを計算

        Args:
            candle: 現在のキャンドル
            base_direction: ベース時間足の方向

        Returns:
            tuple[float, float, float]: (買いスコア, 売りスコア, 一致度)
        """
        if not self._higher_tf_data:
            return 0.0, 0.0, 0.0

        aligned_count = 0
        total_count = 0
        directions = []

        for tf_name in ["H4", "D1"]:
            direction, strength = self._get_higher_tf_trend(
                tf_name, candle.time
            )
            if direction != "neutral":
                total_count += 1
                directions.append(direction)
                if direction == base_direction:
                    aligned_count += 1

        if total_count == 0:
            return 0.0, 0.0, 0.0

        alignment = aligned_count / total_count

        # 全て同じ方向の場合のみ高スコア
        if aligned_count == total_count and total_count >= 2:
            if base_direction == "up":
                return alignment, 0.0, alignment
            else:
                return 0.0, alignment, alignment

        # 部分一致
        up_count = sum(1 for d in directions if d == "up")
        down_count = sum(1 for d in directions if d == "down")

        if up_count > down_count:
            return alignment * 0.5, 0.0, alignment
        elif down_count > up_count:
            return 0.0, alignment * 0.5, alignment

        return 0.0, 0.0, 0.0

    def _calculate_momentum_score(
        self,
        row: pd.Series,
    ) -> tuple[float, float, bool]:
        """モメンタムスコアを計算

        Args:
            row: 指標付きデータ行

        Returns:
            tuple[float, float, bool]: (買いスコア, 売りスコア, 確認フラグ)
        """
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        macd_hist = row.get("macd_histogram")
        macd_hist_slope = row.get("macd_hist_slope")
        stoch_k = row.get("stoch_k")
        stoch_d = row.get("stoch_d")

        buy_score = 0.0
        sell_score = 0.0
        confirmed = False

        # MACD判定
        if not pd.isna(macd) and not pd.isna(macd_signal):
            if macd > macd_signal:
                buy_score += 0.3
                # ヒストグラム上昇中なら追加
                if macd_hist_slope is not None and macd_hist_slope > 0:
                    buy_score += 0.2
                    confirmed = True
            elif macd < macd_signal:
                sell_score += 0.3
                if macd_hist_slope is not None and macd_hist_slope < 0:
                    sell_score += 0.2
                    confirmed = True

            # ゼロライン上/下でボーナス
            if macd > 0 and buy_score > 0:
                buy_score += 0.1
            elif macd < 0 and sell_score > 0:
                sell_score += 0.1

        # ストキャスティクス判定
        if not pd.isna(stoch_k) and not pd.isna(stoch_d):
            if stoch_k < 30 and stoch_k > stoch_d:
                buy_score += 0.2
            elif stoch_k > 70 and stoch_k < stoch_d:
                sell_score += 0.2

        return min(buy_score, 1.0), min(sell_score, 1.0), confirmed

    def _calculate_rsi_score(
        self,
        row: pd.Series,
    ) -> tuple[float, float]:
        """RSIスコアを計算

        Args:
            row: 指標付きデータ行

        Returns:
            tuple[float, float]: (買いスコア, 売りスコア)
        """
        rsi = row.get("rsi_14")

        if rsi is None or pd.isna(rsi):
            return 0.0, 0.0

        # 売られすぎ
        if rsi < self.config.rsi_oversold:
            score = (self.config.rsi_oversold - rsi) / 20
            return min(score, 1.0), 0.0

        # 買われすぎ
        if rsi > self.config.rsi_overbought:
            score = (rsi - self.config.rsi_overbought) / 20
            return 0.0, min(score, 1.0)

        # 中間域（50付近は中立）
        if 40 <= rsi <= 60:
            return 0.0, 0.0

        # 中間～閾値
        if rsi < 40:
            return 0.2, 0.0
        else:
            return 0.0, 0.2

    def _calculate_macd_score(
        self,
        row: pd.Series,
    ) -> tuple[float, float]:
        """MACDスコアを計算

        Args:
            row: 指標付きデータ行

        Returns:
            tuple[float, float]: (買いスコア, 売りスコア)
        """
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        macd_hist = row.get("macd_histogram")

        if any(pd.isna(v) for v in [macd, macd_signal, macd_hist]):
            return 0.0, 0.0

        # MACDとシグナルのクロス
        buy_score = 0.0
        sell_score = 0.0

        if macd > macd_signal:
            buy_score = 0.4
            if macd_hist > 0:
                buy_score += 0.3
            if macd > 0:
                buy_score += 0.2
        elif macd < macd_signal:
            sell_score = 0.4
            if macd_hist < 0:
                sell_score += 0.3
            if macd < 0:
                sell_score += 0.2

        return min(buy_score, 1.0), min(sell_score, 1.0)

    def _calculate_pa_score(
        self,
        row: pd.Series,
    ) -> tuple[float, float, bool]:
        """価格アクションスコアを計算

        Args:
            row: 指標付きデータ行

        Returns:
            tuple[float, float, bool]: (買いスコア, 売りスコア, 確認フラグ)
        """
        pa_bullish = row.get("pa_bullish_score", 0.0)
        pa_bearish = row.get("pa_bearish_score", 0.0)
        pattern = row.get("candle_pattern", "none")
        at_support = row.get("at_support", False)
        at_resistance = row.get("at_resistance", False)

        if pd.isna(pa_bullish):
            pa_bullish = 0.0
        if pd.isna(pa_bearish):
            pa_bearish = 0.0

        buy_score = pa_bullish
        sell_score = pa_bearish

        # S/R付近でボーナス
        if at_support and buy_score > 0:
            buy_score *= 1.3
        if at_resistance and sell_score > 0:
            sell_score *= 1.3

        confirmed = (buy_score > 0.2) or (sell_score > 0.2)

        return min(buy_score, 1.0), min(sell_score, 1.0), confirmed

    def _check_divergence(
        self,
        row: pd.Series,
    ) -> tuple[float, float]:
        """ダイバージェンスを確認

        Args:
            row: 指標付きデータ行

        Returns:
            tuple[float, float]: (買いボーナス, 売りボーナス)
        """
        is_bullish_div = row.get("is_bullish_div", False)
        is_bearish_div = row.get("is_bearish_div", False)

        if is_bullish_div:
            return 0.2, 0.0
        if is_bearish_div:
            return 0.0, 0.2

        return 0.0, 0.0

    def _is_cooldown_active(self, current_time: datetime) -> bool:
        """クールダウン中かどうか確認

        Args:
            current_time: 現在時刻

        Returns:
            bool: クールダウン中の場合True
        """
        if self._last_signal_time is None:
            return False

        cooldown_delta = timedelta(minutes=self.config.cooldown_minutes)
        return current_time < self._last_signal_time + cooldown_delta

    def evaluate_quality(
        self,
        row: pd.Series,
        candle: "Candle",
    ) -> SignalQuality:
        """シグナル品質を評価

        Args:
            row: 指標付きデータ行
            candle: 現在のキャンドル

        Returns:
            SignalQuality: シグナル品質
        """
        reasons = []

        # トレンドスコア
        trend_buy, trend_sell, trend_dir = self._calculate_trend_score(
            row, candle
        )

        # MTFスコア
        mtf_buy, mtf_sell, mtf_alignment = self._calculate_mtf_score(
            candle, trend_dir
        )

        # モメンタムスコア
        mom_buy, mom_sell, mom_confirmed = self._calculate_momentum_score(row)

        # RSIスコア
        rsi_buy, rsi_sell = self._calculate_rsi_score(row)

        # MACDスコア
        macd_buy, macd_sell = self._calculate_macd_score(row)

        # 価格アクションスコア
        pa_buy, pa_sell, pa_confirmed = self._calculate_pa_score(row)

        # ダイバージェンスボーナス
        div_buy, div_sell = self._check_divergence(row)

        # 総合スコア計算
        buy_score = (
            trend_buy * self.WEIGHTS["trend"]
            + mtf_buy * self.WEIGHTS["mtf"]
            + mom_buy * self.WEIGHTS["momentum"]
            + rsi_buy * self.WEIGHTS["rsi"]
            + macd_buy * self.WEIGHTS["macd"]
            + pa_buy * self.WEIGHTS["pa"]
            + div_buy * 0.1  # ボーナス
        )

        sell_score = (
            trend_sell * self.WEIGHTS["trend"]
            + mtf_sell * self.WEIGHTS["mtf"]
            + mom_sell * self.WEIGHTS["momentum"]
            + rsi_sell * self.WEIGHTS["rsi"]
            + macd_sell * self.WEIGHTS["macd"]
            + pa_sell * self.WEIGHTS["pa"]
            + div_sell * 0.1
        )

        # セットアップ種別判定
        setup_type = TradeSetup.NONE
        if div_buy > 0 or div_sell > 0:
            setup_type = TradeSetup.DIVERGENCE
            reasons.append("ダイバージェンス検出")
        elif trend_dir != "neutral" and mtf_alignment >= 0.8:
            setup_type = TradeSetup.TREND_CONTINUATION
            reasons.append("トレンド継続")
        elif pa_buy > 0.2 or pa_sell > 0.2:
            at_support = row.get("at_support", False)
            at_resistance = row.get("at_resistance", False)
            if at_support or at_resistance:
                setup_type = TradeSetup.PULLBACK_ENTRY
                reasons.append("押し目/戻りエントリー")
            else:
                setup_type = TradeSetup.REVERSAL
                reasons.append("転換シグナル")

        # 方向決定
        if buy_score > sell_score:
            final_score = buy_score
            if trend_dir == "up":
                reasons.append(f"上昇トレンド(強度{trend_buy:.2f})")
            if mtf_alignment > 0:
                reasons.append(f"MTF一致({mtf_alignment:.0%})")
            if mom_confirmed:
                reasons.append("モメンタム確認")
        elif sell_score > buy_score:
            final_score = sell_score
            if trend_dir == "down":
                reasons.append(f"下降トレンド(強度{trend_sell:.2f})")
            if mtf_alignment > 0:
                reasons.append(f"MTF一致({mtf_alignment:.0%})")
            if mom_confirmed:
                reasons.append("モメンタム確認")
        else:
            final_score = 0.0

        return SignalQuality(
            score=final_score,
            setup_type=setup_type,
            mtf_aligned=mtf_alignment,
            momentum_confirmed=mom_confirmed,
            pa_confirmed=pa_confirmed,
            reasons=reasons,
        )

    def generate(
        self,
        row: pd.Series,
        candle: "Candle",
        symbol: str,
        timeframe: "Timeframe",
    ) -> "Signal | None":
        """シグナルを生成

        Args:
            row: 指標付きデータ行
            candle: 現在のキャンドル
            symbol: シンボル
            timeframe: 時間足

        Returns:
            Signal | None: シグナル
        """
        from autotrader.core.entities import Signal

        # クールダウンチェック
        if self._is_cooldown_active(candle.time):
            return None

        # 品質評価
        quality = self.evaluate_quality(row, candle)

        # 閾値チェック
        if quality.score < self.config.min_score_threshold:
            return None

        # MTF一致必須チェック
        if self.config.require_mtf_alignment:
            if quality.mtf_aligned < 0.5:
                return None

        # モメンタム確認必須チェック
        if self.config.require_momentum:
            if not quality.momentum_confirmed:
                return None

        # 価格アクション確認必須チェック
        if self.config.require_pa_confirmation:
            if not quality.pa_confirmed:
                return None

        # トレンド方向判定
        trend_buy, trend_sell, trend_dir = self._calculate_trend_score(
            row, candle
        )

        # シグナル方向決定
        mtf_buy, mtf_sell, _ = self._calculate_mtf_score(candle, trend_dir)
        mom_buy, mom_sell, _ = self._calculate_momentum_score(row)

        total_buy = trend_buy + mtf_buy + mom_buy
        total_sell = trend_sell + mtf_sell + mom_sell

        if total_buy > total_sell:
            signal_type = SignalType.BUY
        elif total_sell > total_buy:
            signal_type = SignalType.SELL
        else:
            return None

        # SL/TP計算
        atr = row.get("atr_14", 0.5)
        if pd.isna(atr) or atr <= 0:
            atr = 0.5

        if signal_type == SignalType.BUY:
            stop_loss = candle.close - atr * self.config.sl_atr_mult
            take_profit = candle.close + atr * self.config.tp_atr_mult
        else:
            stop_loss = candle.close + atr * self.config.sl_atr_mult
            take_profit = candle.close - atr * self.config.tp_atr_mult

        # クールダウン更新
        self._last_signal_time = candle.time

        return Signal(
            signal_id=str(uuid4()),
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            confidence=quality.score,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=f"{quality.setup_type.value}: " + ", ".join(quality.reasons),
            created_at=candle.time,
        )


@dataclass
class MultiTimeframeSignalGenerator:
    """複数時間足対応シグナル生成器

    H1、M15、M5など複数の時間足で一貫した
    シグナル生成を行う。

    Attributes:
        generators: 時間足別ジェネレータ辞書
    """

    generators: dict[str, ImprovedSignalGenerator] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        # デフォルトのジェネレータを設定
        if not self.generators:
            self.generators = {
                "H1": ImprovedSignalGenerator(
                    ImprovedSignalConfig.for_h1()
                ),
                "M15": ImprovedSignalGenerator(
                    ImprovedSignalConfig.for_m15()
                ),
                "M5": ImprovedSignalGenerator(
                    ImprovedSignalConfig.for_m5()
                ),
            }

    def set_higher_tf_data(
        self,
        timeframe: str,
        higher_tf: str,
        df: pd.DataFrame,
    ) -> None:
        """上位足データを設定

        Args:
            timeframe: エントリー時間足
            higher_tf: 上位足名
            df: データフレーム
        """
        if timeframe in self.generators:
            self.generators[timeframe].set_higher_tf_data(higher_tf, df)

    def generate(
        self,
        timeframe: str,
        row: pd.Series,
        candle: "Candle",
        symbol: str,
        tf_enum: "Timeframe",
    ) -> "Signal | None":
        """シグナルを生成

        Args:
            timeframe: 時間足名
            row: 指標付きデータ行
            candle: 現在のキャンドル
            symbol: シンボル
            tf_enum: 時間足Enum

        Returns:
            Signal | None: シグナル
        """
        if timeframe not in self.generators:
            return None

        return self.generators[timeframe].generate(
            row, candle, symbol, tf_enum
        )

    def reset(self) -> None:
        """全ジェネレータをリセット"""
        for gen in self.generators.values():
            gen.reset()
