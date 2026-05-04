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
    """エッジアラートレベル

    OK → INFO → WARNING → STOP → CRITICAL の5段階。
    WARNING: ロット縮小（防御モード）
    STOP: 一時停止（壊れる前に止める）
    CRITICAL: サーキットブレーカー発動
    """

    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    STOP = "stop"
    CRITICAL = "critical"


@dataclass(frozen=True)
class EdgeValidatorConfig:
    """エッジ検定設定

    5段階アラート: OK → INFO → WARNING → STOP → CRITICAL
    デュアルウィンドウ: 短期(30)で早期検知、長期(100)で安定判定。

    Attributes:
        enabled: エッジ検定有効化
        window_size: 長期ローリングウィンドウサイズ
        short_window_size: 短期ローリングウィンドウサイズ（早期検知）
        min_samples: 検定に必要な最小サンプル数（長期）
        short_min_samples: 短期ウィンドウの最小サンプル数
        expected_winrate: 期待勝率（BT基準）
        info_wr_drop: INFO閾値（WR低下幅）
        warning_wr_drop: WARNING閾値（WR低下幅）
        stop_wr_drop: STOP閾値（WR低下幅）
        critical_wr_drop: CRITICAL閾値（WR低下幅）
        info_pf_threshold: INFO閾値（PF）
        warning_pf_threshold: WARNING閾値（PF）
        stop_pf_threshold: STOP閾値（PF）
        critical_pf_threshold: CRITICAL閾値（PF）
        pf_below_1_max_trades: PF<1.0持続の最大許容トレード数
    """

    enabled: bool = True
    window_size: int = 100
    # 短期ウィンドウ（早期検知用）
    short_window_size: int = 30
    min_samples: int = 20
    short_min_samples: int = 10
    expected_winrate: float = 0.80
    # WR低下閾値
    info_wr_drop: float = 0.05
    warning_wr_drop: float = 0.10
    stop_wr_drop: float = 0.15
    critical_wr_drop: float = 0.20
    # PF閾値
    info_pf_threshold: float = 2.0
    warning_pf_threshold: float = 1.5
    stop_pf_threshold: float = 1.3
    critical_pf_threshold: float = 1.0
    # PF<1.0持続閾値
    pf_below_1_max_trades: int = 15


@dataclass(frozen=True)
class EdgeStatus:
    """エッジ検定結果

    Attributes:
        alert_level: アラートレベル（5段階）
        rolling_winrate: 長期ローリング勝率
        rolling_pf: 長期ローリングPF
        rolling_sharpe: ローリングシャープレシオ
        wr_drop: 期待WRからの低下幅（長期）
        pf_below_1_count: PF<1.0持続トレード数
        reasons: アラート理由リスト
        sample_count: 長期サンプル数
        short_winrate: 短期ローリング勝率
        short_pf: 短期ローリングPF
        short_sample_count: 短期サンプル数
    """

    alert_level: EdgeAlertLevel = EdgeAlertLevel.OK
    rolling_winrate: float = 0.0
    rolling_pf: float = 0.0
    rolling_sharpe: float = 0.0
    wr_drop: float = 0.0
    pf_below_1_count: int = 0
    reasons: list[str] = field(default_factory=list)
    sample_count: int = 0
    short_winrate: float = 0.0
    short_pf: float = 0.0
    short_sample_count: int = 0


