"""MT5トレード実行

TradeExecutor ProtocolのMT5実装。
"""

from __future__ import annotations

import logging

from autotrader.adapters.mt5.connection import MT5ConnectionManager
from autotrader.adapters.mt5.constants import (
    ORDER_FILLING_FOK,
    ORDER_FILLING_IOC,
    ORDER_FILLING_RETURN,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    SUCCESS_RETCODES,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_SLTP,
)
from autotrader.adapters.mt5.converters import (
    mt5_position_to_entity,
    signal_to_mt5_request,
)
from autotrader.core.entities import Position, Signal
from autotrader.core.enums import SignalType
from autotrader.core.interfaces.trade_executor import ExecutionResult

logger = logging.getLogger(__name__)


class MT5TradeExecutor:
    """MT5トレード実行

    magic_numberフィルタでAutoTraderV4のポジションのみ操作。

    Attributes:
        _conn: MT5接続マネージャ
        _magic: マジックナンバー
        _deviation: 許容スリッページ
        _symbol: デフォルトシンボル
    """

    def __init__(
        self,
        conn: MT5ConnectionManager,
        magic: int,
        deviation: int = 20,
        symbol: str = "USDJPY",
    ) -> None:
        """初期化

        Args:
            conn: MT5接続マネージャ
            magic: マジックナンバー
            deviation: 許容スリッページ
            symbol: デフォルトシンボル
        """
        self._conn = conn
        self._magic = magic
        self._deviation = deviation
        self._symbol = symbol

    def open_position(
        self,
        signal: Signal,
        volume: float,
    ) -> ExecutionResult:
        """ポジションを開く

        Args:
            signal: シグナル
            volume: ロット数

        Returns:
            ExecutionResult: 実行結果
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.open_position_async(signal, volume)
        )

    async def _get_filling_mode(
        self, symbol: str,
    ) -> int:
        """シンボルの対応フィリングモードを取得

        symbol_info.filling_modeビットマスクから判定:
        - bit0(1): FOK対応
        - bit1(2): IOC対応
        - どちらもなし: RETURN

        Args:
            symbol: シンボル名

        Returns:
            int: フィリングモード定数
        """
        async with self._conn.session() as transport:
            info = await transport.symbol_info(symbol)
        filling_mode = int(info.get("filling_mode", 0))
        if filling_mode & 1:
            return ORDER_FILLING_FOK
        if filling_mode & 2:
            return ORDER_FILLING_IOC
        return ORDER_FILLING_RETURN

    async def open_position_async(
        self,
        signal: Signal,
        volume: float,
    ) -> ExecutionResult:
        """ポジションを非同期で開く

        Args:
            signal: シグナル
            volume: ロット数

        Returns:
            ExecutionResult: 実行結果
        """
        filling = await self._get_filling_mode(signal.symbol)
        async with self._conn.order_session() as transport:
            tick = await transport.symbol_info_tick(signal.symbol)
            if not tick:
                return ExecutionResult(
                    success=False,
                    message=f"ティック取得失敗: {signal.symbol}",
                )

            # シンボルのpoint値取得（SL/TP pips→価格変換用）
            sym_info = await transport.symbol_info(signal.symbol)
            point = (
                float(sym_info.get("point", 0.0))
                if sym_info else None
            )

            request = signal_to_mt5_request(
                signal, volume, tick,
                magic=self._magic,
                deviation=self._deviation,
                filling_type=filling,
                point=point,
            )

            logger.info(
                "注文送信: %s %s %.2f lots @ %.3f "
                "sl=%.3f tp=%.3f filling=%d",
                signal.signal_type.value,
                signal.symbol,
                volume,
                request["price"],
                request.get("sl", 0.0),
                request.get("tp", 0.0),
                filling,
            )

            result = await transport.order_send(request)

        retcode = result.get("retcode", -1)
        if retcode in SUCCESS_RETCODES:
            ticket = int(result.get("order", 0))
            logger.info(
                "注文成功: ticket=%d retcode=%d",
                ticket, retcode,
            )
            return ExecutionResult(
                success=True,
                ticket=ticket,
                message=str(result.get("comment", "")),
            )

        msg = (
            f"注文失敗: retcode={retcode} "
            f"comment={result.get('comment', '')}"
        )
        logger.error(msg)
        return ExecutionResult(success=False, message=msg)

    def close_position(
        self,
        position: Position,
        reason: str,
    ) -> ExecutionResult:
        """ポジションを閉じる

        Args:
            position: ポジション
            reason: 決済理由

        Returns:
            ExecutionResult: 実行結果
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.close_position_async(position, reason)
        )

    async def close_position_async(
        self,
        position: Position,
        reason: str,
    ) -> ExecutionResult:
        """ポジションを非同期で閉じる

        Args:
            position: ポジション
            reason: 決済理由

        Returns:
            ExecutionResult: 実行結果
        """
        return await self._close_volume(
            position, position.volume, reason
        )

    def close_partial(
        self,
        position: Position,
        volume: float,
        reason: str,
    ) -> ExecutionResult:
        """ポジションを部分決済

        Args:
            position: ポジション
            volume: 決済ロット数
            reason: 決済理由

        Returns:
            ExecutionResult: 実行結果
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.close_partial_async(position, volume, reason)
        )

    async def close_partial_async(
        self,
        position: Position,
        volume: float,
        reason: str,
    ) -> ExecutionResult:
        """ポジションを非同期で部分決済

        Args:
            position: ポジション
            volume: 決済ロット数
            reason: 決済理由

        Returns:
            ExecutionResult: 実行結果
        """
        return await self._close_volume(position, volume, reason)

    async def _close_volume(
        self,
        position: Position,
        volume: float,
        reason: str,
    ) -> ExecutionResult:
        """指定ロット数を決済（共通ロジック）

        Args:
            position: ポジション
            volume: 決済ロット数
            reason: 決済理由

        Returns:
            ExecutionResult: 実行結果
        """
        # 反対方向の成行注文で決済
        is_buy = position.signal_type == SignalType.BUY
        close_type = ORDER_TYPE_SELL if is_buy else ORDER_TYPE_BUY
        filling = await self._get_filling_mode(position.symbol)

        async with self._conn.order_session() as transport:
            tick = await transport.symbol_info_tick(position.symbol)
            if not tick:
                return ExecutionResult(
                    success=False,
                    message=f"ティック取得失敗: {position.symbol}",
                )

            # 決済価格: BUYポジション→bid、SELLポジション→ask
            price = (
                float(tick.get("bid", 0)) if is_buy
                else float(tick.get("ask", 0))
            )

            request = {
                "action": TRADE_ACTION_DEAL,
                "symbol": position.symbol,
                "volume": round(volume, 2),
                "type": close_type,
                "position": position.ticket,
                "price": price,
                "deviation": self._deviation,
                "magic": self._magic,
                "comment": f"AT4_{reason[:16]}",
                "type_time": 0,
                "type_filling": filling,
            }

            logger.info(
                "決済送信: ticket=%d %.2f lots reason=%s",
                position.ticket, volume, reason,
            )

            result = await transport.order_send(request)

        retcode = result.get("retcode", -1)
        if retcode in SUCCESS_RETCODES:
            # MT5 OrderSendResult から実際の約定価格を取得
            exec_price = float(result.get("price", 0)) or price
            logger.info(
                "決済成功: ticket=%d retcode=%d price=%.5f",
                position.ticket, retcode, exec_price,
            )
            return ExecutionResult(
                success=True,
                ticket=int(result.get("order", 0)),
                message=reason,
                exit_price=exec_price,
            )

        msg = (
            f"決済失敗: ticket={position.ticket} "
            f"retcode={retcode} "
            f"comment={result.get('comment', '')}"
        )
        logger.error(msg)
        return ExecutionResult(success=False, message=msg)

    def modify_position(
        self,
        position: Position,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> ExecutionResult:
        """ポジションを修正

        Args:
            position: ポジション
            stop_loss: 新しいSL
            take_profit: 新しいTP

        Returns:
            ExecutionResult: 実行結果
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.modify_position_async(
                position, stop_loss, take_profit
            )
        )

    async def modify_position_async(
        self,
        position: Position,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> ExecutionResult:
        """ポジションを非同期で修正

        Args:
            position: ポジション
            stop_loss: 新しいSL
            take_profit: 新しいTP

        Returns:
            ExecutionResult: 実行結果
        """
        request = {
            "action": TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": position.ticket,
            "magic": self._magic,
        }
        if stop_loss is not None:
            request["sl"] = stop_loss
        else:
            request["sl"] = position.stop_loss or 0.0

        if take_profit is not None:
            request["tp"] = take_profit
        else:
            request["tp"] = position.take_profit or 0.0

        async with self._conn.order_session() as transport:
            result = await transport.order_send(request)

        retcode = result.get("retcode", -1)
        if retcode in SUCCESS_RETCODES:
            logger.info(
                "SL/TP変更成功: ticket=%d SL=%.3f TP=%.3f",
                position.ticket,
                request.get("sl", 0),
                request.get("tp", 0),
            )
            return ExecutionResult(
                success=True,
                ticket=position.ticket,
                message="SL/TP変更成功",
            )

        msg = (
            f"SL/TP変更失敗: ticket={position.ticket} "
            f"retcode={retcode}"
        )
        logger.error(msg)
        return ExecutionResult(success=False, message=msg)

    def get_open_positions(
        self, symbol: str | None = None
    ) -> list[Position]:
        """オープンポジションを取得（同期版）

        非同期版がNone（MT5接続エラー）を返した場合は
        空リストにフォールバックする。バックテスト等の
        同期呼び出し元はリストを期待するため。

        Args:
            symbol: シンボル（Noneで全て）

        Returns:
            list[Position]: ポジションリスト
        """
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            self.get_open_positions_async(symbol)
        )
        if result is None:
            return []
        return result

    async def get_open_positions_async(
        self, symbol: str | None = None
    ) -> list[Position] | None:
        """オープンポジションを非同期で取得

        magic_numberフィルタで自動トレーダのポジションのみ返却。

        Args:
            symbol: シンボル（Noneで全て）

        Returns:
            list[Position] | None: ポジションリスト。
                MT5接続エラー時はNone。
        """
        async with self._conn.session() as transport:
            raw_positions = await transport.positions_get(symbol)

        # MT5接続エラー時はNoneを伝搬
        if raw_positions is None:
            return None

        # magic_numberフィルタ
        positions = []
        for raw in raw_positions:
            if int(raw.get("magic", 0)) == self._magic:
                positions.append(mt5_position_to_entity(raw))

        return positions

    async def get_deal_by_position_async(
        self,
        ticket: int,
        lookback_seconds: int = 300,
    ) -> dict | None:
        """ポジション決済約定をMT5履歴から取得。

        Args:
            ticket: MT5ポジションID
            lookback_seconds: 遡る秒数（デフォルト5分）

        Returns:
            dict | None: price, profit, reason_code を含む辞書。
                未取得の場合はNone。
        """
        import time
        date_to = int(time.time()) + 60
        date_from = date_to - lookback_seconds
        try:
            async with self._conn.session() as transport:
                deals = await transport.history_deals_get(
                    date_from, date_to
                )
        except Exception as e:
            logger.warning("約定履歴取得失敗: %s", e)
            return None

        # DEAL_ENTRY_OUT = 1（決済約定）
        for deal in deals:
            if (
                deal.get("position_id") == ticket
                and deal.get("entry") == 1
            ):
                return {
                    "price": float(deal.get("price", 0.0)),
                    "profit": float(deal.get("profit", 0.0)),
                    "reason_code": int(deal.get("reason", -1)),
                }
        return None

    async def get_deal_by_position_id_async(
        self,
        ticket: int,
    ) -> dict | None:
        """ポジションIDで決済約定をMT5全履歴から取得（ゴースト用）

        時間範囲を指定しないため、エンジン停止が
        数日間でも確実にdealを取得可能。

        Args:
            ticket: MT5ポジションID

        Returns:
            dict | None: price, profit, reason_code, time
                を含む辞書。未取得の場合はNone。
        """
        try:
            async with self._conn.session() as transport:
                deals = (
                    await transport
                    .history_deals_get_by_position(ticket)
                )
        except Exception as e:
            logger.warning(
                "約定履歴取得失敗(position=%d): %s",
                ticket, e,
            )
            return None

        # DEAL_ENTRY_OUT = 1（決済約定）
        for deal in deals:
            if deal.get("entry") == 1:
                return {
                    "price": float(
                        deal.get("price", 0.0)
                    ),
                    "profit": float(
                        deal.get("profit", 0.0)
                    ),
                    "reason_code": int(
                        deal.get("reason", -1)
                    ),
                    "time": int(
                        deal.get("time", 0)
                    ),
                }
        return None
