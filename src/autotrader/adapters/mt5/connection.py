"""MT5接続管理

Transport ABC + DirectTransport + BridgeTransport + ConnectionManager
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from autotrader.adapters.mt5.config import MT5Config
from autotrader.adapters.mt5.exceptions import (
    MT5BridgeError,
    MT5ConnectionError,
)

logger = logging.getLogger(__name__)


class MT5Transport(ABC):
    """MT5トランスポート抽象クラス

    DirectTransport / BridgeTransport の共通インターフェース。
    """

    @abstractmethod
    async def initialize(self) -> bool:
        """MT5初期化

        Returns:
            bool: 成功か
        """
        ...

    @abstractmethod
    async def login(
        self, login: int, password: str, server: str
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
    ) -> list[dict]:
        """オープンポジション取得

        Args:
            symbol: シンボル（Noneで全て）

        Returns:
            list[dict]: ポジションデータ
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
                "Windows環境でpip install MetaTrader5を実行してください。"
            ) from e

        kwargs: dict[str, Any] = {}
        if self._terminal_path:
            kwargs["path"] = self._terminal_path

        loop = asyncio.get_event_loop()
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
        self, login: int, password: str, server: str
    ) -> bool:
        """MT5ログイン"""
        loop = asyncio.get_event_loop()
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
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._mt5.shutdown)

    async def account_info(self) -> dict:
        """口座情報取得"""
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(
            None, self._mt5.account_info
        )
        if info is None:
            return {}
        return info._asdict()

    async def symbol_info(self, symbol: str) -> dict:
        """シンボル情報取得"""
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(
            None, lambda: self._mt5.symbol_info(symbol)
        )
        if info is None:
            return {}
        return info._asdict()

    async def symbol_info_tick(self, symbol: str) -> dict:
        """ティック情報取得"""
        loop = asyncio.get_event_loop()
        tick = await loop.run_in_executor(
            None, lambda: self._mt5.symbol_info_tick(symbol)
        )
        if tick is None:
            return {}
        return tick._asdict()

    async def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict]:
        """直近N本のローソク足取得"""
        loop = asyncio.get_event_loop()
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
        dt_from = datetime.fromtimestamp(date_from, tz=timezone.utc)
        dt_to = datetime.fromtimestamp(date_to, tz=timezone.utc)

        loop = asyncio.get_event_loop()
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
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: self._mt5.order_send(request)
        )
        if result is None:
            return {"retcode": -1, "comment": "order_send returned None"}
        return result._asdict()

    async def positions_get(
        self, symbol: str | None = None
    ) -> list[dict]:
        """オープンポジション取得"""
        loop = asyncio.get_event_loop()
        if symbol:
            positions = await loop.run_in_executor(
                None,
                lambda: self._mt5.positions_get(symbol=symbol),
            )
        else:
            positions = await loop.run_in_executor(
                None, self._mt5.positions_get
            )
        if positions is None:
            return []
        return [p._asdict() for p in positions]

    async def history_deals_get(
        self, date_from: int, date_to: int
    ) -> list[dict]:
        """約定履歴取得"""
        from datetime import datetime, timezone
        dt_from = datetime.fromtimestamp(date_from, tz=timezone.utc)
        dt_to = datetime.fromtimestamp(date_to, tz=timezone.utc)

        loop = asyncio.get_event_loop()
        deals = await loop.run_in_executor(
            None,
            lambda: self._mt5.history_deals_get(dt_from, dt_to),
        )
        if deals is None:
            return []
        return [d._asdict() for d in deals]


