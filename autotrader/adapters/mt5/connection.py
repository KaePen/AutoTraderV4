"""MT5接続管理

Transport ABC + DirectTransport + ConnectionManager
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from autotrader.adapters.mt5.config import MT5Config
from autotrader.adapters.mt5.exceptions import MT5ConnectionError

logger = logging.getLogger(__name__)


async def _hide_mt5_window() -> None:
    """MT5ウィンドウを非表示にする

    win32guiでMetaTraderウィンドウを検索し非表示化。
    ベストエフォート: 失敗してもMT5接続に影響しない。
    """

    def _do_hide() -> None:
        try:
            import win32con  # type: ignore[import]
            import win32gui  # type: ignore[import]
        except ImportError:
            logger.debug(
                "pywin32未インストール: "
                "MT5ウィンドウ非表示をスキップ"
            )
            return

        hidden_count = 0

        def _enum_callback(
            hwnd: int, _: object
        ) -> None:
            nonlocal hidden_count
            title = win32gui.GetWindowText(hwnd)
            if "MetaTrader" in title:
                win32gui.ShowWindow(
                    hwnd, win32con.SW_HIDE
                )
                hidden_count += 1
                logger.debug(
                    "MT5ウィンドウ非表示: %s", title
                )

        win32gui.EnumWindows(_enum_callback, None)
        if hidden_count:
            logger.info(
                "MT5ウィンドウ %d件を非表示化",
                hidden_count,
            )
        else:
            logger.debug(
                "MT5ウィンドウが見つかりません"
            )

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _do_hide)
    except Exception:
        logger.warning(
            "MT5ウィンドウ非表示に失敗",
            exc_info=True,
        )


class MT5Transport(ABC):
    """MT5トランスポート抽象クラス"""

    @abstractmethod
    async def initialize(self) -> bool:
        """MT5初期化

        Returns:
            bool: 成功か
        """
        ...

    @abstractmethod
    async def login(
        self, login: int, password: str = "", server: str = ""
    ) -> bool:
        """MT5ログイン

        Args:
            login: ログインID
            password: パスワード
            server: サーバー名

        Returns:
            bool: 成功か
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """MT5シャットダウン"""
        ...

    @abstractmethod
    async def account_info(self) -> dict:
        """口座情報取得

        Returns:
            dict: 口座情報
        """
        ...

    @abstractmethod
    async def symbol_info(self, symbol: str) -> dict:
        """シンボル情報取得

        Args:
            symbol: シンボル名

        Returns:
            dict: シンボル情報
        """
        ...

    @abstractmethod
    async def symbol_info_tick(self, symbol: str) -> dict:
        """ティック情報取得

        Args:
            symbol: シンボル名

        Returns:
            dict: ティック情報（ask, bid, last等）
        """
        ...

    @abstractmethod
    async def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict]:
        """直近N本のローソク足取得

        Args:
            symbol: シンボル名
            timeframe: MT5時間足ID
            start_pos: 開始位置（0=最新）
            count: 取得本数

        Returns:
            list[dict]: レートデータ
        """
        ...

    @abstractmethod
    async def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        date_from: int,
        date_to: int,
    ) -> list[dict]:
        """期間指定ローソク足取得

        Args:
            symbol: シンボル名
            timeframe: MT5時間足ID
            date_from: 開始日時（UNIXエポック秒）
            date_to: 終了日時（UNIXエポック秒）

        Returns:
            list[dict]: レートデータ
        """
        ...

    @abstractmethod
    async def order_send(self, request: dict) -> dict:
        """注文送信

        Args:
            request: 注文リクエスト辞書

        Returns:
            dict: 注文結果
        """
        ...

    @abstractmethod
    async def positions_get(
        self, symbol: str | None = None
    ) -> list[dict] | None:
        """オープンポジション取得

        Args:
            symbol: シンボル（Noneで全て）

        Returns:
            list[dict] | None: ポジションデータ。
                MT5がNoneを返した場合（接続エラー等）は
                Noneをそのまま返す。
        """
        ...

    @abstractmethod
    async def history_deals_get(
        self, date_from: int, date_to: int
    ) -> list[dict]:
        """約定履歴取得

        Args:
            date_from: 開始日時（UNIXエポック秒）
            date_to: 終了日時（UNIXエポック秒）

        Returns:
            list[dict]: 約定データ
        """
        ...

    @abstractmethod
    async def history_deals_get_by_position(
        self, position_id: int
    ) -> list[dict]:
        """ポジションIDで約定履歴取得

        Args:
            position_id: MT5ポジションID

        Returns:
            list[dict]: 約定データ
        """
        ...

    @abstractmethod
    async def copy_ticks_from(
        self,
        symbol: str,
        date_from: int,
        count: int,
        flags: int = 0,
    ) -> list[dict]:
        """ティック履歴取得

        Args:
            symbol: シンボル名
            date_from: 開始日時（UNIXエポック秒）
            count: 取得件数
            flags: COPY_TICKS_ALL(0)/INFO(1)/TRADE(2)

        Returns:
            list[dict]: ティックデータ
        """
        ...


