"""統計的エッジ検定モジュール

ローリングウィンドウでボットの統計的エッジを検証し、
パフォーマンス劣化を3段階アラートで通知する。
バックテストとリアルトレードの両方で同じロジックを使用。
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from autotrader.decision.unified.adaptive.trade_record import (
    TradeRecord,
)

logger = logging.getLogger(__name__)


class EdgeAlertLevel(Enum):
    """エッジアラートレベル"""

    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class EdgeValidatorConfig:
    """エッジ検定設定

    Attributes:
        enabled: エッジ検定有効化
        window_size: ローリングウィンドウサイズ
        min_samples: 検定に必要な最小サンプル数
        expected_winrate: 期待勝率（BT基準）
        info_wr_drop: INFO閾値（WR低下幅）
        warning_wr_drop: WARNING閾値（WR低下幅）
        critical_wr_drop: CRITICAL閾値（WR低下幅）
        info_pf_threshold: INFO閾値（PF）
        warning_pf_threshold: WARNING閾値（PF）
        critical_pf_threshold: CRITICAL閾値（PF）
        pf_below_1_max_trades: PF<1.0持続の最大許容トレード数
    """

    enabled: bool = True
    window_size: int = 100
    min_samples: int = 20
    expected_winrate: float = 0.80
    # WR低下閾値
    info_wr_drop: float = 0.05
    warning_wr_drop: float = 0.10
    critical_wr_drop: float = 0.20
    # PF閾値
    info_pf_threshold: float = 2.0
    warning_pf_threshold: float = 1.5
    critical_pf_threshold: float = 1.0
    # PF<1.0持続閾値
    pf_below_1_max_trades: int = 15


@dataclass(frozen=True)
class EdgeStatus:
    """エッジ検定結果

    Attributes:
        alert_level: アラートレベル
        rolling_winrate: ローリング勝率
        rolling_pf: ローリングプロフィットファクター
        rolling_sharpe: ローリングシャープレシオ
        wr_drop: 期待WRからの低下幅
        pf_below_1_count: PF<1.0持続トレード数
        reasons: アラート理由リスト
        sample_count: サンプル数
    """

    alert_level: EdgeAlertLevel = EdgeAlertLevel.OK
    rolling_winrate: float = 0.0
    rolling_pf: float = 0.0
    rolling_sharpe: float = 0.0
    wr_drop: float = 0.0
    pf_below_1_count: int = 0
    reasons: list[str] = field(default_factory=list)
    sample_count: int = 0


class EdgeValidator:
    """統計的エッジ検定器

    直近Nトレードでボットの統計的エッジを検証し、
    エッジ消失の兆候を3段階アラートで報告する。
    """

    def __init__(
        self,
        config: EdgeValidatorConfig | None = None,
    ) -> None:
        self._config = config or EdgeValidatorConfig()
        self._window: deque[TradeRecord] = deque(
            maxlen=self._config.window_size,
        )
        self._last_status = EdgeStatus()
        # PF<1.0持続カウンタ
        self._pf_below_1_count: int = 0

    @property
    def config(self) -> EdgeValidatorConfig:
        """設定"""
        return self._config

    @property
    def last_status(self) -> EdgeStatus:
        """最新のエッジ検定結果"""
        return self._last_status

    @property
    def window_size(self) -> int:
        """現在のウィンドウ内トレード数"""
        return len(self._window)

    def record_trade(self, record: TradeRecord) -> EdgeStatus:
        """トレード結果を記録してエッジ検定を実行

        Args:
            record: トレード記録

        Returns:
            EdgeStatus: 検定結果
        """
        self._window.append(record)

        if not self._config.enabled:
            return self._last_status

        if len(self._window) < self._config.min_samples:
            self._last_status = EdgeStatus(
                sample_count=len(self._window),
            )
            return self._last_status

        status = self._evaluate()
        self._last_status = status

        # ログ出力
        if status.alert_level == EdgeAlertLevel.CRITICAL:
            logger.warning(
                "エッジCRITICAL: WR=%.1f%% PF=%.2f %s",
                status.rolling_winrate * 100,
                status.rolling_pf,
                "; ".join(status.reasons),
            )
        elif status.alert_level == EdgeAlertLevel.WARNING:
            logger.info(
                "エッジWARNING: WR=%.1f%% PF=%.2f %s",
                status.rolling_winrate * 100,
                status.rolling_pf,
                "; ".join(status.reasons),
            )

        return status

    def reset(self) -> None:
        """状態をリセット"""
        self._window.clear()
        self._last_status = EdgeStatus()
        self._pf_below_1_count = 0

    def _evaluate(self) -> EdgeStatus:
        """エッジ検定を実行"""
        trades = list(self._window)
        n = len(trades)
        reasons: list[str] = []

        # ローリング勝率
        wins = sum(1 for t in trades if t.is_win)
        rolling_wr = wins / n

        # WR低下幅
        wr_drop = self._config.expected_winrate - rolling_wr

        # ローリングPF
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        rolling_pf = (
            gross_profit / gross_loss if gross_loss > 0 else 99.9
        )

        # PF<1.0持続カウンタ更新
        if rolling_pf < 1.0:
            self._pf_below_1_count += 1
        else:
            self._pf_below_1_count = 0

        # ローリングシャープレシオ（簡易版: pnl_pipsベース）
        rolling_sharpe = self._calc_sharpe(trades)

        # アラートレベル判定
        alert = EdgeAlertLevel.OK

        # CRITICAL判定
        if wr_drop >= self._config.critical_wr_drop:
            alert = EdgeAlertLevel.CRITICAL
            reasons.append(
                f"WR急落: {rolling_wr:.1%}"
                f"(期待{self._config.expected_winrate:.1%})",
            )
        if rolling_pf < self._config.critical_pf_threshold:
            alert = EdgeAlertLevel.CRITICAL
            reasons.append(f"PF<{self._config.critical_pf_threshold}: {rolling_pf:.2f}")
        if (
            self._pf_below_1_count
            >= self._config.pf_below_1_max_trades
        ):
            alert = EdgeAlertLevel.CRITICAL
            reasons.append(
                f"PF<1.0が{self._pf_below_1_count}トレード持続",
            )

        # WARNING判定（CRITICALでなければ）
        if alert != EdgeAlertLevel.CRITICAL:
            if wr_drop >= self._config.warning_wr_drop:
                alert = EdgeAlertLevel.WARNING
                reasons.append(
                    f"WR低下: {rolling_wr:.1%}"
                    f"(期待{self._config.expected_winrate:.1%})",
                )
            if rolling_pf < self._config.warning_pf_threshold:
                alert = max(alert, EdgeAlertLevel.WARNING, key=lambda x: list(EdgeAlertLevel).index(x))
                reasons.append(
                    f"PF低下: {rolling_pf:.2f}"
                    f"(<{self._config.warning_pf_threshold})",
                )
            if rolling_sharpe < 0:
                alert = max(alert, EdgeAlertLevel.WARNING, key=lambda x: list(EdgeAlertLevel).index(x))
                reasons.append(f"Sharpe反転: {rolling_sharpe:.2f}")

        # INFO判定（WARNING以上でなければ）
        if alert == EdgeAlertLevel.OK:
            if wr_drop >= self._config.info_wr_drop:
                alert = EdgeAlertLevel.INFO
                reasons.append(
                    f"WR軽微低下: {rolling_wr:.1%}",
                )
            if rolling_pf < self._config.info_pf_threshold:
                alert = max(alert, EdgeAlertLevel.INFO, key=lambda x: list(EdgeAlertLevel).index(x))
                reasons.append(
                    f"PF軽微低下: {rolling_pf:.2f}",
                )

        return EdgeStatus(
            alert_level=alert,
            rolling_winrate=rolling_wr,
            rolling_pf=rolling_pf,
            rolling_sharpe=rolling_sharpe,
            wr_drop=wr_drop,
            pf_below_1_count=self._pf_below_1_count,
            reasons=reasons,
            sample_count=n,
        )

    @staticmethod
    def _calc_sharpe(trades: list[TradeRecord]) -> float:
        """簡易ローリングシャープレシオ

        Args:
            trades: トレードリスト

        Returns:
            float: シャープレシオ（年率換算なし）
        """
        if len(trades) < 2:
            return 0.0

        pnls = [t.pnl_pips for t in trades]
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (
            len(pnls) - 1
        )
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        return mean / std

    def get_status_dict(self) -> dict[str, object]:
        """ステータスを辞書形式で取得（API/diagnostics用）"""
        s = self._last_status
        return {
            "edge_alert_level": s.alert_level.value,
            "edge_rolling_winrate": round(s.rolling_winrate, 4),
            "edge_rolling_pf": round(s.rolling_pf, 2),
            "edge_rolling_sharpe": round(s.rolling_sharpe, 2),
            "edge_wr_drop": round(s.wr_drop, 4),
            "edge_pf_below_1_count": s.pf_below_1_count,
            "edge_sample_count": s.sample_count,
            "edge_reasons": s.reasons,
        }
