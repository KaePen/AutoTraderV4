"""WebUI入出力アダプター

FastAPIリクエストから ExecutorConfig への変換と、
実行結果のJSON応答を担当。
"""

from __future__ import annotations

from typing import Any

from autotrader.backtest.executor import ExecutorConfig, ExecutorResult


class WebUIAdapter:
    """WebUI入出力アダプター

    FastAPIのPydanticモデルをExecutorConfigに変換し、
    結果をJSON応答形式に変換する。
    """

    @classmethod
    def from_request(cls, request: Any) -> ExecutorConfig:
        """リクエストからExecutorConfigを生成

        Args:
            request: Pydanticリクエストモデル

        Returns:
            ExecutorConfig: 実行設定
        """
        # Pydanticモデルの属性を取得
        start_year = getattr(request, "start_year", 2020)
        end_year = getattr(request, "end_year", 2024)
        initial_balance = getattr(request, "initial_balance", 1_000_000.0)
        volume = getattr(request, "volume", 1.0)
        data_dir = getattr(request, "data_dir", "data/csv")
        use_short_timeframe = getattr(request, "use_short_timeframe", True)

        # 新オプション（後方互換性）
        parallel_years = getattr(request, "parallel_years", False)
        max_workers = getattr(request, "max_workers", None)
        symbol = getattr(request, "symbol", "USDJPY")
        max_positions = getattr(request, "max_positions", 1)

        return ExecutorConfig(
            start_year=start_year,
            end_year=end_year,
            initial_balance=initial_balance,
            volume=volume,
            symbol=symbol,
            data_dir=data_dir,
            use_short_timeframe=use_short_timeframe,
            parallel_years=parallel_years,
            max_workers=max_workers,
            max_positions=max_positions,
        )

    @classmethod
    def to_response(cls, result: ExecutorResult) -> dict[str, Any]:
        """ExecutorResultを応答辞書に変換

        Args:
            result: 実行結果

        Returns:
            dict: JSON応答用辞書
        """
        return {
            "status": "cancelled" if result.cancelled else "completed",
            "trades": result.trades,
            "win_rate": round(result.win_rate, 2),
            "profit_factor": round(result.profit_factor, 2),
            "net_profit": round(result.net_profit, 2),
            "max_drawdown": round(result.max_drawdown, 2),
            "sharpe_ratio": round(result.sharpe_ratio, 2),
            "annual_return": round(result.annual_return, 2),
            "execution_time": round(result.execution_time, 2),
            "monthly_results": cls._format_monthly_results(
                result.monthly_results
            ),
            "yearly_results": cls._format_yearly_results(
                result.yearly_results
            ),
            "mode_results": result.mode_results,
        }

    @classmethod
    def _format_monthly_results(
        cls,
        monthly_results: list[dict],
    ) -> list[dict[str, Any]]:
        """月別結果をフォーマット

        Args:
            monthly_results: 月別結果リスト

        Returns:
            list: フォーマット済み月別結果
        """
        formatted = []
        for r in monthly_results:
            formatted.append({
                "year": r.get("year", 0),
                "month": r.get("month", 0),
                "trades": r.get("trades", 0),
                "pnl": round(r.get("pnl", 0), 2),
                "return_pct": round(r.get("return_pct", 0), 2),
            })
        return formatted

    @classmethod
    def _format_yearly_results(
        cls,
        yearly_results: list[dict],
    ) -> list[dict[str, Any]]:
        """年別結果をフォーマット

        Args:
            yearly_results: 年別結果リスト

        Returns:
            list: フォーマット済み年別結果
        """
        formatted = []
        for r in yearly_results:
            formatted.append({
                "year": r.get("year", 0),
                "trades": r.get("trades", 0),
                "win_rate": round(r.get("win_rate", 0), 2),
                "profit_factor": round(r.get("profit_factor", 0), 2),
                "net_profit": round(r.get("net_profit", 0), 2),
                "max_drawdown": round(r.get("max_drawdown", 0), 2),
                "sharpe": round(r.get("sharpe", 0), 2),
            })
        return formatted

    @classmethod
    def to_progress_event(
        cls,
        current: int,
        total: int,
        elapsed: float,
        message: str = "",
    ) -> dict[str, Any]:
        """進捗イベントを生成

        Args:
            current: 現在の進捗
            total: 合計
            elapsed: 経過時間
            message: メッセージ

        Returns:
            dict: 進捗イベント
        """
        pct = current / total * 100 if total > 0 else 0
        return {
            "type": "progress",
            "current": current,
            "total": total,
            "percentage": round(pct, 1),
            "elapsed": round(elapsed, 2),
            "message": message,
        }

    @classmethod
    def to_trade_event(
        cls,
        trade_type: str,
        trade_data: dict[str, Any],
    ) -> dict[str, Any]:
        """トレードイベントを生成

        Args:
            trade_type: イベントタイプ（opened/closed）
            trade_data: トレードデータ

        Returns:
            dict: トレードイベント
        """
        return {
            "type": f"trade_{trade_type}",
            **trade_data,
        }

    @classmethod
    def to_metrics_event(
        cls,
        balance: float,
        equity: float,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        max_drawdown: float,
    ) -> dict[str, Any]:
        """メトリクスイベントを生成

        Args:
            balance: 残高
            equity: 有効証拠金
            total_trades: 総取引数
            winning_trades: 勝ち取引数
            losing_trades: 負け取引数
            max_drawdown: 最大ドローダウン

        Returns:
            dict: メトリクスイベント
        """
        win_rate = (
            winning_trades / total_trades * 100
            if total_trades > 0 else 0
        )
        return {
            "type": "metrics",
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 2),
            "max_drawdown": round(max_drawdown, 2),
        }
