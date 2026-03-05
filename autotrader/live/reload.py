"""トレードロジックホットリロードユーティリティ

importlib.reload() を使用して UnifiedTradeBot / PositionManager /
PositionSizer のコードをプロセス再起動なしに更新する。
"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TradeLogicReloader:
    """トレードロジックモジュールのホットリロードを管理する

    Attributes:
        RELOAD_MODULES: リロード対象モジュール名プレフィックス（依存順）
        WATCH_DIRS: ファイル変更検知対象ディレクトリ（プロジェクトルート相対）
        _project_root: プロジェクトルートパス
        _mtime_snapshot: ファイルパス→mtime のスナップショット
    """

    # リロード順序は依存関係の昇順（被依存モジュールを先にリロード）
    RELOAD_MODULES: list[str] = [
        "autotrader.calculator",
        "autotrader.constraint",
        "autotrader.decision.unified.config",
        "autotrader.decision.unified.position_manager",
        "autotrader.decision.unified.scoring",
        "autotrader.decision.unified.pipeline_pkg",
        "autotrader.decision.unified.trade_bot",
    ]

    WATCH_DIRS: list[str] = [
        "autotrader/calculator",
        "autotrader/constraint",
        "autotrader/decision/unified",
    ]

    def __init__(self, project_root: Path) -> None:
        """初期化

        Args:
            project_root: プロジェクトルートパス
        """
        self._project_root = project_root
        self._mtime_snapshot: dict[str, float] = {}
        self._take_snapshot()

    def _take_snapshot(self) -> None:
        """現在のmtimeスナップショットを取得"""
        for watch_dir in self.WATCH_DIRS:
            dir_path = self._project_root / watch_dir
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                try:
                    self._mtime_snapshot[str(py_file)] = (
                        py_file.stat().st_mtime
                    )
                except OSError:
                    pass

    def check_changed(self) -> list[str]:
        """mtime比較で変更されたファイルパスを返す

        Returns:
            list[str]: 変更されたファイルのパスリスト（相対パス）
        """
        changed: list[str] = []
        for watch_dir in self.WATCH_DIRS:
            dir_path = self._project_root / watch_dir
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                path_str = str(py_file)
                try:
                    current_mtime = py_file.stat().st_mtime
                except OSError:
                    continue
                prev_mtime = self._mtime_snapshot.get(path_str)
                if prev_mtime is None or current_mtime != prev_mtime:
                    # 相対パスで返す（ログ表示用）
                    try:
                        rel = str(
                            py_file.relative_to(self._project_root)
                        )
                    except ValueError:
                        rel = path_str
                    changed.append(rel)
        return changed

    def mark_reloaded(self) -> None:
        """リロード完了後にmtimeスナップショットを最新化"""
        self._take_snapshot()

    def reload_modules(self) -> None:
        """sys.modules 登録済みモジュールを依存順にリロード

        ``from X import Y`` 形式でバインド済みの参照は更新されない。
        新インスタンス生成は create_new_* メソッドで動的インポートを使うこと。

        Raises:
            Exception: モジュールリロード中のエラー（呼び出し元でキャッチ）
        """
        reloaded: list[str] = []
        for mod_prefix in self.RELOAD_MODULES:
            # sys.modules からプレフィックス一致のモジュールを収集
            targets = [
                (name, mod)
                for name, mod in list(sys.modules.items())
                if name == mod_prefix
                or name.startswith(mod_prefix + ".")
            ]
            # サブモジュールを先にリロード（逆アルファベット順で深いパスが先）
            targets.sort(key=lambda x: x[0], reverse=True)
            for name, mod in targets:
                try:
                    importlib.reload(mod)
                    reloaded.append(name)
                except Exception as e:
                    logger.error(
                        "モジュールリロード失敗: %s - %s", name, e
                    )
                    raise
        logger.info("モジュールリロード完了: %d 件", len(reloaded))

    def create_new_bot(self, config: Any) -> Any:
        """動的インポートで新しい UnifiedTradeBot インスタンスを生成

        Args:
            config: UnifiedBotConfig インスタンス

        Returns:
            UnifiedTradeBot: reload 後のクラス定義で生成した新インスタンス
        """
        mod = importlib.import_module(
            "autotrader.decision.unified.trade_bot"
        )
        return mod.UnifiedTradeBot(config)

    def create_new_pm(self) -> Any:
        """動的インポートで新しい PositionManager インスタンスを生成

        Returns:
            PositionManager: reload 後のクラス定義で生成した新インスタンス
        """
        mod = importlib.import_module(
            "autotrader.decision.unified.position_manager"
        )
        return mod.PositionManager()

    def create_new_sizer(self, sizer_config: Any) -> Any:
        """動的インポートで新しい PositionSizer インスタンスを生成

        Args:
            sizer_config: PositionSizerConfig インスタンス

        Returns:
            PositionSizer: reload 後のクラス定義で生成した新インスタンス
        """
        mod = importlib.import_module(
            "autotrader.decision.unified.position_sizer"
        )
        return mod.PositionSizer(sizer_config)
