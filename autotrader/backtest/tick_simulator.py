"""バックテスト用ティックシミュレーター

エントリー最適化: M1足の OHLC+SPREAD で TickEntryOptimizer 互換スコアリング
エグジット精密化: ティックデータ（bid/ask）で SL/TP ヒット順序を判定
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from autotrader.core.enums import ExitReason, SignalType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickSimConfig:
    """ティックシミュレーション設定

    TickEntryConfig（ライブ用）と同等のパラメータを持つ。

    Attributes:
        enabled: M1ベースティックシミュレーション有効化
        window_minutes: エントリー探索ウィンドウ（分）
        composite_threshold: 総合スコア閾値
        spread_weight: スプレッド条件の重み
        momentum_weight: モメンタム条件の重み
        retracement_weight: リトレースメント条件の重み
        retracement_enabled: リトレース評価の有効/無効
        timeout_execute: タイムアウト時に強制約定するか
        spread_threshold_pips: スプレッド閾値（pips）
    """

    enabled: bool = False
    window_minutes: int = 15
    composite_threshold: float = 0.6
    spread_weight: float = 0.4
    momentum_weight: float = 0.35
    retracement_weight: float = 0.25
    retracement_enabled: bool = False
    timeout_execute: bool = True
    spread_threshold_pips: float = 1.5


@dataclass(frozen=True)
class TickSimResult:
    """ティックシミュレーション結果

    Attributes:
        entry_price: 最適エントリー価格（mid）
        spread_pips: その時点のスプレッド（pips）
        composite_score: 成立時の総合スコア
        entry_time: エントリーしたM1足の時刻
        is_timeout: タイムアウト約定かどうか
        bars_scanned: スキャンしたM1足数
    """

    entry_price: float
    spread_pips: float
    composite_score: float
    entry_time: pd.Timestamp
    is_timeout: bool
    bars_scanned: int


class BacktestTickSimulator:
    """M1データを使ったエントリー最適化シミュレーター

    リアルの TickEntryOptimizer.evaluate_conditions() と
    同等のスコアリングを M1 OHLC+SPREAD で再現する。
    """

    def __init__(
        self,
        config: TickSimConfig,
        symbol: str,
    ) -> None:
        self._config = config
        self._symbol = symbol
        self._is_jpy = "JPY" in symbol.upper()
        self._pip_unit = 0.01 if self._is_jpy else 0.0001

    def find_optimal_entry(
        self,
        signal_type: SignalType,
        signal_time: pd.Timestamp,
        m1_df: pd.DataFrame,
    ) -> TickSimResult | None:
        """M1ウィンドウ内の最適エントリーポイントを探索

        Args:
            signal_type: BUY or SELL
            signal_time: シグナル発火時刻（M15足の時刻）
            m1_df: M1 DataFrame（DatetimeIndex、OHLC+SPREAD列）

        Returns:
            TickSimResult: 最適エントリー結果。M1データ不足時は None
        """
        # シグナル時刻から探索ウィンドウのM1足を抽出
        window_end = signal_time + pd.Timedelta(
            minutes=self._config.window_minutes
        )
        mask = (m1_df.index >= signal_time) & (
            m1_df.index < window_end
        )
        window = m1_df.loc[mask]

        if len(window) < 2:
            return None

        # M1足を順にスキャンし、累積擬似ティックでスコアリング
        pseudo_ticks: list[dict] = []
        for i in range(len(window)):
            row = window.iloc[i]
            row_ticks = self._m1_to_pseudo_ticks(
                row, signal_type
            )
            pseudo_ticks.extend(row_ticks)

            # 最低4ティック（1M1足分）蓄積してから評価開始
            if len(pseudo_ticks) < 4:
                continue

            result = self._evaluate(pseudo_ticks, signal_type)
            if result.composite_score >= self._config.composite_threshold:
                # スコア成立: このM1足の close で約定
                mid = float(row["close"])
                spread_pips = self._get_spread_pips(row)
                return TickSimResult(
                    entry_price=mid,
                    spread_pips=spread_pips,
                    composite_score=result.composite_score,
                    entry_time=window.index[i],
                    is_timeout=False,
                    bars_scanned=i + 1,
                )

        # タイムアウト: ウィンドウ内で条件未成立
        if self._config.timeout_execute:
            last_row = window.iloc[-1]
            mid = float(last_row["close"])
            spread_pips = self._get_spread_pips(last_row)
            last_result = self._evaluate(
                pseudo_ticks, signal_type
            )
            return TickSimResult(
                entry_price=mid,
                spread_pips=spread_pips,
                composite_score=last_result.composite_score,
                entry_time=window.index[-1],
                is_timeout=True,
                bars_scanned=len(window),
            )

        return None

    def _m1_to_pseudo_ticks(
        self,
        row: pd.Series,
        signal_type: SignalType,
    ) -> list[dict]:
        """M1足を4つの擬似ティックに変換

        BUY: O→L→H→C（不利→有利の順）
        SELL: O→H→L→C（不利→有利の順）
        """
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])  # noqa: E741
        c = float(row["close"])

        # スプレッド: SPREAD列はpoint単位（10 points = 1 pip）
        spread_raw = self._get_spread_raw(row)
        half = spread_raw / 2

        if signal_type == SignalType.BUY:
            prices = [o, l, h, c]
        else:
            prices = [o, h, l, c]

        return [
            {"bid": p - half, "ask": p + half}
            for p in prices
        ]

    def _get_spread_raw(self, row: pd.Series) -> float:
        """SPREAD列から生スプレッド（price単位）を取得"""
        spread_col = None
        for col in ("spread", "SPREAD", "spread_points"):
            if col in row.index:
                spread_col = col
                break
        if spread_col is None:
            # フォールバック: 固定1.5pips
            return self._config.spread_threshold_pips * self._pip_unit
        # SPREAD列はpoint単位 → price単位に変換
        # MT5: 1 point = 0.001 (JPY) or 0.00001 (非JPY)
        point_unit = 0.001 if self._is_jpy else 0.00001
        return float(row[spread_col]) * point_unit

    def _get_spread_pips(self, row: pd.Series) -> float:
        """SPREAD列からpips単位のスプレッドを取得"""
        spread_raw = self._get_spread_raw(row)
        return spread_raw / self._pip_unit

    def _evaluate(
        self,
        ticks: list[dict],
        signal_type: SignalType,
    ) -> _EvalResult:
        """擬似ティック列のスコアリング（TickEntryOptimizer互換）"""
        spread_score = self._evaluate_spread(ticks)
        momentum_score = self._evaluate_momentum(
            ticks, signal_type
        )
        retrace_score = 0.0
        if self._config.retracement_enabled:
            retrace_score = self._evaluate_retracement(
                ticks, signal_type
            )

        total_weight = (
            self._config.spread_weight
            + self._config.momentum_weight
        )
        composite = (
            spread_score * self._config.spread_weight
            + momentum_score * self._config.momentum_weight
        )
        if self._config.retracement_enabled:
            total_weight += self._config.retracement_weight
            composite += (
                retrace_score * self._config.retracement_weight
            )

        if total_weight > 0:
            composite /= total_weight

        return _EvalResult(
            composite_score=round(composite, 4),
            spread_score=round(spread_score, 4),
            momentum_score=round(momentum_score, 4),
            retracement_score=round(retrace_score, 4),
        )

    def _evaluate_spread(self, ticks: list[dict]) -> float:
        """スプレッド条件評価（TickEntryOptimizer互換）"""
        if not ticks:
            return 0.0

        latest = ticks[-1]
        ask = float(latest["ask"])
        bid = float(latest["bid"])
        if ask <= 0 or bid <= 0:
            return 0.0

        spread_raw = ask - bid
        spread_pips = spread_raw / self._pip_unit

        threshold = self._config.spread_threshold_pips
        if spread_pips <= 0:
            return 1.0
        if spread_pips > threshold * 2:
            return 0.0
        if spread_pips <= threshold:
            base_score = 1.0
        else:
            base_score = 1.0 - (
                (spread_pips - threshold) / threshold
            )

        # 縮小傾向ボーナス（直近3ティック以上）
        bonus = 0.0
        if len(ticks) >= 3:
            spreads = []
            for t in ticks[-3:]:
                a = float(t["ask"])
                b = float(t["bid"])
                if a > 0 and b > 0:
                    spreads.append(a - b)
            if (
                len(spreads) >= 2
                and spreads[-1] < spreads[0]
            ):
                bonus = 0.1

        return min(1.0, base_score + bonus)

    def _evaluate_momentum(
        self,
        ticks: list[dict],
        signal_type: SignalType,
    ) -> float:
        """マイクロモメンタム評価（TickEntryOptimizer互換）"""
        # 直近10ティック（ライブのmomentum_window_ticks相当）
        window = 10
        recent = ticks[-window:] if len(ticks) >= window else ticks

        if len(recent) < 2:
            return 0.0

        is_buy = signal_type == SignalType.BUY
        aligned_count = 0
        total_moves = 0

        for i in range(1, len(recent)):
            prev_mid = (
                float(recent[i - 1]["ask"])
                + float(recent[i - 1]["bid"])
            ) / 2
            curr_mid = (
                float(recent[i]["ask"])
                + float(recent[i]["bid"])
            ) / 2

            if prev_mid <= 0 or curr_mid <= 0:
                continue

            diff = curr_mid - prev_mid
            if abs(diff) < 1e-10:
                continue

            total_moves += 1
            if is_buy and diff > 0:
                aligned_count += 1
            elif not is_buy and diff < 0:
                aligned_count += 1

        if total_moves == 0:
            return 0.5

        return aligned_count / total_moves

    def _evaluate_retracement(
        self,
        ticks: list[dict],
        signal_type: SignalType,
    ) -> float:
        """リトレースメント評価（TickEntryOptimizer互換）"""
        if len(ticks) < 3:
            return 0.0

        is_buy = signal_type == SignalType.BUY

        mids = []
        for t in ticks:
            a = float(t["ask"])
            b = float(t["bid"])
            if a > 0 and b > 0:
                mids.append((a + b) / 2)

        if len(mids) < 3:
            return 0.0

        first_mid = mids[0]
        min_mid = min(mids)
        max_mid = max(mids)
        last_mid = mids[-1]

        if is_buy:
            dip = first_mid - min_mid
            recovery = last_mid - min_mid
            total_range = max_mid - min_mid
            if total_range < 1e-10:
                return 0.0
            if dip > 0 and recovery > dip * 0.5:
                return min(1.0, recovery / total_range)
            return 0.0

        rise = max_mid - first_mid
        recovery = max_mid - last_mid
        total_range = max_mid - min_mid
        if total_range < 1e-10:
            return 0.0
        if rise > 0 and recovery > rise * 0.5:
            return min(1.0, recovery / total_range)
        return 0.0


@dataclass(frozen=True)
class _EvalResult:
    """内部スコアリング結果"""

    composite_score: float
    spread_score: float
    momentum_score: float
    retracement_score: float


# ============================================================
# ティックベース エグジット判定
# ============================================================


@dataclass(frozen=True)
class TickExitResult:
    """ティックベースSL/TP判定結果

    Attributes:
        exit_price: 実際のbid/askでの決済価格
        exit_time: ティック精度の決済時刻
        reason: SL / TP
        trigger_price: SL/TP設定値
        spread_at_exit: 決済時スプレッド（pips）
    """

    exit_price: float
    exit_time: pd.Timestamp
    reason: ExitReason
    trigger_price: float
    spread_at_exit: float


def check_tick_exit(
    position_signal_type: SignalType,
    sl_price: float | None,
    tp_price: float | None,
    tick_df: pd.DataFrame,
    candle_start: pd.Timestamp,
    candle_end: pd.Timestamp,
    slippage_price: float = 0.0,
    pip_unit: float = 0.01,
) -> TickExitResult | None:
    """ティックデータで SL/TP ヒットを精密判定

    M15足の high/low ではなく、ティックの bid/ask で判定する。
    BUYポジション → bid で SL/TP チェック（売り決済）
    SELLポジション → ask で SL/TP チェック（買い決済）

    SL と TP の両方がヒットするM15足では、
    どちらのティックが先に到達したかで判定する。

    Args:
        position_signal_type: BUY or SELL
        sl_price: ストップロス価格（None=なし）
        tp_price: テイクプロフィット価格（None=なし）
        tick_df: ティックDataFrame（DatetimeIndex, bid/ask列）
        candle_start: M15足の開始時刻
        candle_end: M15足の終了時刻
        slippage_price: スリッページ（price単位）
        pip_unit: 1pipの価格単位

    Returns:
        TickExitResult or None（SL/TPヒットなし）
    """
    if sl_price is None and tp_price is None:
        return None

    # 対象期間のティックを抽出
    mask = (tick_df.index >= candle_start) & (
        tick_df.index < candle_end
    )
    window = tick_df.loc[mask]
    if window.empty:
        return None

    is_buy = position_signal_type == SignalType.BUY

    for i in range(len(window)):
        row = window.iloc[i]
        tick_time = window.index[i]
        bid = float(row["bid"])
        ask = float(row["ask"])
        spread = ask - bid

        if is_buy:
            # BUYポジション決済はbid価格
            check_price = bid
            # SL: bid <= sl_price
            if sl_price is not None and check_price <= sl_price:
                return TickExitResult(
                    exit_price=sl_price - slippage_price,
                    exit_time=tick_time,
                    reason=ExitReason.STOP_LOSS,
                    trigger_price=sl_price,
                    spread_at_exit=spread / pip_unit,
                )
            # TP: bid >= tp_price
            if tp_price is not None and check_price >= tp_price:
                return TickExitResult(
                    exit_price=tp_price - slippage_price,
                    exit_time=tick_time,
                    reason=ExitReason.TAKE_PROFIT,
                    trigger_price=tp_price,
                    spread_at_exit=spread / pip_unit,
                )
        else:
            # SELLポジション決済はask価格
            check_price = ask
            # SL: ask >= sl_price
            if sl_price is not None and check_price >= sl_price:
                return TickExitResult(
                    exit_price=sl_price + slippage_price,
                    exit_time=tick_time,
                    reason=ExitReason.STOP_LOSS,
                    trigger_price=sl_price,
                    spread_at_exit=spread / pip_unit,
                )
            # TP: ask <= tp_price
            if tp_price is not None and check_price <= tp_price:
                return TickExitResult(
                    exit_price=tp_price + slippage_price,
                    exit_time=tick_time,
                    reason=ExitReason.TAKE_PROFIT,
                    trigger_price=tp_price,
                    spread_at_exit=spread / pip_unit,
                )

    return None
