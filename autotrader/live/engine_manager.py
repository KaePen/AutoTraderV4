"""マルチシンボルエンジンマネージャー

シンボルごとに独立した LiveTradingEngine インスタンスを管理し、
MT5接続とデータプロバイダを全エンジンで共有する。
ポートフォリオレベルのDD監視・サーキットブレーカーを担う。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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

# ポートフォリオDD閾値
DD_WARNING_PCT = 3.0    # 警告表示
DD_EMERGENCY_PCT = 5.0  # 全決済+エントリー停止


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

    def __init__(
        self,
        mt5_config: MT5Config,
        global_max_positions: int = 0,
        global_max_exposure_lot: float = 0.0,
    ) -> None:
        """初期化

        Args:
            mt5_config: MT5接続設定
            global_max_positions: 全ペア合計の最大ポジション数
                （0=無制限）
            global_max_exposure_lot: 全ペア合計の最大ロット数
                （0.0=無制限）
        """
        self._mt5_config = mt5_config
        self._conn = MT5ConnectionManager(mt5_config)
        self._data_provider = MT5DataProvider(self._conn)
        self._engines: dict[str, LiveTradingEngine] = {}
        # グローバルポジション/エクスポージャー制限
        self._global_max_positions = global_max_positions
        self._global_max_exposure_lot = global_max_exposure_lot
        # 共有コレクター（最初のエンジン起動時に初期化）
        self._shared_fundamental_collector = None
        self._shared_rss_collector = None

        # ポートフォリオDD監視
        self._peak_equity: float = 0.0
        self._current_dd_pct: float = 0.0
        self._dd_warning_active: bool = False  # DD >= 3%
        self._dd_emergency_active: bool = False  # DD >= 5%
        self._dd_emergency_at: datetime | None = None
        self._emergency_close_done: bool = False

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
        # グローバル制限コールバックを注入
        engine.set_global_limit_callbacks(
            get_global_position_count=(
                self.get_global_position_count
            ),
            get_global_exposure_lot=(
                self.get_global_exposure_lot
            ),
            global_max_positions=(
                self._global_max_positions
            ),
            global_max_exposure_lot=(
                self._global_max_exposure_lot
            ),
        )
        # ポートフォリオDD監視用にマネージャー参照を注入
        engine._engine_manager = self
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

    def get_global_position_count(self) -> int:
        """全エンジンの合計オープンポジション数を取得

        Returns:
            int: 合計ポジション数
        """
        return sum(
            len(e.cached_positions)
            for e in self._engines.values()
        )

    def get_global_exposure_lot(self) -> float:
        """全エンジンの合計エクスポージャー（ロット）を取得

        Returns:
            float: 合計ロット数
        """
        total = 0.0
        for engine in self._engines.values():
            for pos in engine.cached_positions:
                total += pos.get("volume", 0.0)
        return total

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

    # =========================================================
    # ポートフォリオDD監視
    # =========================================================

    @property
    def dd_warning_active(self) -> bool:
        """DD >= 3% 警告状態"""
        return self._dd_warning_active

    @property
    def dd_emergency_active(self) -> bool:
        """DD >= 5% 緊急停止状態"""
        return self._dd_emergency_active

    @property
    def current_dd_pct(self) -> float:
        """現在のポートフォリオDD(%)"""
        return self._current_dd_pct

    @property
    def peak_equity(self) -> float:
        """ピーク有効証拠金"""
        return self._peak_equity

    @property
    def dd_status(self) -> dict[str, Any]:
        """DD状態の辞書（WebUI用）"""
        return {
            "current_dd_pct": round(self._current_dd_pct, 3),
            "peak_equity": round(self._peak_equity, 0),
            "dd_warning_active": self._dd_warning_active,
            "dd_emergency_active": self._dd_emergency_active,
            "dd_emergency_at": (
                self._dd_emergency_at.isoformat()
                if self._dd_emergency_at
                else None
            ),
        }

    def update_portfolio_dd(
        self, equity: float,
    ) -> None:
        """ポートフォリオDDを更新しサーキットブレーカーを判定

        各エンジンのtickループから呼ばれる。

        Args:
            equity: 現在の口座有効証拠金
        """
        if equity <= 0:
            return

        # 緊急停止済みなら更新のみ（再発動しない）
        if self._dd_emergency_active:
            return

        # ピーク更新
        if equity > self._peak_equity:
            self._peak_equity = equity

        # DD計算
        if self._peak_equity > 0:
            self._current_dd_pct = (
                (self._peak_equity - equity)
                / self._peak_equity
                * 100
            )
        else:
            self._current_dd_pct = 0.0

        # 3% 警告
        self._dd_warning_active = (
            self._current_dd_pct >= DD_WARNING_PCT
        )

        # 5% 緊急停止
        if self._current_dd_pct >= DD_EMERGENCY_PCT:
            self._dd_emergency_active = True
            self._dd_emergency_at = datetime.now(
                tz=timezone.utc
            )
            logger.critical(
                "ポートフォリオDD %.2f%% >= %.1f%% — "
                "緊急停止発動: 全ポジション決済+エントリー停止",
                self._current_dd_pct,
                DD_EMERGENCY_PCT,
            )

    async def emergency_close_all(self) -> int:
        """全エンジンの全ポジションを緊急決済

        Returns:
            int: 決済したポジション数
        """
        if self._emergency_close_done:
            return 0

        closed = 0
        for symbol, engine in self._engines.items():
            positions = engine.cached_positions
            for pos_dict in positions:
                ticket = pos_dict.get("ticket")
                if ticket is None:
                    continue
                try:
                    # MT5ポジション取得
                    mt5_positions = (
                        await engine._executor
                        .get_open_positions_async(symbol)
                    )
                    if mt5_positions is None:
                        continue
                    for p in mt5_positions:
                        result = (
                            await engine._executor
                            .close_position_async(
                                p, "DD緊急決済"
                            )
                        )
                        if result.success:
                            closed += 1
                            logger.info(
                                "緊急決済: %s #%s",
                                symbol,
                                p.ticket,
                            )
                        else:
                            logger.error(
                                "緊急決済失敗: %s #%s: %s",
                                symbol,
                                p.ticket,
                                result.message,
                            )
                except Exception as e:
                    logger.error(
                        "緊急決済エラー: %s: %s",
                        symbol, e,
                    )

        self._emergency_close_done = True
        logger.critical(
            "緊急決済完了: %d ポジション決済", closed
        )
        return closed

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
