"""ポジションサイザーモジュール

リスク量に応じたロット数を動的に算出する。
資金管理機能を統合し、資金ショートを防止する。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.enums import MarketRegime
from autotrader.core.interfaces.position_sizing import (
    PositionSizerProtocol,
    SizingContext,
    SizingResult,
)

# クォート通貨別pip_value（1ロット=100,000通貨、JPY建て口座）
_SIZER_PIP_VALUE_BY_QUOTE: dict[str, float] = {
    "JPY": 1000.0,   # XXXJPY: 0.01×100,000=1000JPY（正確値）
    "USD": 1500.0,   # XXXUSD: 0.0001×100,000×150JPY/USD（概算）
    "EUR": 1600.0,   # XXXEUR: 0.0001×100,000×160JPY/EUR（概算）
    "GBP": 1900.0,   # XXXGBP: 0.0001×100,000×190JPY/GBP（概算）
    "AUD": 1000.0,   # XXXAUD: 0.0001×100,000×100JPY/AUD（概算）
    "NZD": 900.0,    # XXXNZD: 0.0001×100,000×90JPY/NZD（概算）
    "CAD": 1100.0,   # XXXCAD: 0.0001×100,000×110JPY/CAD（概算）
    "CHF": 1650.0,   # XXXCHF: 0.0001×100,000×165JPY/CHF（概算）
}


def _sizer_pip_value(symbol: str) -> float:
    """シンボルからポジションサイザー用pip_valueを計算

    Args:
        symbol: 通貨ペアシンボル（例: USDJPY, EURUSD）

    Returns:
        float: 1pip=1ロットあたりのJPY価値
    """
    if len(symbol) >= 6:
        quote = symbol[-3:].upper()
        return _SIZER_PIP_VALUE_BY_QUOTE.get(quote, 1000.0)
    return 1000.0


@dataclass(frozen=True)
class PositionSizerConfig:
    """ポジションサイザー設定

    Attributes:
        symbol: 通貨ペアシンボル（pip_value自動計算に使用）
        base_risk_pct: 基本リスク率（資金に対する%）
        pip_value: 1pipあたりの価値（0=シンボルから自動計算）
        min_lot: 最小ロット数
        max_lot: 最大ロット数（ブローカー上限）
        confidence_high_threshold: 高確度閾値
        confidence_low_threshold: 低確度閾値
        dd_reduction_threshold: DD本格減額開始閾値
        dd_max_reduction: DD最大減額率
        dd_early_threshold: DD早期減額開始閾値
        consecutive_loss_start: 連敗減額開始数
        consecutive_loss_max: 連敗最大減額数
        consecutive_loss_min_adjust: 連敗最大減額時の調整係数
        slippage_buffer_pips: SLスリッページバッファ（pips）
        max_lot_per_trade: 1トレードあたりの上限ロット
        max_risk_pct_absolute: 1トレードあたりの絶対最大リスク率
        equity_floor_pct: 取引停止の資金下限率（初期資金比）
        equity_caution_pct: ロット減額開始の資金注意率（初期資金比）
        max_total_exposure_lot: 合計オープンロット上限
        max_same_direction_ratio: 同方向エクスポージャー比率上限
    """

    symbol: str = ""              # 通貨ペアシンボル（pip_value自動計算用）
    base_risk_pct: float = 0.025  # 2.5%リスク（2025年DD対策）
    pip_value: float = 0.0       # 0=シンボルから自動計算
    min_lot: float = 0.01
    max_lot: float = 10.0
    confidence_high_threshold: float = 0.7
    confidence_low_threshold: float = 0.5
    dd_reduction_threshold: float = 0.015  # 1.5%DD本格減額（強化）
    dd_max_reduction: float = 0.5         # 最大50%減額（強化）
    dd_early_threshold: float = 0.008    # 0.8%DD早期減額開始（強化）
    consecutive_loss_start: int = 2       # 2連敗から減額開始（強化）
    consecutive_loss_max: int = 5         # 5連敗で最大減額（強化）
    consecutive_loss_min_adjust: float = 0.2  # 最大減額時0.2x（強化）
    slippage_buffer_pips: float = 2.0     # SLスリッページバッファ
    # 資金管理パラメータ
    max_lot_per_trade: float = 2.5        # 1トレード上限2.5ロット（強化）
    max_risk_pct_absolute: float = 0.07   # 絶対最大7%リスク
    equity_floor_pct: float = 0.30        # 初期資金の30%で取引停止
    equity_caution_pct: float = 0.50      # 初期資金の50%で減額開始
    max_total_exposure_lot: float = 4.0   # 合計4ロット上限（強化）
    max_same_direction_ratio: float = 0.6 # 同方向は全体の60%まで


class PositionSizer(PositionSizerProtocol):
    """ポジションサイザー

    lot = (equity × risk_pct × risk_adjust)
          / ((sl_pips + slippage_buffer) × pip_value)

    リスク調整係数は以下の要素で決定:
    - 確度調整: 0.7以上→1.2倍、0.5以下→0.5倍
    - レジーム調整: TREND=1.0, RANGE=0.7, HIGH_VOL=0.5, LOW_VOL=0.8
    - DD調整: 1%から緩やかに開始、2%超で本格減額（2025年強化）
    - 連敗調整: 2連敗から段階的に減額（5連敗で0.2x）（2025年強化）

    資金管理:
    - 資金下限（初期の30%）で取引停止
    - 資金注意（初期の50%→30%）で段階的減額
    - SLスリッページバッファ（デフォルト2pips）
    - リスクベース動的ロット上限
    - 同方向エクスポージャー制限（全体の60%）
    - 合計オープンロット上限
    - 絶対最大リスク率
    """

    # レジーム別リスク係数
    REGIME_MULTIPLIERS: dict[MarketRegime, float] = {
        MarketRegime.TREND: 1.0,
        MarketRegime.RANGE: 0.7,
        MarketRegime.HIGH_VOL: 0.5,
        MarketRegime.LOW_VOL: 0.8,
    }

    def __init__(self, config: PositionSizerConfig | None = None) -> None:
        """初期化

        Args:
            config: サイザー設定（Noneの場合はデフォルト）
        """
        self.config = config or PositionSizerConfig()
        # pip_value=0の場合はシンボルから自動計算
        self._pip_value: float = (
            self.config.pip_value
            if self.config.pip_value > 0
            else _sizer_pip_value(self.config.symbol)
        )

    def calculate(self, context: SizingContext) -> SizingResult:
        """ロット数を計算

        Args:
            context: サイジングコンテキスト

        Returns:
            SizingResult: サイジング結果
        """
        # 資金下限チェック（取引停止）
        equity_ratio = (
            context.equity / context.initial_equity
            if context.initial_equity > 0
            else 1.0
        )
        if equity_ratio <= self.config.equity_floor_pct:
            return SizingResult(
                lot=0.0,
                risk_budget=0.0,
                risk_adjust=0.0,
                reasoning=(
                    f"取引停止: 資金{equity_ratio:.0%}"
                    f"<下限{self.config.equity_floor_pct:.0%}"
                ),
                blocked=True,
            )

        # 合計エクスポージャーチェック
        remaining_lot = (
            self.config.max_total_exposure_lot
            - context.open_exposure_lot
        )
        if remaining_lot <= self.config.min_lot:
            return SizingResult(
                lot=0.0,
                risk_budget=0.0,
                risk_adjust=0.0,
                reasoning=(
                    f"取引停止: 合計ロット"
                    f"{context.open_exposure_lot:.2f}"
                    f"≥上限{self.config.max_total_exposure_lot:.1f}"
                ),
                blocked=True,
            )

        # 同方向エクスポージャー制限
        max_same_dir = (
            self.config.max_total_exposure_lot
            * self.config.max_same_direction_ratio
        )
        remaining_same_dir = (
            max_same_dir - context.open_same_direction_lot
        )
        if remaining_same_dir <= self.config.min_lot:
            return SizingResult(
                lot=0.0,
                risk_budget=0.0,
                risk_adjust=0.0,
                reasoning=(
                    f"取引停止: 同方向ロット"
                    f"{context.open_same_direction_lot:.2f}"
                    f"≥上限{max_same_dir:.1f}"
                ),
                blocked=True,
            )

        # リスク調整係数を計算
        risk_adjust, reasons = self._calculate_risk_adjust(context)

        # 資金注意域で段階的減額（caution→floorで1.0→0.25）
        caution_adjust = self._calculate_caution_adjust(equity_ratio)
        if caution_adjust < 1.0:
            risk_adjust *= caution_adjust
            reasons.append(f"注意域{caution_adjust:.2f}x")

        # リスク予算を計算
        risk_budget = (
            context.equity * self.config.base_risk_pct * risk_adjust
        )

        # 絶対最大リスク率で制限
        max_risk_budget = (
            context.equity * self.config.max_risk_pct_absolute
        )
        if risk_budget > max_risk_budget:
            risk_budget = max_risk_budget
            reasons.append(
                f"絶対上限{self.config.max_risk_pct_absolute:.0%}"
            )

        # SLが0の場合はエラー防止
        if context.sl_pips <= 0:
            return SizingResult(
                lot=self.config.min_lot,
                risk_budget=risk_budget,
                risk_adjust=risk_adjust,
                reasoning="SL距離が0のため最小ロット適用",
            )

        # ロット数を計算（SLスリッページバッファ込み）
        effective_sl = (
            context.sl_pips + self.config.slippage_buffer_pips
        )
        lot = risk_budget / (effective_sl * self._pip_value)

        # 静的1トレード上限ロット制限
        lot = min(lot, self.config.max_lot_per_trade)

        # リスクベース動的ロット上限
        max_lot_from_risk = (
            context.equity * self.config.max_risk_pct_absolute
        ) / (effective_sl * self._pip_value)
        lot = min(lot, max_lot_from_risk)

        # 合計エクスポージャー制限
        lot = min(lot, remaining_lot)

        # 同方向エクスポージャー制限
        lot = min(lot, remaining_same_dir)

        # ブローカー上限・下限
        lot = max(self.config.min_lot, min(self.config.max_lot, lot))

        # 小数点以下2桁に丸め
        lot = round(lot, 2)

        reasoning = (
            f"調整係数={risk_adjust:.2f} ({', '.join(reasons)})"
        )

        return SizingResult(
            lot=lot,
            risk_budget=risk_budget,
            risk_adjust=risk_adjust,
            reasoning=reasoning,
        )

    def _calculate_caution_adjust(
        self, equity_ratio: float
    ) -> float:
        """資金注意域の段階的減額係数

        100%→caution_pct: フルサイズ（1.0x）
        caution_pct→floor_pct: 線形減衰（1.0x→0.25x）

        Args:
            equity_ratio: 現在資金/初期資金の比率

        Returns:
            float: 減額係数（0.25〜1.0）
        """
        if equity_ratio > self.config.equity_caution_pct:
            return 1.0
        # caution→floorで1.0→0.25の線形補間
        caution_range = (
            self.config.equity_caution_pct
            - self.config.equity_floor_pct
        )
        if caution_range <= 0:
            return 0.25
        ratio = (
            equity_ratio - self.config.equity_floor_pct
        ) / caution_range
        ratio = max(0.0, min(1.0, ratio))
        return 0.25 + ratio * 0.75

    def _calculate_risk_adjust(
        self, context: SizingContext
    ) -> tuple[float, list[str]]:
        """リスク調整係数を計算

        Args:
            context: サイジングコンテキスト

        Returns:
            tuple[float, list[str]]: (調整係数, 理由リスト)
        """
        adjust = 1.0
        reasons: list[str] = []

        # 確度調整
        conf_adjust = self._calculate_confidence_adjust(
            context.confidence
        )
        adjust *= conf_adjust
        reasons.append(f"確度{conf_adjust:.1f}x")

        # レジーム調整
        regime_adjust = self.REGIME_MULTIPLIERS.get(
            context.regime, 1.0
        )
        adjust *= regime_adjust
        reasons.append(
            f"{context.regime.value}{regime_adjust:.1f}x"
        )

        # DD調整（平滑化）
        dd_adjust = self._calculate_dd_adjust(
            context.current_dd_pct
        )
        adjust *= dd_adjust
        if dd_adjust < 1.0:
            reasons.append(f"DD{dd_adjust:.2f}x")

        # 連敗調整（段階的）
        loss_adjust = self._calculate_consecutive_loss_adjust(
            context.consecutive_losses
        )
        adjust *= loss_adjust
        if loss_adjust < 1.0:
            reasons.append(f"連敗{loss_adjust:.2f}x")

        # ファンダメンタル調整（流動性・ボラティリティ）
        fund_adjust = self._calculate_fundamental_adjust(
            context.liquidity_factor,
            context.volatility_multiplier,
        )
        adjust *= fund_adjust
        if fund_adjust < 1.0:
            reasons.append(f"ファンダ{fund_adjust:.2f}x")

        # 最終調整値を0.1〜2.0に制限
        adjust = max(0.1, min(2.0, adjust))

        return adjust, reasons

    def _calculate_fundamental_adjust(
        self,
        liquidity_factor: float,
        volatility_multiplier: float,
    ) -> float:
        """ファンダメンタル要因によるリスク調整

        低流動性・高ボラティリティ時にロットを縮小する。

        Args:
            liquidity_factor: 流動性係数 (1.0=通常)
            volatility_multiplier: ボラ倍率 (1.0=通常)

        Returns:
            float: 調整係数 (0.5~1.0)
        """
        adjust = 1.0

        # 流動性が低い場合（<0.5）にロット縮小
        if liquidity_factor < 0.5:
            # 0.5 + liquidity_factor → 0.5~1.0
            adjust *= 0.5 + liquidity_factor

        # 高ボラティリティ時（>1.5）にロット縮小
        if volatility_multiplier > 1.5:
            adjust *= 0.8

        return adjust

    def _calculate_confidence_adjust(
        self, confidence: float
    ) -> float:
        """確度に基づく調整係数（区分線形）

        confidence = score/threshold（上限なし）で算出された値を
        区分線形関数でロット係数に変換する。

        しきい値未満（confidence < 1.0）:
            lot_mult = 0.3 + confidence * 0.7
            → confidence=0.0: 0.3x, confidence=1.0: 1.0x

        しきい値以上（confidence >= 1.0）:
            lot_mult = 1.0 + min(confidence - 1.0, 1.0) * 0.5
            → confidence=1.0: 1.0x, confidence=2.0: 1.5x（上限）

        Args:
            confidence: シグナル確度（0〜2以上、上限なし）

        Returns:
            float: 調整係数（0.3〜1.5）
        """
        if confidence >= 1.0:
            # しきい値以上：最大+50%増し（confidence=2.0で上限）
            return 1.0 + min(confidence - 1.0, 1.0) * 0.5
        else:
            # しきい値未満：0.3x〜1.0xで線形補間
            return 0.3 + confidence * 0.7  # 0.5〜1.2の範囲

    def _calculate_dd_adjust(self, current_dd_pct: float) -> float:
        """ドローダウンに基づく調整係数（2025年強化版）

        1%以下: 1.0（減額なし）
        1%→2%: 早期減額（1.0→0.7）
        2%超: 本格減額（0.7→最小値）

        Args:
            current_dd_pct: 現在のドローダウン率（0-1）

        Returns:
            float: 調整係数
        """
        early = self.config.dd_early_threshold
        main = self.config.dd_reduction_threshold

        # 早期閾値以下は減額なし
        if current_dd_pct <= early:
            return 1.0

        # 早期減額域（early→mainで1.0→0.7）
        if current_dd_pct <= main:
            early_range = main - early
            if early_range <= 0:
                return 1.0
            ratio = (current_dd_pct - early) / early_range
            return 1.0 - ratio * 0.3

        # 本格減額域（main超過→最大減額0.5）
        excess = current_dd_pct - main
        max_excess = 0.005  # 2%DDで最大減額（強化）
        reduction = (
            min(excess / max_excess, 1.0)
            * self.config.dd_max_reduction
        )
        return 0.7 - reduction * (0.7 - 0.5) / self.config.dd_max_reduction

    def _calculate_consecutive_loss_adjust(
        self, consecutive_losses: int
    ) -> float:
        """連敗数に基づく調整係数（段階的版）

        start未満: 1.0
        start→max: 線形減額（1.0→min_adjust）
        max以上: min_adjust

        Args:
            consecutive_losses: 連敗数

        Returns:
            float: 調整係数
        """
        start = self.config.consecutive_loss_start
        max_losses = self.config.consecutive_loss_max
        min_adj = self.config.consecutive_loss_min_adjust

        if consecutive_losses < start:
            return 1.0
        if consecutive_losses >= max_losses:
            return min_adj

        # start→maxで1.0→min_adjの線形補間
        loss_range = max_losses - start
        if loss_range <= 0:
            return min_adj
        ratio = (consecutive_losses - start) / loss_range
        return 1.0 - ratio * (1.0 - min_adj)