class EdgeValidator:
    """統計的エッジ検定器

    デュアルウィンドウ（短期+長期）でボットの統計的エッジを検証し、
    エッジ消失の兆候を5段階アラートで報告する。
    短期ウィンドウで早期検知、長期ウィンドウで安定判定。
    """

    def __init__(
        self,
        config: EdgeValidatorConfig | None = None,
    ) -> None:
        self._config = config or EdgeValidatorConfig()
        # 長期ウィンドウ
        self._window: deque[TradeRecord] = deque(
            maxlen=self._config.window_size,
        )
        # 短期ウィンドウ（早期検知用）
        self._short_window: deque[TradeRecord] = deque(
            maxlen=self._config.short_window_size,
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
        self._short_window.append(record)

        if not self._config.enabled:
            return self._last_status

        if len(self._window) < self._config.min_samples:
            self._last_status = EdgeStatus(
                sample_count=len(self._window),
                short_sample_count=len(self._short_window),
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
        elif status.alert_level == EdgeAlertLevel.STOP:
            logger.warning(
                "エッジSTOP: WR=%.1f%% PF=%.2f %s",
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
        self._short_window.clear()
        self._last_status = EdgeStatus()
        self._pf_below_1_count = 0

    def to_dict(self) -> dict:
        """累積状態を JSON serializable dict に変換 (state dump用)

        BT/Live間の累積状態 (window/short_window/pf_below_1_count) を
        ファイル経由で受け渡すために使用。
        """
        return {
            "window": [r.to_dict() for r in self._window],
            "short_window": [r.to_dict() for r in self._short_window],
            "pf_below_1_count": self._pf_below_1_count,
            "last_status": {
                "alert_level": self._last_status.alert_level.value,
                "rolling_winrate": self._last_status.rolling_winrate,
                "rolling_pf": self._last_status.rolling_pf,
                "rolling_sharpe": self._last_status.rolling_sharpe,
                "wr_drop": self._last_status.wr_drop,
                "pf_below_1_count": self._last_status.pf_below_1_count,
                "reasons": list(self._last_status.reasons),
                "sample_count": self._last_status.sample_count,
                "short_winrate": self._last_status.short_winrate,
                "short_pf": self._last_status.short_pf,
                "short_sample_count": self._last_status.short_sample_count,
            },
        }

    def load_state(self, data: dict) -> None:
        """dump した状態を復元 (state inject用)

        BT実行前にライブの累積状態を注入してreplay再現精度を上げる目的。
        既存の状態は破棄される。

        Args:
            data: to_dict() の出力
        """
        self._window.clear()
        self._short_window.clear()
        for r_data in data.get("window", []):
            self._window.append(TradeRecord.from_dict(r_data))
        for r_data in data.get("short_window", []):
            self._short_window.append(TradeRecord.from_dict(r_data))
        self._pf_below_1_count = int(data.get("pf_below_1_count", 0))

        status_data = data.get("last_status", {})
        if status_data:
            self._last_status = EdgeStatus(
                alert_level=EdgeAlertLevel(
                    status_data.get("alert_level", "ok"),
                ),
                rolling_winrate=float(
                    status_data.get("rolling_winrate", 0.0),
                ),
                rolling_pf=float(status_data.get("rolling_pf", 0.0)),
                rolling_sharpe=float(
                    status_data.get("rolling_sharpe", 0.0),
                ),
                wr_drop=float(status_data.get("wr_drop", 0.0)),
                pf_below_1_count=int(
                    status_data.get("pf_below_1_count", 0),
                ),
                reasons=list(status_data.get("reasons", [])),
                sample_count=int(status_data.get("sample_count", 0)),
                short_winrate=float(
                    status_data.get("short_winrate", 0.0),
                ),
                short_pf=float(status_data.get("short_pf", 0.0)),
                short_sample_count=int(
                    status_data.get("short_sample_count", 0),
                ),
            )

    def _evaluate(self) -> EdgeStatus:
        """エッジ検定を実行（デュアルウィンドウ）

        短期ウィンドウで早期検知、長期ウィンドウで安定判定。
        両方の結果のうち、より深刻な方を採用する。
        """
        trades = list(self._window)
        n = len(trades)
        reasons: list[str] = []

        # --- 長期ウィンドウ統計 ---
        rolling_wr, wr_drop, rolling_pf = (
            self._calc_stats(trades)
        )

        # PF<1.0持続カウンタ更新
        if rolling_pf < 1.0:
            self._pf_below_1_count += 1
        else:
            self._pf_below_1_count = 0

        rolling_sharpe = self._calc_sharpe(trades)

        # --- 短期ウィンドウ統計 ---
        short_trades = list(self._short_window)
        short_n = len(short_trades)
        short_wr = 0.0
        short_pf = 0.0
        if short_n >= self._config.short_min_samples:
            short_wr, _, short_pf = self._calc_stats(
                short_trades,
            )

        # --- アラートレベル判定（長期ベース） ---
        alert = self._judge_alert(
            rolling_wr, wr_drop, rolling_pf,
            rolling_sharpe, reasons, "長期",
        )

        # --- 短期ウィンドウによる早期検知 ---
        if short_n >= self._config.short_min_samples:
            short_reasons: list[str] = []
            short_wr_drop = (
                self._config.expected_winrate - short_wr
            )
            short_alert = self._judge_alert(
                short_wr, short_wr_drop, short_pf,
                0.0, short_reasons, "短期",
            )
            # 短期の方が深刻なら昇格
            _levels = list(EdgeAlertLevel)
            if _levels.index(short_alert) > _levels.index(
                alert,
            ):
                alert = short_alert
                reasons.extend(short_reasons)

        return EdgeStatus(
            alert_level=alert,
            rolling_winrate=rolling_wr,
            rolling_pf=rolling_pf,
            rolling_sharpe=rolling_sharpe,
            wr_drop=wr_drop,
            pf_below_1_count=self._pf_below_1_count,
            reasons=reasons,
            sample_count=n,
            short_winrate=short_wr,
            short_pf=short_pf,
            short_sample_count=short_n,
        )

    def _calc_stats(
        self,
        trades: list[TradeRecord],
    ) -> tuple[float, float, float]:
        """ウィンドウの基本統計を計算

        Returns:
            (勝率, WR低下幅, PF)
        """
        n = len(trades)
        if n == 0:
            return 0.0, self._config.expected_winrate, 0.0
        wins = sum(1 for t in trades if t.is_win)
        wr = wins / n
        wr_drop = self._config.expected_winrate - wr
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(
            sum(t.pnl for t in trades if t.pnl < 0),
        )
        pf = gross_profit / gross_loss if gross_loss > 0 else 99.9
        return wr, wr_drop, pf

    def _judge_alert(
        self,
        wr: float,
        wr_drop: float,
        pf: float,
        sharpe: float,
        reasons: list[str],
        label: str,
    ) -> EdgeAlertLevel:
        """アラートレベルを判定"""
        alert = EdgeAlertLevel.OK
        cfg = self._config

        # CRITICAL判定
        if wr_drop >= cfg.critical_wr_drop:
            alert = EdgeAlertLevel.CRITICAL
            reasons.append(
                f"{label}WR急落: {wr:.1%}"
                f"(期待{cfg.expected_winrate:.1%})",
            )
        if pf < cfg.critical_pf_threshold:
            alert = EdgeAlertLevel.CRITICAL
            reasons.append(
                f"{label}PF<{cfg.critical_pf_threshold}: "
                f"{pf:.2f}",
            )
        if (
            self._pf_below_1_count
            >= cfg.pf_below_1_max_trades
        ):
            alert = EdgeAlertLevel.CRITICAL
            reasons.append(
                f"PF<1.0が"
                f"{self._pf_below_1_count}トレード持続",
            )

        # STOP判定（CRITICALでなければ）
        if alert != EdgeAlertLevel.CRITICAL:
            if wr_drop >= cfg.stop_wr_drop:
                alert = EdgeAlertLevel.STOP
                reasons.append(
                    f"{label}WR低下(STOP): {wr:.1%}",
                )
            if pf < cfg.stop_pf_threshold:
                alert = self._max_alert(
                    alert, EdgeAlertLevel.STOP,
                )
                reasons.append(
                    f"{label}PF低下(STOP): {pf:.2f}"
                    f"(<{cfg.stop_pf_threshold})",
                )

        # WARNING判定（STOP以上でなければ）
        if alert in (EdgeAlertLevel.OK, EdgeAlertLevel.INFO):
            if wr_drop >= cfg.warning_wr_drop:
                alert = EdgeAlertLevel.WARNING
                reasons.append(
                    f"{label}WR低下: {wr:.1%}",
                )
            if pf < cfg.warning_pf_threshold:
                alert = self._max_alert(
                    alert, EdgeAlertLevel.WARNING,
                )
                reasons.append(
                    f"{label}PF低下: {pf:.2f}"
                    f"(<{cfg.warning_pf_threshold})",
                )
            if sharpe < 0:
                alert = self._max_alert(
                    alert, EdgeAlertLevel.WARNING,
                )
                reasons.append(
                    f"{label}Sharpe反転: {sharpe:.2f}",
                )

        # INFO判定
        if alert == EdgeAlertLevel.OK:
            if wr_drop >= cfg.info_wr_drop:
                alert = EdgeAlertLevel.INFO
                reasons.append(
                    f"{label}WR軽微低下: {wr:.1%}",
                )
            if pf < cfg.info_pf_threshold:
                alert = self._max_alert(
                    alert, EdgeAlertLevel.INFO,
                )
                reasons.append(
                    f"{label}PF軽微低下: {pf:.2f}",
                )

        return alert

    @staticmethod
    def _max_alert(
        a: EdgeAlertLevel,
        b: EdgeAlertLevel,
    ) -> EdgeAlertLevel:
        """より深刻なアラートレベルを返す"""
        _levels = list(EdgeAlertLevel)
        if _levels.index(a) >= _levels.index(b):
            return a
        return b

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
            "edge_short_winrate": round(s.short_winrate, 4),
            "edge_short_pf": round(s.short_pf, 2),
            "edge_short_sample_count": s.short_sample_count,
            "edge_reasons": s.reasons,
        }
