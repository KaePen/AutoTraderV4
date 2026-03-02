"""バックテスト状態管理モジュール

バックテストの実行状態を管理するクラスを提供。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import threading
import uuid

from autotrader.core.clock import Clock, SystemClock


class BacktestStatus(str, Enum):
    """バックテスト状態"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class BacktestState:
    """バックテスト状態

    Attributes:
        backtest_id: バックテストID
        status: 現在のステータス
        current_year: 処理中の年
        total_years: 総年数
        current_progress: 現在の進捗（0-100）
        started_at: 開始日時
        completed_at: 完了日時
        error_message: エラーメッセージ
        cancel_requested: キャンセルリクエストフラグ
        metadata: メタデータ
    """

    backtest_id: str
    status: BacktestStatus = BacktestStatus.PENDING
    current_year: int | None = None
    total_years: int = 0
    current_progress: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_running(self) -> bool:
        """実行中かどうか

        Returns:
            bool: 実行中ならTrue
        """
        return self.status == BacktestStatus.RUNNING

    def is_completed(self) -> bool:
        """完了したかどうか

        Returns:
            bool: 完了ならTrue
        """
        return self.status in [
            BacktestStatus.COMPLETED,
            BacktestStatus.CANCELLED,
            BacktestStatus.FAILED,
        ]

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換

        Returns:
            dict: 状態辞書
        """
        return {
            "backtest_id": self.backtest_id,
            "status": self.status.value,
            "current_year": self.current_year,
            "total_years": self.total_years,
            "current_progress": round(self.current_progress, 2),
            "started_at": (
                self.started_at.isoformat() if self.started_at else None
            ),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "error_message": self.error_message,
            "cancel_requested": self.cancel_requested,
        }


class BacktestStateManager:
    """バックテスト状態管理

    複数のバックテスト実行状態を管理。
    スレッドセーフな実装。
    """

    def __init__(self, clock: Clock | None = None):
        """初期化

        Args:
            clock: 時刻プロバイダー（デフォルト: SystemClock）
        """
        self._states: dict[str, BacktestState] = {}
        self._lock = threading.Lock()
        self._clock = clock or SystemClock()

    def create(
        self,
        backtest_id: str | None = None,
        total_years: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> BacktestState:
        """新規状態を作成

        Args:
            backtest_id: バックテストID（Noneで自動生成）
            total_years: 総年数
            metadata: メタデータ

        Returns:
            BacktestState: 作成された状態
        """
        with self._lock:
            if backtest_id is None:
                backtest_id = str(uuid.uuid4())

            state = BacktestState(
                backtest_id=backtest_id,
                status=BacktestStatus.PENDING,
                total_years=total_years,
                metadata=metadata or {},
            )
            self._states[backtest_id] = state
            return state

    def get(self, backtest_id: str) -> BacktestState | None:
        """状態を取得

        Args:
            backtest_id: バックテストID

        Returns:
            BacktestState | None: 状態（見つからない場合None）
        """
        with self._lock:
            return self._states.get(backtest_id)

    def start(self, backtest_id: str) -> bool:
        """バックテストを開始状態に変更

        Args:
            backtest_id: バックテストID

        Returns:
            bool: 成功した場合True
        """
        with self._lock:
            state = self._states.get(backtest_id)
            if state is None:
                return False

            state.status = BacktestStatus.RUNNING
            state.started_at = self._clock.now()
            return True

    def update_progress(
        self,
        backtest_id: str,
        current_year: int | None = None,
        progress: float | None = None,
    ) -> bool:
        """進捗を更新

        Args:
            backtest_id: バックテストID
            current_year: 現在処理中の年
            progress: 進捗率（0-100）

        Returns:
            bool: 成功した場合True
        """
        with self._lock:
            state = self._states.get(backtest_id)
            if state is None:
                return False

            if current_year is not None:
                state.current_year = current_year

            if progress is not None:
                state.current_progress = progress

            return True

    def complete(
        self,
        backtest_id: str,
        status: BacktestStatus = BacktestStatus.COMPLETED,
        error_message: str | None = None,
    ) -> bool:
        """バックテストを完了状態に変更

        Args:
            backtest_id: バックテストID
            status: 完了ステータス
            error_message: エラーメッセージ

        Returns:
            bool: 成功した場合True
        """
        with self._lock:
            state = self._states.get(backtest_id)
            if state is None:
                return False

            state.status = status
            state.completed_at = self._clock.now()
            state.current_progress = 100.0

            if error_message:
                state.error_message = error_message

            return True

    def cancel(self, backtest_id: str) -> bool:
        """キャンセルをリクエスト

        Args:
            backtest_id: バックテストID

        Returns:
            bool: 成功した場合True
        """
        with self._lock:
            state = self._states.get(backtest_id)
            if state is None:
                return False

            state.cancel_requested = True
            return True

    def is_cancel_requested(self, backtest_id: str) -> bool:
        """キャンセルがリクエストされたか確認

        Args:
            backtest_id: バックテストID

        Returns:
            bool: キャンセルリクエスト済みならTrue
        """
        with self._lock:
            state = self._states.get(backtest_id)
            if state is None:
                return False
            return state.cancel_requested

    def cleanup(self, backtest_id: str) -> bool:
        """状態をクリーンアップ

        Args:
            backtest_id: バックテストID

        Returns:
            bool: 成功した場合True
        """
        with self._lock:
            if backtest_id in self._states:
                del self._states[backtest_id]
                return True
            return False

    def cleanup_completed(self, max_age_seconds: int = 3600) -> int:
        """完了した古い状態をクリーンアップ

        Args:
            max_age_seconds: 保持する最大秒数

        Returns:
            int: クリーンアップされた数
        """
        now = self._clock.now()
        cleaned = 0

        with self._lock:
            to_delete = []
            for backtest_id, state in self._states.items():
                if state.is_completed() and state.completed_at:
                    age = (now - state.completed_at).total_seconds()
                    if age > max_age_seconds:
                        to_delete.append(backtest_id)

            for backtest_id in to_delete:
                del self._states[backtest_id]
                cleaned += 1

        return cleaned

    def get_all_running(self) -> list[BacktestState]:
        """実行中のすべての状態を取得

        Returns:
            list[BacktestState]: 実行中の状態リスト
        """
        with self._lock:
            return [
                state for state in self._states.values()
                if state.is_running()
            ]

    def get_all(self) -> list[BacktestState]:
        """すべての状態を取得

        Returns:
            list[BacktestState]: 全状態リスト
        """
        with self._lock:
            return list(self._states.values())


# グローバルインスタンス（WebUI等で共有使用）
_global_state_manager: BacktestStateManager | None = None


def get_state_manager() -> BacktestStateManager:
    """グローバル状態マネージャーを取得

    Returns:
        BacktestStateManager: 状態マネージャー
    """
    global _global_state_manager
    if _global_state_manager is None:
        _global_state_manager = BacktestStateManager()
    return _global_state_manager
