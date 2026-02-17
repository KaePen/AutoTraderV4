"""バックテスト履歴サービス

バックテスト結果のDB永続化と履歴管理。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from autotrader.adapters.database.models import (
    BacktestResult,
    TradeRecord,
)
from autotrader.adapters.database.repositories import (
    BacktestRepository,
    TradeRepository,
)


class BacktestHistoryService:
    """バックテスト履歴サービス"""

    def __init__(self, db: Session) -> None:
        """初期化

        Args:
            db: DBセッション
        """
        self._db = db
        self._bt_repo = BacktestRepository(db)
        self._trade_repo = TradeRepository(db)

    def create_backtest_record(
        self,
        name: str,
        symbol: str,
        start_year: int,
        end_year: int,
        config: dict | None = None,
    ) -> BacktestResult:
        """バックテスト記録を作成

        Args:
            name: 実行名
            symbol: 通貨ペア
            start_year: 開始年
            end_year: 終了年
            config: 設定パラメータ

        Returns:
            BacktestResult: 作成された記録
        """
        return self._bt_repo.create(
            name=name,
            symbol=symbol,
            timeframe="M15",
            start_date=datetime(start_year, 1, 1),
            end_date=datetime(end_year, 12, 31),
            config=config,
        )

    def complete_backtest(
        self,
        backtest: BacktestResult,
        result_data: dict,
    ) -> BacktestResult:
        """バックテスト結果を完了に更新

        Args:
            backtest: バックテスト記録
            result_data: 結果辞書

        Returns:
            BacktestResult: 更新された記録
        """
        return self._bt_repo.update_metrics(
            backtest=backtest,
            total_trades=result_data.get("total_trades", 0),
            winning_trades=result_data.get("winning_trades", 0),
            losing_trades=result_data.get("losing_trades", 0),
            total_profit=result_data.get("total_profit", 0.0),
            total_loss=result_data.get("total_loss", 0.0),
            max_drawdown=result_data.get("max_drawdown", 0.0),
            max_drawdown_pct=result_data.get(
                "max_drawdown_pct", 0.0
            ),
            sharpe_ratio=result_data.get("sharpe_ratio"),
        )

    def get_history(
        self, limit: int = 20
    ) -> list[dict]:
        """バックテスト履歴を取得

        Args:
            limit: 取得件数

        Returns:
            list[dict]: 履歴一覧
        """
        results = self._bt_repo.get_recent(limit=limit)
        return [r.to_dict() for r in results]

    def get_backtest_trades(
        self, backtest_id: int
    ) -> list[dict]:
        """特定バックテストのトレード一覧

        Args:
            backtest_id: バックテストID

        Returns:
            list[dict]: トレード一覧
        """
        trades = self._trade_repo.get_by_backtest(backtest_id)
        return [t.to_dict() for t in trades]
