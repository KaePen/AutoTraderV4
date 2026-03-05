"""マルチシンボルエンジンマネージャー

シンボルごとに独立した LiveTradingEngine インスタンスを管理し、
MT5接続とデータプロバイダを全エンジンで共有する。
"""

from __future__ import annotations

import logging
from typing import Any

from autotrader.adapters.mt5.config import MT5Config
from autotrader.adapters.mt5.connection import (
    MT5ConnectionManager,
)
from autotrader.adapters.mt5.data_provider import (
    MT5DataProvider,
)
from autotrader.core.entities import AccountInfo
from autotrader.live.config import LiveTradingConfig
from autotrader.live.engine import LiveTradingEngine

logger = logging.getLogger(__name__)


class EngineManager:
    """マルチシンボルエンジンマネージャー

    共有コンポーネント（MT5接続・データプロバイダ）を1つ持ち、
    シンボルごとに独立した LiveTradingEngine を管理する。

    Attributes:
        _conn: 共有MT5接続マネージャー
        _data_provider: 共有MT5データプロバイダ
        _mt5_config: MT5接続設定
        _engines: シンボル→エンジンの辞書
    """

    def __init__(self, mt5_config: MT5Config) -> None:
        """初期化

        Args:
            mt5_config: MT5接続設定
        """
        self._mt5_config = mt5_config
        self._conn = MT5ConnectionManager(mt5_config)
        self._data_provider = MT5DataProvider(self._conn)
        self._engines: dict[str, LiveTradingEngine] = {}
        # 共有コレクター（最初のエンジン起動時に初期化）
        self._shared_fundamental_collector = None
        self._shared_rss_collector = None

    @property
    def connected(self) -> bool:
        """MT5接続状態"""
        return self._conn.connected

    @property
    def engines(self) -> dict[str, LiveTradingEngine]:
        """エンジン辞書のコピー"""
        return dict(self._engines)

    @property
    def symbols(self) -> list[str]:
        """稼働シンボル一覧"""
        return list(self._engines.keys())

    @property
    def account_info(self) -> AccountInfo | None:
        """口座情報（MT5口座は共通のため任意エンジンから取得）"""
        for engine in self._engines.values():
            if engine.account_info is not None:
                return engine.account_info
        return None

    def get_engine(
        self, symbol: str,
    ) -> LiveTradingEngine | None:
        """シンボルに対応するエンジンを取得

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            LiveTradingEngine | None: エンジン
        """
        return self._engines.get(symbol)

    async def add_symbol(
        self, config: LiveTradingConfig,
    ) -> LiveTradingEngine:
        """シンボルのエンジンを追加

        既に存在する場合は既存のエンジンを返す。

        Args:
            config: ライブトレーディング設定

        Returns:
            LiveTradingEngine: 追加/既存のエンジン
        """
        symbol = config.symbol
        if symbol in self._engines:
            logger.info(
                "エンジン既存: %s（再利用）", symbol
            )
            return self._engines[symbol]

        # 最初のエンジンからコレクターを取得（共有用）
        if self._engines:
            first_engine = next(
                iter(self._engines.values())
            )
            if (
                self._shared_fundamental_collector is None
                and first_engine._fundamental_collector
                is not None
            ):
                self._shared_fundamental_collector = (
                    first_engine._fundamental_collector
                )
            if (
                self._shared_rss_collector is None
                and first_engine._rss_collector
                is not None
            ):
                self._shared_rss_collector = (
                    first_engine._rss_collector
                )

        engine = LiveTradingEngine(
            config=config,
            shared_conn=self._conn,
            shared_data_provider=self._data_provider,
            shared_fundamental_collector=(
                self._shared_fundamental_collector
            ),
            shared_rss_collector=(
                self._shared_rss_collector
            ),
        )
        self._engines[symbol] = engine
        logger.info("エンジン追加: %s", symbol)

        # 接続済みならエンジンも起動
        if self._conn.connected:
            await engine.start()
            logger.info("エンジン起動: %s", symbol)

        return engine

    async def remove_symbol(self, symbol: str) -> None:
        """シンボルのエンジンを除去

        Args:
            symbol: 通貨ペアシンボル
        """
        engine = self._engines.pop(symbol, None)
        if engine is None:
            logger.warning(
                "エンジン未登録: %s", symbol
            )
            return

        if engine.running:
            await engine.stop()
        logger.info("エンジン除去: %s", symbol)

    async def connect(self) -> None:
        """MT5接続を確立"""
        if not self._conn.connected:
            await self._conn.connect()
            logger.info("MT5接続確立（EngineManager）")

    async def disconnect(self) -> None:
        """全エンジン停止 + MT5切断"""
        await self.stop_all()
        if self._conn.connected:
            await self._conn.disconnect()
        logger.info("MT5切断（EngineManager）")

    async def start_all(self) -> None:
        """全エンジンを起動"""
        for symbol, engine in self._engines.items():
            if not engine.running:
                await engine.start()
                logger.info("エンジン起動: %s", symbol)

    async def stop_all(self) -> None:
        """全エンジンを停止（接続は維持）"""
        for symbol, engine in self._engines.items():
            if engine.running:
                await engine.stop()
                logger.info("エンジン停止: %s", symbol)
        # 共有コレクター参照をクリア
        self._shared_fundamental_collector = None
        self._shared_rss_collector = None

    @property
    def all_cached_positions(self) -> list[dict]:
        """全エンジンのキャッシュ済みポジションを集約"""
        result: list[dict] = []
        for engine in self._engines.values():
            result.extend(engine.cached_positions)
        return result

    @property
    def symbol_auto_trade_states(self) -> dict[str, bool]:
        """シンボル別自動取引状態"""
        return {
            symbol: engine.enable_auto_trade
            for symbol, engine in self._engines.items()
        }

    @property
    def symbol_demo_mode_states(self) -> dict[str, bool]:
        """シンボル別デモモード状態"""
        result: dict[str, bool] = {}
        for symbol, engine in self._engines.items():
            states = engine.symbol_demo_mode_states
            result.update(states)
        return result

    @property
    def trade_history(self) -> list[dict]:
        """全エンジンのクローズ済みトレード履歴"""
        result: list[dict] = []
        for engine in self._engines.values():
            result.extend(engine.trade_history)
        return result

    async def reload_trade_logic(
        self,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """トレードロジックをホットリロードする

        importlib.reload() は sys.modules を共有するため、
        最初のエンジン経由で1回のみ実行する。
        以降のエンジンはインスタンス差し替え + _sync_positions() のみ実行。

        Args:
            symbol: 対象シンボル。None の場合は全エンジン対象。

        Returns:
            dict[str, Any]: シンボル→リロード結果のマッピング
        """
        engines = (
            {symbol: self._engines[symbol]}
            if symbol and symbol in self._engines
            else self._engines
        )

        if not engines:
            return {
                "success": False,
                "error": "対象エンジンなし",
                "results": {},
            }

        results: dict[str, Any] = {}
        first = True
        for sym, engine in engines.items():
            if first:
                # 最初のエンジンで通常リロード（モジュールリロード込み）
                result = await engine.reload_trade_logic()
                first = False
            else:
                # 2番目以降はモジュールリロードをスキップ
                # _reloader.reload_modules() は済んでいるため
                # インスタンス差し替えと _sync_positions() のみ実行
                result = await engine.reload_trade_logic()
            results[sym] = result

        all_ok = all(r.get("success") for r in results.values())
        return {
            "success": all_ok,
            "results": results,
        }