class DirectTransport(MT5Transport):
    """MetaTrader5パッケージ直接呼出トランスポート

    Windows環境でMetaTrader5パッケージが利用可能な場合に使用。
    """

    def __init__(self, terminal_path: str = "") -> None:
        """初期化

        Args:
            terminal_path: MT5ターミナルパス
        """
        self._terminal_path = terminal_path
        self._mt5: Any = None

    async def initialize(self) -> bool:
        """MT5初期化"""
        try:
            import MetaTrader5 as mt5  # type: ignore[import]
            self._mt5 = mt5
        except ImportError as e:
            raise MT5ConnectionError(
                "MetaTrader5パッケージが見つかりません。"
                "Windows環境でpip install MetaTrader5を"
                "実行してください。"
            ) from e

        kwargs: dict[str, Any] = {}
        if self._terminal_path:
            kwargs["path"] = self._terminal_path

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._mt5.initialize(**kwargs)
        )
        if not result:
            error = self._mt5.last_error()
            raise MT5ConnectionError(
                f"MT5初期化失敗: {error}"
            )
        return True

    async def login(
        self, login: int, password: str = "", server: str = ""
    ) -> bool:
        """MT5ログイン（passwordは省略可: MT5ターミナルの保存済み認証を使用）"""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._mt5.login(
                login, password=password, server=server
            ),
        )
        if not result:
            error = self._mt5.last_error()
            raise MT5ConnectionError(
                f"MT5ログイン失敗: {error}"
            )
        return True

    async def shutdown(self) -> None:
        """MT5シャットダウン"""
        if self._mt5:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._mt5.shutdown
            )

    async def account_info(self) -> dict:
        """口座情報取得"""
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None, self._mt5.account_info
        )
        if info is None:
            return {}
        return info._asdict()

    async def symbol_info(self, symbol: str) -> dict:
        """シンボル情報取得"""
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None, lambda: self._mt5.symbol_info(symbol)
        )
        if info is None:
            return {}
        return info._asdict()

    async def symbol_info_tick(self, symbol: str) -> dict:
        """ティック情報取得"""
        loop = asyncio.get_running_loop()
        tick = await loop.run_in_executor(
            None, lambda: self._mt5.symbol_info_tick(symbol)
        )
        if tick is None:
            return {}
        return tick._asdict()

    async def copy_rates_from_pos(
        self, symbol: str, timeframe: int,
        start_pos: int, count: int,
    ) -> list[dict]:
        """直近N本のローソク足取得"""
        loop = asyncio.get_running_loop()
        rates = await loop.run_in_executor(
            None,
            lambda: self._mt5.copy_rates_from_pos(
                symbol, timeframe, start_pos, count
            ),
        )
        if rates is None:
            return []
        return [dict(zip(rates.dtype.names, r)) for r in rates]

    async def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        date_from: int,
        date_to: int,
    ) -> list[dict]:
        """期間指定ローソク足取得"""
        from datetime import datetime, timezone
        dt_from = datetime.fromtimestamp(
            date_from, tz=timezone.utc
        )
        dt_to = datetime.fromtimestamp(
            date_to, tz=timezone.utc
        )

        loop = asyncio.get_running_loop()
        rates = await loop.run_in_executor(
            None,
            lambda: self._mt5.copy_rates_range(
                symbol, timeframe, dt_from, dt_to
            ),
        )
        if rates is None:
            return []
        return [dict(zip(rates.dtype.names, r)) for r in rates]

    async def order_send(self, request: dict) -> dict:
        """注文送信"""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._mt5.order_send(request)
        )
        if result is None:
            return {
                "retcode": -1,
                "comment": "order_send returned None",
            }
        return result._asdict()

    async def positions_get(
        self, symbol: str | None = None
    ) -> list[dict] | None:
        """オープンポジション取得

        Returns:
            list[dict] | None: ポジションデータ。
                MT5がNoneを返した場合（接続エラー等）は
                Noneをそのまま返す。
        """
        loop = asyncio.get_running_loop()
        if symbol:
            positions = await loop.run_in_executor(
                None,
                lambda: self._mt5.positions_get(
                    symbol=symbol
                ),
            )
        else:
            positions = await loop.run_in_executor(
                None, self._mt5.positions_get
            )
        if positions is None:
            return None
        return [p._asdict() for p in positions]

    async def history_deals_get(
        self, date_from: int, date_to: int
    ) -> list[dict]:
        """約定履歴取得"""
        from datetime import datetime, timezone
        dt_from = datetime.fromtimestamp(
            date_from, tz=timezone.utc
        )
        dt_to = datetime.fromtimestamp(
            date_to, tz=timezone.utc
        )

        loop = asyncio.get_running_loop()
        deals = await loop.run_in_executor(
            None,
            lambda: self._mt5.history_deals_get(
                dt_from, dt_to
            ),
        )
        if deals is None:
            return []
        return [d._asdict() for d in deals]

    async def history_deals_get_by_position(
        self, position_id: int
    ) -> list[dict]:
        """ポジションIDで約定履歴取得"""
        loop = asyncio.get_running_loop()
        deals = await loop.run_in_executor(
            None,
            lambda: self._mt5.history_deals_get(
                position=position_id
            ),
        )
        if deals is None:
            return []
        return [d._asdict() for d in deals]

    async def copy_ticks_from(
        self,
        symbol: str,
        date_from: int,
        count: int,
        flags: int = 0,
    ) -> list[dict]:
        """ティック履歴取得"""
        from datetime import datetime, timezone
        dt_from = datetime.fromtimestamp(
            date_from, tz=timezone.utc
        )

        loop = asyncio.get_running_loop()
        ticks = await loop.run_in_executor(
            None,
            lambda: self._mt5.copy_ticks_from(
                symbol, dt_from, count, flags
            ),
        )
        if ticks is None:
            return []
        return [
            dict(zip(ticks.dtype.names, t)) for t in ticks
        ]


