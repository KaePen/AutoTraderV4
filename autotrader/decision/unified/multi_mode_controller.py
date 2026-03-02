"""マルチモード並列制御

UNIVERSALモードで全TFを評価し、統合されたトレード機会を検出する。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd

logger = logging.getLogger(__name__)

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.core.exceptions import CalculationError
from autotrader.decision.unified.mode_monitor import (
    UNIVERSAL_CONFIG,
    ModeConfig,
    ModeMonitor,
    ModeSignal,
)

if TYPE_CHECKING:
    from autotrader.core.entities import Candle


@dataclass
class MultiModeConfig:
    """マルチモード設定

    Attributes:
        enabled_modes: 有効なモードリスト
        max_total_positions: 合計最大ポジション数
        max_per_mode: モード別最大ポジション数
        allow_opposite_directions: 逆方向ポジションを許可
        use_parallel_eval: 並列評価を使用
    """

    enabled_modes: list[TradingStrategyMode] = field(
        default_factory=lambda: [
            TradingStrategyMode.UNIVERSAL,
        ]
    )
    max_total_positions: int = 3
    max_per_mode: int = 1
    allow_opposite_directions: bool = False
    use_parallel_eval: bool = True


@dataclass
class MultiModeSignal:
    """マルチモード統合シグナル

    Attributes:
        signals: モード別シグナルリスト
        selected_signal: 採用されたシグナル（コンフリクト解決後）
        conflict_resolved: コンフリクトが解決されたか
        conflict_reason: コンフリクト理由
    """

    signals: list[ModeSignal] = field(default_factory=list)
    selected_signal: ModeSignal | None = None
    conflict_resolved: bool = False
    conflict_reason: str = ""

    def has_signal(self) -> bool:
        """シグナルがあるかどうか

        Returns:
            bool: シグナルがある場合True
        """
        return self.selected_signal is not None

    @property
    def direction(self) -> SignalType:
        """シグナル方向

        Returns:
            SignalType: シグナル方向
        """
        if self.selected_signal:
            return self.selected_signal.direction
        return SignalType.HOLD


class MultiModeController:
    """マルチモード並列制御クラス

    3つのトレードモードを並列に評価し、
    コンフリクト解決後の最適なシグナルを返す。
    """

    # モード設定マッピング（UNIVERSAL固定）
    MODE_CONFIGS: dict[TradingStrategyMode, ModeConfig] = {
        TradingStrategyMode.UNIVERSAL: UNIVERSAL_CONFIG,
    }

    def __init__(
        self,
        config: MultiModeConfig | None = None,
        market_data: dict[str, pd.DataFrame] | None = None,
    ):
        """初期化

        Args:
            config: マルチモード設定
            market_data: 時間足別マーケットデータ
        """
        self._config = config or MultiModeConfig()
        self._market_data = market_data or {}

        # モード別モニターを作成
        self._monitors: dict[TradingStrategyMode, ModeMonitor] = {}
        self._init_monitors()

        # 現在のポジション状態
        self._current_positions: dict[TradingStrategyMode, int] = {
            mode: 0 for mode in self._config.enabled_modes
        }

    def _init_monitors(self) -> None:
        """モニターを初期化"""
        for mode in self._config.enabled_modes:
            mode_config = self.MODE_CONFIGS.get(mode)
            if mode_config:
                self._monitors[mode] = ModeMonitor(
                    config=mode_config,
                    market_data=self._market_data,
                )

    def set_market_data(self, market_data: dict[str, pd.DataFrame]) -> None:
        """マーケットデータを設定

        Args:
            market_data: 時間足別マーケットデータ
        """
        self._market_data = market_data
        for monitor in self._monitors.values():
            monitor.set_market_data(market_data)

    def evaluate_all_modes(
        self,
        current_time: pd.Timestamp,
        candle: "Candle | None" = None,
    ) -> MultiModeSignal:
        """全モードを評価

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足

        Returns:
            MultiModeSignal: 統合シグナル
        """
        if self._config.use_parallel_eval:
            signals = self._evaluate_parallel(current_time, candle)
        else:
            signals = self._evaluate_sequential(current_time, candle)

        # シグナルがない場合
        if not signals:
            return MultiModeSignal()

        # コンフリクト解決
        return self._resolve_conflicts(signals)

    def _evaluate_parallel(
        self,
        current_time: pd.Timestamp,
        candle: "Candle | None",
    ) -> list[ModeSignal]:
        """並列評価

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足

        Returns:
            list[ModeSignal]: シグナルリスト
        """
        signals = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(
                    monitor.evaluate, current_time, candle
                ): mode
                for mode, monitor in self._monitors.items()
            }

            for future in futures:
                try:
                    signal = future.result()
                    if signal is not None:
                        signals.append(signal)
                except CalculationError as e:
                    logger.warning("モニター評価エラー: %s", e, exc_info=True)
                except Exception as e:
                    logger.warning("モニター評価エラー: %s", e, exc_info=True)

        return signals

    def _evaluate_sequential(
        self,
        current_time: pd.Timestamp,
        candle: "Candle | None",
    ) -> list[ModeSignal]:
        """逐次評価

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足

        Returns:
            list[ModeSignal]: シグナルリスト
        """
        signals = []

        for monitor in self._monitors.values():
            try:
                signal = monitor.evaluate(current_time, candle)
                if signal is not None:
                    signals.append(signal)
            except CalculationError as e:
                logger.warning("モニター評価エラー: %s", e, exc_info=True)
            except Exception as e:
                logger.warning("モニター評価エラー: %s", e, exc_info=True)

        return signals

    def _resolve_conflicts(
        self,
        signals: list[ModeSignal],
    ) -> MultiModeSignal:
        """コンフリクト解決

        Args:
            signals: シグナルリスト

        Returns:
            MultiModeSignal: 解決後の統合シグナル
        """
        if not signals:
            return MultiModeSignal()

        # 1シグナルのみなら即採用
        if len(signals) == 1:
            return MultiModeSignal(
                signals=signals,
                selected_signal=signals[0],
                conflict_resolved=False,
            )

        # 方向を確認
        directions = {s.direction for s in signals}

        # 全て同方向なら最も確度が高いものを採用
        if len(directions) == 1:
            best = max(signals, key=lambda s: s.confidence)
            return MultiModeSignal(
                signals=signals,
                selected_signal=best,
                conflict_resolved=False,
            )

        # 異なる方向がある場合
        if not self._config.allow_opposite_directions:
            # 逆方向禁止: 最も確度が高いものを採用
            best = max(signals, key=lambda s: s.confidence)

            # 確度差が小さい場合はシグナルなし
            others = [s for s in signals if s.direction != best.direction]
            if others:
                second_best = max(others, key=lambda s: s.confidence)
                if best.confidence - second_best.confidence < 0.1:
                    return MultiModeSignal(
                        signals=signals,
                        selected_signal=None,
                        conflict_resolved=True,
                        conflict_reason="方向コンフリクト（確度差不十分）",
                    )

            return MultiModeSignal(
                signals=signals,
                selected_signal=best,
                conflict_resolved=True,
                conflict_reason="方向コンフリクト（高確度優先）",
            )

        # 逆方向許可: 全シグナルを返す（呼び出し側で処理）
        best = max(signals, key=lambda s: s.confidence)
        return MultiModeSignal(
            signals=signals,
            selected_signal=best,
            conflict_resolved=False,
        )

    def can_open_position(
        self,
        mode: TradingStrategyMode,
    ) -> bool:
        """ポジション開設可能かチェック

        Args:
            mode: トレードモード

        Returns:
            bool: 開設可能な場合True
        """
        # 合計ポジション数チェック
        total = sum(self._current_positions.values())
        if total >= self._config.max_total_positions:
            return False

        # モード別ポジション数チェック
        mode_count = self._current_positions.get(mode, 0)
        if mode_count >= self._config.max_per_mode:
            return False

        return True

    def record_position_opened(self, mode: TradingStrategyMode) -> None:
        """ポジション開設を記録

        Args:
            mode: トレードモード
        """
        self._current_positions[mode] = (
            self._current_positions.get(mode, 0) + 1
        )

    def record_position_closed(self, mode: TradingStrategyMode) -> None:
        """ポジション決済を記録

        Args:
            mode: トレードモード
        """
        current = self._current_positions.get(mode, 0)
        self._current_positions[mode] = max(0, current - 1)

    def get_active_modes(self) -> list[TradingStrategyMode]:
        """アクティブなモードリストを取得

        Returns:
            list[TradingStrategyMode]: アクティブモードリスト
        """
        return [
            mode for mode, count in self._current_positions.items()
            if count > 0
        ]

    def get_position_summary(self) -> dict[str, int]:
        """ポジションサマリーを取得

        Returns:
            dict[str, int]: モード別ポジション数
        """
        return {
            mode.value: count
            for mode, count in self._current_positions.items()
        }

    @property
    def config(self) -> MultiModeConfig:
        """設定を取得"""
        return self._config

    @property
    def monitors(self) -> dict[TradingStrategyMode, ModeMonitor]:
        """モニター辞書を取得"""
        return self._monitors
