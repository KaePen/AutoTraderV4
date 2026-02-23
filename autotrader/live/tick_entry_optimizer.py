"""ティックエントリー最適化

M1シグナル発生後にティックデータを短時間監視し、
スプレッド縮小・モメンタム確認等の最適条件で
エントリーするレイヤー。

状態マシン:
    IDLE → MONITORING → EXECUTING → IDLE
                      → TIMED_OUT → IDLE
                      → CANCELLED → IDLE
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from autotrader.core.entities import Signal
from autotrader.core.enums import SignalType
from autotrader.live.tick_entry_config import TickEntryConfig

logger = logging.getLogger(__name__)


class OptimizerState(str, Enum):
    """オプティマイザ状態"""

    IDLE = "IDLE"
    MONITORING = "MONITORING"
    EXECUTING = "EXECUTING"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


@dataclass
class EntryConditionResult:
    """エントリー条件評価結果

    Attributes:
        should_execute: 実行すべきか
        composite_score: 総合スコア（0-1）
        spread_score: スプレッドスコア（0-1）
        momentum_score: モメンタムスコア（0-1）
        retracement_score: リトレースメントスコア（0-1）
        reason: 判定理由
        timed_out: タイムアウトによる実行か
    """

    should_execute: bool = False
    composite_score: float = 0.0
    spread_score: float = 0.0
    momentum_score: float = 0.0
    retracement_score: float = 0.0
    reason: str = ""
    timed_out: bool = False


@dataclass
class TickBuffer:
    """ティックデータバッファ

    Attributes:
        ticks: 収集したティックデータ
        start_time: 監視開始時刻
    """

    ticks: list[dict] = field(default_factory=list)
    start_time: float = 0.0

    def add(self, tick: dict) -> None:
        """ティック追加"""
        self.ticks.append(tick)

    def clear(self) -> None:
        """バッファクリア"""
        self.ticks.clear()
        self.start_time = 0.0

    @property
    def count(self) -> int:
        """収集ティック数"""
        return len(self.ticks)

    @property
    def elapsed(self) -> float:
        """経過時間（秒）"""
        if self.start_time <= 0:
            return 0.0
        return time.monotonic() - self.start_time


class TickEntryOptimizer:
    """ティックエントリー最適化

    M1シグナル発生後にティック監視を開始し、
    最適なエントリータイミングを判定する。

    Attributes:
        _config: ティックエントリー設定
        _state: 現在の状態
        _pending_signal: 保持中のシグナル
        _symbol: 対象シンボル
        _buffer: ティックバッファ
        _data_provider: データプロバイダ
    """

    def __init__(
        self,
        config: TickEntryConfig,
        data_provider: object | None = None,
        symbol: str = "USDJPY",
    ) -> None:
        """初期化

        Args:
            config: ティックエントリー設定
            data_provider: MT5DataProvider（get_tick_fast使用）
            symbol: 取引対象シンボル
        """
        self._config = config
        self._state = OptimizerState.IDLE
        self._pending_signal: Signal | None = None
        self._symbol = symbol
        self._buffer = TickBuffer()
        self._data_provider = data_provider
        self._last_evaluation: EntryConditionResult | None = None

    @property
    def state(self) -> OptimizerState:
        """現在の状態"""
        return self._state

    @property
    def is_active(self) -> bool:
        """監視中か"""
        return self._state == OptimizerState.MONITORING

    @property
    def pending_signal(self) -> Signal | None:
        """保持中のシグナル"""
        return self._pending_signal

    @property
    def tick_count(self) -> int:
        """収集ティック数"""
        return self._buffer.count

    @property
    def last_evaluation(self) -> EntryConditionResult | None:
        """直近の条件評価結果"""
        return self._last_evaluation

    def start_monitoring(
        self,
        signal: Signal,
        current_mode: str = "",
    ) -> bool:
        """ティック監視を開始

        Args:
            signal: トレードシグナル
            current_mode: 現在の取引モード名

        Returns:
            bool: 監視開始に成功したか
        """
        if self._state == OptimizerState.MONITORING:
            # 衝突ポリシー処理
            if self._config.conflict_policy == "ignore":
                logger.debug(
                    "既存監視中のため新シグナル無視"
                )
                return False
            if self._config.conflict_policy == "replace":
                logger.info(
                    "既存監視を新シグナルで置換"
                )
                self.reset()

        self._state = OptimizerState.MONITORING
        self._pending_signal = signal
        self._buffer.clear()
        self._buffer.start_time = time.monotonic()

        logger.info(
            "ティック監視開始: %s %s",
            signal.signal_type.value,
            signal.symbol,
        )
        return True

    async def poll_tick(self) -> EntryConditionResult | None:
        """ティックをポーリングし条件評価

        Returns:
            EntryConditionResult: 条件成立/タイムアウト時
            None: 継続監視
        """
        if self._state != OptimizerState.MONITORING:
            return None

        if self._pending_signal is None:
            self.cancel_monitoring("シグナルなし")
            return EntryConditionResult(
                should_execute=False,
                reason="シグナルなし",
            )

        # タイムアウト判定
        elapsed = self._buffer.elapsed
        if elapsed >= self._config.max_monitoring_sec:
            self._state = OptimizerState.TIMED_OUT
            should_exec = self._config.execute_on_timeout
            logger.info(
                "ティック監視タイムアウト(%.1fs): "
                "execute=%s ticks=%d",
                elapsed,
                should_exec,
                self._buffer.count,
            )
            return EntryConditionResult(
                should_execute=should_exec,
                reason="タイムアウト",
                timed_out=True,
            )

        # ティック取得
        tick = await self._fetch_tick()
        if tick:
            self._buffer.add(tick)

        # 最低観測数に達していなければ継続
        if (
            self._buffer.count
            < self._config.momentum_min_ticks
        ):
            return None

        # 条件評価
        result = self.evaluate_conditions(
            self._buffer.ticks,
            self._pending_signal,
        )
        self._last_evaluation = result

        if result.should_execute:
            self._state = OptimizerState.EXECUTING
            logger.info(
                "ティック条件成立: score=%.2f "
                "(spread=%.2f, momentum=%.2f, "
                "retrace=%.2f) ticks=%d",
                result.composite_score,
                result.spread_score,
                result.momentum_score,
                result.retracement_score,
                self._buffer.count,
            )
            return result

        return None

    def evaluate_conditions(
        self,
        ticks: list[dict],
        signal: Signal,
    ) -> EntryConditionResult:
        """エントリー条件を評価（純粋関数）

        3つのサブスコアの加重和で判定:
        1. スプレッド条件
        2. マイクロモメンタム
        3. リトレースメント（オプション）

        Args:
            ticks: ティックデータリスト
            signal: トレードシグナル

        Returns:
            EntryConditionResult: 評価結果
        """
        if not ticks:
            return EntryConditionResult(
                should_execute=False,
                reason="ティックデータなし",
            )

        # 1. スプレッド評価
        spread_score = self._evaluate_spread(ticks)

        # 2. モメンタム評価
        momentum_score = self._evaluate_momentum(
            ticks, signal
        )

        # 3. リトレースメント評価
        retrace_score = 0.0
        if self._config.retracement_enabled:
            retrace_score = self._evaluate_retracement(
                ticks, signal
            )

        # 加重和計算
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
                retrace_score
                * self._config.retracement_weight
            )

        # 正規化
        if total_weight > 0:
            composite /= total_weight

        should_execute = (
            composite >= self._config.composite_threshold
        )

        return EntryConditionResult(
            should_execute=should_execute,
            composite_score=round(composite, 4),
            spread_score=round(spread_score, 4),
            momentum_score=round(momentum_score, 4),
            retracement_score=round(retrace_score, 4),
            reason=(
                "条件成立"
                if should_execute
                else f"スコア不足({composite:.2f}"
                f"<{self._config.composite_threshold})"
            ),
        )

    def _evaluate_spread(
        self, ticks: list[dict]
    ) -> float:
        """スプレッド条件評価

        現在スプレッド ≤ 閾値で1.0、縮小傾向でボーナス。

        Args:
            ticks: ティックデータ

        Returns:
            float: スプレッドスコア（0-1）
        """
        if not ticks:
            return 0.0

        latest = ticks[-1]
        ask = float(latest.get("ask", 0))
        bid = float(latest.get("bid", 0))
        if ask <= 0 or bid <= 0:
            return 0.0

        spread_raw = ask - bid
        # pip変換
        if "JPY" in self._symbol.upper():
            spread_pips = spread_raw / 0.01
        else:
            spread_pips = spread_raw / 0.0001

        threshold = self._config.spread_threshold_pips
        if spread_pips <= 0:
            return 1.0
        if spread_pips > threshold * 2:
            return 0.0
        if spread_pips <= threshold:
            base_score = 1.0
        else:
            # 閾値超え〜2倍で0に線形減衰
            base_score = 1.0 - (
                (spread_pips - threshold) / threshold
            )

        # 縮小傾向ボーナス（直近3ティック以上）
        bonus = 0.0
        if len(ticks) >= 3:
            spreads = []
            for t in ticks[-3:]:
                a = float(t.get("ask", 0))
                b = float(t.get("bid", 0))
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
        signal: Signal,
    ) -> float:
        """マイクロモメンタム評価

        直近Nティックの価格変動方向がシグナル方向と
        一致する割合を計算。

        Args:
            ticks: ティックデータ
            signal: トレードシグナル

        Returns:
            float: モメンタムスコア（0-1）
        """
        window = self._config.momentum_window_ticks
        recent = ticks[-window:] if len(ticks) >= window else ticks

        if len(recent) < 2:
            return 0.0

        # mid価格の変動方向を計算
        is_buy = signal.signal_type == SignalType.BUY
        aligned_count = 0
        total_moves = 0

        for i in range(1, len(recent)):
            prev_mid = (
                float(recent[i - 1].get("ask", 0))
                + float(recent[i - 1].get("bid", 0))
            ) / 2
            curr_mid = (
                float(recent[i].get("ask", 0))
                + float(recent[i].get("bid", 0))
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
            return 0.5  # 変動なし→中立

        ratio = aligned_count / total_moves
        return ratio

    def _evaluate_retracement(
        self,
        ticks: list[dict],
        signal: Signal,
    ) -> float:
        """リトレースメント評価

        シグナル方向と逆の一時的戻しを検出し、
        有利な価格でのエントリー機会を判定。

        Args:
            ticks: ティックデータ
            signal: トレードシグナル

        Returns:
            float: リトレースメントスコア（0-1）
        """
        if len(ticks) < 3:
            return 0.0

        is_buy = signal.signal_type == SignalType.BUY

        # mid価格の推移
        mids = []
        for t in ticks:
            a = float(t.get("ask", 0))
            b = float(t.get("bid", 0))
            if a > 0 and b > 0:
                mids.append((a + b) / 2)

        if len(mids) < 3:
            return 0.0

        first_mid = mids[0]
        min_mid = min(mids)
        max_mid = max(mids)
        last_mid = mids[-1]

        # BUYの場合: 一度下がって戻った→リトレースメント
        if is_buy:
            dip = first_mid - min_mid
            recovery = last_mid - min_mid
            total_range = max_mid - min_mid
            if total_range < 1e-10:
                return 0.0
            if dip > 0 and recovery > dip * 0.5:
                return min(1.0, recovery / total_range)
            return 0.0

        # SELLの場合: 一度上がって戻った
        rise = max_mid - first_mid
        recovery = max_mid - last_mid
        total_range = max_mid - min_mid
        if total_range < 1e-10:
            return 0.0
        if rise > 0 and recovery > rise * 0.5:
            return min(1.0, recovery / total_range)
        return 0.0

    async def _fetch_tick(self) -> dict:
        """ティックデータ取得

        Returns:
            dict: ティック情報（空dictなら取得失敗）
        """
        if self._data_provider is None:
            return {}
        try:
            return await self._data_provider.get_tick_fast(
                self._symbol
            )
        except Exception as e:
            logger.debug("ティック取得失敗: %s", e)
            return {}

    def cancel_monitoring(self, reason: str = "") -> None:
        """監視をキャンセル

        Args:
            reason: キャンセル理由
        """
        if self._state == OptimizerState.MONITORING:
            self._state = OptimizerState.CANCELLED
            logger.info(
                "ティック監視キャンセル: %s "
                "(ticks=%d, elapsed=%.1fs)",
                reason,
                self._buffer.count,
                self._buffer.elapsed,
            )
        self._pending_signal = None
        self._buffer.clear()

    def reset(self) -> None:
        """IDLE状態に復帰、バッファクリア"""
        self._state = OptimizerState.IDLE
        self._pending_signal = None
        self._buffer.clear()
        self._last_evaluation = None