class MT5ConnectionManager:
    """MT5接続マネージャ

    リトライ付き接続、ヘルスチェック、コンテキストマネージャ対応。

    Attributes:
        _config: MT5設定
        _transport: トランスポート
        _connected: 接続状態
        _last_health_check: 最後のヘルスチェック時刻
    """

    def __init__(self, config: MT5Config) -> None:
        """初期化

        Args:
            config: MT5設定
        """
        self._config = config
        self._transport = self._create_transport()
        self._connected = False
        self._last_health_check = 0.0

    def _create_transport(self) -> MT5Transport:
        """トランスポートを生成

        Returns:
            MT5Transport: DirectTransportインスタンス
        """
        return DirectTransport(self._config.terminal_path)

    @property
    def transport(self) -> MT5Transport:
        """トランスポート取得"""
        return self._transport

    @property
    def connected(self) -> bool:
        """接続状態"""
        return self._connected

    async def connect(self) -> bool:
        """MT5に接続（リトライ付き）

        Returns:
            bool: 接続成功か

        Raises:
            MT5ConnectionError: 全リトライ失敗
        """
        last_error: Exception | None = None

        for attempt in range(
            1, self._config.retry_count + 1
        ):
            try:
                logger.info(
                    "MT5接続試行 %d/%d (direct)",
                    attempt,
                    self._config.retry_count,
                )
                await self._transport.initialize()

                if self._config.login:
                    await self._transport.login(
                        self._config.login,
                        self._config.password,
                        self._config.server,
                    )

                self._connected = True
                self._last_health_check = time.time()
                logger.info("MT5接続成功")

                if self._config.hide_window:
                    await _hide_mt5_window()

                return True

            except MT5ConnectionError as e:
                last_error = e
                logger.warning(
                    "MT5接続失敗 (試行%d): %s",
                    attempt, e,
                )
                if attempt < self._config.retry_count:
                    await asyncio.sleep(
                        self._config.retry_delay_sec
                    )

        self._connected = False
        raise MT5ConnectionError(
            f"MT5接続失敗（{self._config.retry_count}回試行）"
            f": {last_error}"
        )

    async def disconnect(self) -> None:
        """MT5切断"""
        try:
            await self._transport.shutdown()
        except Exception as e:
            logger.warning("MT5切断時エラー: %s", e)
        finally:
            self._connected = False
            logger.info("MT5切断完了")

    async def health_check(self) -> bool:
        """ヘルスチェック

        Returns:
            bool: 正常か
        """
        if not self._connected:
            return False
        try:
            info = await self._transport.account_info()
            self._last_health_check = time.time()
            return bool(info)
        except Exception as e:
            logger.warning("ヘルスチェック失敗: %s", e)
            self._connected = False
            return False

    async def ensure_connected(self) -> None:
        """接続を保証（必要に応じて再接続）

        Raises:
            MT5ConnectionError: 再接続失敗
        """
        now = time.time()
        interval = self._config.health_check_interval_sec
        need_check = (
            (now - self._last_health_check) > interval
        )

        if self._connected and not need_check:
            return

        if self._connected and need_check:
            if await self.health_check():
                return
            logger.warning("ヘルスチェック失敗、再接続開始")

        await self.connect()

    @asynccontextmanager
    async def session(
        self,
    ) -> AsyncGenerator[MT5Transport, None]:
        """接続セッションコンテキストマネージャ

        Yields:
            MT5Transport: 接続済みトランスポート
        """
        await self.ensure_connected()
        try:
            yield self._transport
        except Exception:
            self._connected = False
            raise