class BridgeTransport(MT5Transport):
    """WSL→WindowsブリッジTransport

    JSON-RPC over TCPソケットでWindows側のブリッジサーバと通信。
    メッセージフレーミング: 4バイト長さプレフィックス + JSON本文
    """

    def __init__(
        self, host: str = "localhost", port: int = 18812,
        timeout: float = 10.0,
    ) -> None:
        """初期化

        Args:
            host: ブリッジホスト
            port: ブリッジポート
            timeout: タイムアウト秒
        """
        self._host = host
        self._port = port
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0

    async def _connect(self) -> None:
        """TCP接続確立"""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._timeout,
            )
        except (OSError, asyncio.TimeoutError) as e:
            raise MT5BridgeError(
                f"ブリッジ接続失敗 {self._host}:{self._port}: {e}"
            ) from e

    async def _disconnect(self) -> None:
        """TCP切断"""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _send_request(
        self, method: str, params: dict | None = None
    ) -> Any:
        """JSON-RPCリクエスト送信

        Args:
            method: メソッド名
            params: パラメータ

        Returns:
            Any: レスポンスのresultフィールド

        Raises:
            MT5BridgeError: 通信エラー
        """
        if not self._writer or self._writer.is_closing():
            await self._connect()

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._request_id,
        }

        data = json.dumps(request).encode("utf-8")
        # 長さプレフィックス（4バイト、ビッグエンディアン）
        header = struct.pack(">I", len(data))

        try:
            self._writer.write(header + data)
            await asyncio.wait_for(
                self._writer.drain(), timeout=self._timeout
            )

            # レスポンス読み取り
            resp_header = await asyncio.wait_for(
                self._reader.readexactly(4),  # type: ignore[union-attr]
                timeout=self._timeout,
            )
            resp_len = struct.unpack(">I", resp_header)[0]
            resp_data = await asyncio.wait_for(
                self._reader.readexactly(resp_len),  # type: ignore[union-attr]
                timeout=self._timeout,
            )

            response = json.loads(resp_data.decode("utf-8"))

            if "error" in response:
                err = response["error"]
                raise MT5BridgeError(
                    f"RPC error [{err.get('code', -1)}]: "
                    f"{err.get('message', 'unknown')}"
                )

            return response.get("result")

        except (
            OSError,
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
        ) as e:
            await self._disconnect()
            raise MT5BridgeError(
                f"ブリッジ通信エラー: {e}"
            ) from e

    async def initialize(self) -> bool:
        """ブリッジ経由でMT5初期化"""
        await self._connect()
        result = await self._send_request("initialize")
        return bool(result)

    async def login(
        self, login: int, password: str, server: str
    ) -> bool:
        """ブリッジ経由でMT5ログイン"""
        result = await self._send_request(
            "login",
            {"login": login, "password": password, "server": server},
        )
        return bool(result)

    async def shutdown(self) -> None:
        """ブリッジ経由でMT5シャットダウン"""
        try:
            await self._send_request("shutdown")
        except MT5BridgeError:
            pass
        await self._disconnect()

    async def account_info(self) -> dict:
        """口座情報取得"""
        result = await self._send_request("account_info")
        return result or {}

    async def symbol_info(self, symbol: str) -> dict:
        """シンボル情報取得"""
        result = await self._send_request(
            "symbol_info", {"symbol": symbol}
        )
        return result or {}

    async def symbol_info_tick(self, symbol: str) -> dict:
        """ティック情報取得"""
        result = await self._send_request(
            "symbol_info_tick", {"symbol": symbol}
        )
        return result or {}

    async def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict]:
        """直近N本のローソク足取得"""
        result = await self._send_request(
            "copy_rates_from_pos",
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "start_pos": start_pos,
                "count": count,
            },
        )
        return result or []

    async def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        date_from: int,
        date_to: int,
    ) -> list[dict]:
        """期間指定ローソク足取得"""
        result = await self._send_request(
            "copy_rates_range",
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "date_from": date_from,
                "date_to": date_to,
            },
        )
        return result or []

    async def order_send(self, request: dict) -> dict:
        """注文送信"""
        result = await self._send_request(
            "order_send", {"request": request}
        )
        return result or {}

    async def positions_get(
        self, symbol: str | None = None
    ) -> list[dict]:
        """オープンポジション取得"""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        result = await self._send_request("positions_get", params)
        return result or []

    async def history_deals_get(
        self, date_from: int, date_to: int
    ) -> list[dict]:
        """約定履歴取得"""
        result = await self._send_request(
            "history_deals_get",
            {"date_from": date_from, "date_to": date_to},
        )
        return result or []


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
            MT5Transport: トランスポートインスタンス
        """
        if self._config.transport == "direct":
            return DirectTransport(self._config.terminal_path)
        return BridgeTransport(
            host=self._config.bridge_host,
            port=self._config.bridge_port,
            timeout=self._config.timeout_sec,
        )

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

        for attempt in range(1, self._config.retry_count + 1):
            try:
                logger.info(
                    "MT5接続試行 %d/%d (%s)",
                    attempt,
                    self._config.retry_count,
                    self._config.transport,
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
                return True

            except (MT5ConnectionError, MT5BridgeError) as e:
                last_error = e
                logger.warning(
                    "MT5接続失敗 (試行%d): %s", attempt, e
                )
                if attempt < self._config.retry_count:
                    await asyncio.sleep(
                        self._config.retry_delay_sec
                    )

        self._connected = False
        raise MT5ConnectionError(
            f"MT5接続失敗（{self._config.retry_count}回試行）: "
            f"{last_error}"
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
        need_check = (now - self._last_health_check) > interval

        if self._connected and not need_check:
            return

        if self._connected and need_check:
            if await self.health_check():
                return
            logger.warning("ヘルスチェック失敗、再接続開始")

        await self.connect()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[MT5Transport, None]:
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
