"""ポジション同期サービス

MT5⇔DB間のポジション同期・ゴーストレコード掃除・
管理状態の永続化を担当する。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from autotrader.adapters.mt5.data_provider import MT5DataProvider
from autotrader.adapters.mt5.exceptions import MT5DataError, MT5Error
from autotrader.adapters.mt5.trade_executor import MT5TradeExecutor
from autotrader.core.entities import Signal
from autotrader.core.enums import ExitReason, SignalType, Timeframe
from autotrader.core.event_bus import event_bus
from autotrader.core.exceptions import TradingError
from autotrader.decision.unified.position_manager import (
    PositionManager,
)

logger = logging.getLogger(__name__)


def _mt5_reason_to_exit_reason(reason_code: int) -> str:
    """MT5 DEAL_REASONコードをExitReason.valueに変換

    Args:
        reason_code: MT5のDEAL_REASONコード

    Returns:
        str: ExitReason の文字列値
    """
    _map = {
        0: ExitReason.MANUAL_CLOSE,  # CLIENT
        1: ExitReason.MANUAL_CLOSE,  # MOBILE
        2: ExitReason.MANUAL_CLOSE,  # WEB
        3: ExitReason.EXTERNAL_CLOSE,  # EXPERT（他EA）
        4: ExitReason.STOP_LOSS,  # SL
        5: ExitReason.TAKE_PROFIT,  # TP
        6: ExitReason.STOP_OUT,  # ストップアウト
    }
    return _map.get(reason_code, ExitReason.EXTERNAL_CLOSE).value


class PositionSyncService:
    """ポジション同期サービス

    MT5⇔DB間のポジション同期・ゴーストレコード掃除・
    管理状態の永続化を行う。

    Attributes:
        _executor: MT5トレード実行
        _data_provider: MT5データプロバイダ
        _pm: PositionManagerへの参照
    """

    def __init__(
        self,
        executor: MT5TradeExecutor,
        data_provider: MT5DataProvider,
        pm: PositionManager,
    ) -> None:
        """初期化

        Args:
            executor: MT5トレード実行
            data_provider: MT5データプロバイダ
            pm: PositionManagerへの参照
        """
        self._executor = executor
        self._data_provider = data_provider
        self._pm = pm

    @staticmethod
    def _get_pip_size(symbol: str) -> float:
        """通貨ペアのpipサイズを返す"""
        return 0.01 if "JPY" in symbol.upper() else 0.0001

    @staticmethod
    def _get_pip_value(symbol: str) -> float:
        """通貨ペアの1lot/1pipあたりの価値を返す"""
        return 1000.0 if "JPY" in symbol.upper() else 10.0

    def write_entry_to_db(
        self,
        ticket: int,
        signal: Signal,
        lot: float,
        entry_tick: dict | None,
    ) -> str | None:
        """エントリーをDBに記録

        Args:
            ticket: MT5チケットID
            signal: トレードシグナル
            lot: ロット数
            entry_tick: エントリー時のtick情報

        Returns:
            str | None: 作成されたtrade_id（失敗時None）
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.repositories import (
            TradeRepository,
        )
        from autotrader.config.settings import get_settings

        try:
            is_buy = signal.signal_type == SignalType.BUY
            if entry_tick:
                entry_price = float(
                    entry_tick.get("ask", 0)
                    if is_buy
                    else entry_tick.get("bid", 0)
                )
            else:
                entry_price = 0.0
            pip_size = self._get_pip_size(signal.symbol)
            sl_price = None
            tp_price = None
            if entry_price > 0:
                if signal.stop_loss and signal.stop_loss > 0:
                    sl_dist = signal.stop_loss * pip_size
                    sl_price = (
                        entry_price - sl_dist
                        if is_buy
                        else entry_price + sl_dist
                    )
                if signal.take_profit and signal.take_profit > 0:
                    tp_dist = signal.take_profit * pip_size
                    tp_price = (
                        entry_price + tp_dist
                        if is_buy
                        else entry_price - tp_dist
                    )
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                repo = TradeRepository(db)
                trade = repo.create(
                    symbol=signal.symbol,
                    signal_type=signal.signal_type.value,
                    volume=lot,
                    entry_price=entry_price,
                    opened_at=datetime.now(UTC),
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    ticket=ticket,
                )
                trade_id = trade.trade_id
            logger.info(
                "DB記録（エントリー）: trade_id=%s ticket=%d",
                trade_id,
                ticket,
            )
            return trade_id
        except Exception as e:
            logger.error("DB書き込みエラー（エントリー）: %s", e)
            return None

    def write_close_to_db(
        self,
        ticket: int,
        current_price: float,
        action_reason: str,
        profit_loss: float,
        active_symbol: str,
        open_trades: dict[int, str],
        closed_trades: list[dict],
    ) -> None:
        """決済をDBに更新

        Args:
            ticket: MT5チケットID
            current_price: 決済時の価格
            action_reason: 決済理由
            profit_loss: 確定損益（金額）
            active_symbol: アクティブシンボル
            open_trades: ticket→trade_idマッピング
            closed_trades: クローズ済みトレード履歴
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.repositories import (
            TradeRepository,
        )
        from autotrader.config.settings import get_settings

        trade_id = open_trades.get(ticket)
        if not trade_id:
            return
        try:
            pos = self._pm.get_position(str(ticket))
            pnl_pips = 0.0
            if pos and current_price > 0:
                pip_size = self._get_pip_size(active_symbol)
                price_diff = (
                    current_price - pos.entry_price
                    if pos.direction == SignalType.BUY
                    else pos.entry_price - current_price
                )
                pnl_pips = price_diff / pip_size
                # MT5から損益が取得できなかった場合、概算
                if profit_loss == 0.0 and abs(pnl_pips) > 0:
                    pip_val = self._get_pip_value(active_symbol)
                    _vol = pos.remaining_volume
                    profit_loss = round(pnl_pips * _vol * pip_val, 2)
            closed_at = datetime.now(UTC)
            db_url = get_settings().database_url
            # 最終SL/TP（トレーリング/BE移動後の値）
            final_sl = pos.current_sl if pos else None
            final_tp = pos.original_tp if pos else None
            with get_session(db_url) as db:
                repo = TradeRepository(db)
                repo.close(
                    trade_id=trade_id,
                    exit_price=current_price,
                    closed_at=closed_at,
                    exit_reason=action_reason,
                    profit_loss=profit_loss,
                    profit_loss_pips=pnl_pips,
                    final_stop_loss=final_sl,
                    final_take_profit=final_tp,
                )
            # DB書き込み成功後にpop
            open_trades.pop(ticket, None)
            closed_trades.append(
                {
                    "trade_id": trade_id,
                    "ticket": ticket,
                    "exit_price": current_price,
                    "exit_reason": action_reason,
                    "pnl_pips": round(pnl_pips, 1),
                    "closed_at": closed_at.isoformat(),
                }
            )
            logger.info(
                "DB記録（決済）: trade_id=%s ticket=%d"
                " pnl_pips=%.1f profit_loss=%.2f",
                trade_id,
                ticket,
                pnl_pips,
                profit_loss,
            )
            # EventBus経由で決済イベント通知
            event_bus.publish_nowait(
                "position.closed",
                {"symbol": active_symbol},
            )
        except Exception as e:
            logger.error("DB書き込みエラー（決済）: %s", e)

    async def handle_external_close(
        self,
        ticket: int,
        active_symbol: str,
        open_trades: dict[int, str],
        closed_trades: list[dict],
    ) -> None:
        """外部決済（SL/TP/手動）をMT5約定履歴から取得してDB記録

        Args:
            ticket: MT5ポジションID
            active_symbol: アクティブシンボル
            open_trades: ticket→trade_idマッピング
            closed_trades: クローズ済みトレード履歴
        """
        logger.info("外部決済検出（手動/SL/TP）: ticket=%d", ticket)
        deal = await self._executor.get_deal_by_position_async(ticket)
        if deal:
            exit_price = deal["price"]
            profit_loss = deal["profit"]
            exit_reason = _mt5_reason_to_exit_reason(deal["reason_code"])
            logger.info(
                "外部決済詳細: ticket=%d reason=%s price=%.5f profit=%.2f",
                ticket,
                exit_reason,
                exit_price,
                profit_loss,
            )
        else:
            # 約定履歴取得失敗時はティック価格をフォールバック
            exit_price = 0.0
            try:
                tick = await self._data_provider.get_tick(active_symbol)
                _bid = float(tick.get("bid", 0))
                _ask = float(tick.get("ask", 0))
                if _bid > 0 and _ask > 0:
                    exit_price = (_bid + _ask) / 2
                elif _bid > 0:
                    exit_price = _bid
            except (KeyError, ValueError, TypeError):
                pass
            profit_loss = 0.0
            exit_reason = ExitReason.EXTERNAL_CLOSE.value
            logger.warning(
                "外部決済の約定履歴取得失敗: ticket=%d"
                " → フォールバック価格=%.5f で記録",
                ticket,
                exit_price,
            )
        self.write_close_to_db(
            ticket,
            exit_price,
            exit_reason,
            profit_loss,
            active_symbol,
            open_trades,
            closed_trades,
        )
        # ローカルDB管理状態を削除
        self.delete_position_state(str(ticket))

    async def manage_positions(
        self,
        active_symbol: str,
        open_trades: dict[int, str],
        enable_auto_trade: bool,
        last_signal,
        execute_action_callback,
    ) -> list[dict]:
        """既存ポジションの管理

        PositionManager.evaluateで各ポジションを評価し、
        SL変更・部分決済・全決済をMT5で実行。

        Args:
            active_symbol: アクティブシンボル
            open_trades: ticket→trade_idマッピング
            enable_auto_trade: 自動取引ON/OFF
            last_signal: 直近シグナル
            execute_action_callback: アクション実行コールバック

        Returns:
            list[dict]: キャッシュ済みポジションリスト
        """
        # 全通貨ペアのポジションを取得（UI表示用）
        positions = await self._executor.get_open_positions_async(None)
        if positions is None:
            logger.warning("MT5ポジション取得失敗 — 管理スキップ")
            return []  # sentinel: 呼び出し元で判定
        current_tickets = {pos.ticket for pos in positions}

        # _open_tradesが未復元の場合、DBから復元
        if not open_trades and positions:
            self.restore_open_trades_from_db(
                [pos.ticket for pos in positions],
                open_trades,
            )

        # 外部決済の検出
        if open_trades:
            externally_closed = set(open_trades.keys()) - current_tickets
            for ticket in externally_closed:
                await self.handle_external_close(
                    ticket,
                    active_symbol,
                    open_trades,
                    [],
                )

        if not positions:
            return []

        # ATR取得
        _min_atr = 0.20 if "JPY" in active_symbol.upper() else 0.0020
        try:
            latest = await self._data_provider.get_latest_candle_async(
                active_symbol,
                Timeframe.M15,
            )
            _h = float(latest.get("high", 0))
            _l = float(latest.get("low", 0))
            atr = _h - _l if (_h > 0 and _l > 0) else _min_atr
            atr = max(atr, _min_atr)
        except (
            KeyError,
            ValueError,
            TypeError,
            MT5DataError,
        ):
            atr = 0.3

        # 現在のシグナル方向
        current_signal_type = None
        if last_signal:
            current_signal_type = last_signal.direction

        cache_list: list[dict] = []
        for position in positions:
            pip_factor = self._get_pip_size(position.symbol)
            pip_value = self._get_pip_value(position.symbol)
            pos_id = str(position.ticket)

            # ティック取得
            current_price = position.entry_price
            try:
                tick = await self._data_provider.get_tick(position.symbol)
                price_key = (
                    "bid" if position.signal_type == SignalType.BUY else "ask"
                )
                fetched = float(tick.get(price_key, 0))
                if fetched > 0:
                    current_price = fetched
            except (
                KeyError,
                ValueError,
                TypeError,
                MT5DataError,
            ):
                pass

            # キャッシュエントリ構築
            pip_diff = (current_price - position.entry_price) / pip_factor
            if position.signal_type == SignalType.SELL:
                pip_diff = -pip_diff

            managed = self._pm.get_position(pos_id)
            remaining_minutes = None
            max_hold_minutes = None
            elapsed_minutes = None
            if managed is not None and hasattr(managed, "entry_time"):
                elapsed_sec = (
                    datetime.now(UTC) - managed.entry_time
                ).total_seconds()
                elapsed_minutes = max(0, int(elapsed_sec / 60))
            elif hasattr(position.opened_at, "timestamp"):
                elapsed_sec = (
                    datetime.now(UTC) - position.opened_at
                ).total_seconds()
                elapsed_minutes = max(0, int(elapsed_sec / 60))
            if managed is not None:
                try:
                    from autotrader.config.tf_params_registry import (
                        get_holding_minutes,
                    )

                    dtf = getattr(
                        managed.plan,
                        "dynamic_entry_tf",
                        None,
                    )
                    etf = getattr(
                        managed.plan,
                        "entry_tf",
                        None,
                    )
                    entry_tf = (
                        dtf
                        if isinstance(dtf, str)
                        else etf
                        if isinstance(etf, str)
                        else None
                    )
                    if entry_tf is not None and elapsed_minutes is not None:
                        max_hold_minutes = get_holding_minutes(entry_tf)
                        remaining_minutes = max(
                            0,
                            int(max_hold_minutes - elapsed_minutes),
                        )
                except (
                    KeyError,
                    ValueError,
                    TypeError,
                    AttributeError,
                ):
                    pass

            cache_list.append(
                {
                    "position_id": str(position.ticket),
                    "trade_id": open_trades.get(position.ticket, ""),
                    "ticket": position.ticket,
                    "symbol": position.symbol,
                    "signal_type": position.signal_type.value,
                    "volume": position.volume,
                    "entry_price": position.entry_price,
                    "current_price": current_price,
                    "stop_loss": position.stop_loss,
                    "take_profit": position.take_profit,
                    "opened_at": (
                        position.opened_at.isoformat()
                        if hasattr(position.opened_at, "isoformat")
                        else str(position.opened_at)
                    ),
                    "unrealized_pnl": (pip_diff * position.volume * pip_value),
                    "unrealized_pnl_pips": pip_diff,
                    "remaining_minutes": remaining_minutes,
                    "max_hold_minutes": max_hold_minutes,
                    "elapsed_minutes": elapsed_minutes,
                }
            )

            if managed is None:
                continue
            if not enable_auto_trade:
                continue

            try:
                action = self._pm.evaluate(
                    position_id=pos_id,
                    current_price=current_price,
                    current_time=datetime.now(UTC),
                    atr=atr,
                    current_signal=current_signal_type,
                )
                await execute_action_callback(
                    position,
                    action,
                    current_price,
                )
                self.save_position_state(pos_id)
            except Exception as e:
                logger.error(
                    "ポジション管理エラー(ticket=%d): %s",
                    position.ticket,
                    e,
                )

        return cache_list

    async def sync_positions(
        self,
        active_symbol: str,
        open_trades: dict[int, str],
        bot,
    ) -> None:
        """MT5の既存ポジションとPositionManagerを同期

        Args:
            active_symbol: アクティブシンボル
            open_trades: ticket→trade_idマッピング
            bot: UnifiedTradeBot（configアクセス用）
        """
        positions = await self._executor.get_open_positions_async(
            active_symbol
        )

        if positions is None:
            logger.warning(
                "MT5ポジション取得失敗 — ゴースト掃除・復元をスキップ"
            )
            return

        active_tickets = {p.ticket for p in positions} if positions else set()
        await self.close_ghost_db_records(active_tickets)

        if not positions:
            logger.info("同期対象ポジションなし")
            return

        self.restore_open_trades_from_db(
            [pos.ticket for pos in positions],
            open_trades,
        )

        saved_states = self.load_position_states()

        logger.info("%d件のポジションを同期", len(positions))
        for pos in positions:
            pos_id = str(pos.ticket)
            if self._pm.get_position(pos_id) is None:
                import dataclasses as _dc

                from autotrader.decision.unified.mode_selector import (
                    TradingPlan,
                )

                plan = TradingPlan.create_universal(
                    bot.config,
                )
                plan = _dc.replace(
                    plan,
                    selection_reason="synced_at_startup",
                )

                self._pm.register_position(
                    position_id=pos_id,
                    direction=pos.signal_type,
                    entry_price=pos.entry_price,
                    entry_time=pos.opened_at,
                    sl=pos.stop_loss or pos.entry_price,
                    tp=pos.take_profit or pos.entry_price,
                    volume=pos.volume,
                    plan=plan,
                )
                logger.info(
                    "ポジション同期: ticket=%d %s %.2f lots",
                    pos.ticket,
                    pos.signal_type.value,
                    pos.volume,
                )

            managed = self._pm.get_position(pos_id)
            if managed is not None:
                managed.current_sl = pos.stop_loss or managed.current_sl
                managed.remaining_volume = pos.volume

                if pos_id in saved_states:
                    self._pm.import_state(pos_id, saved_states[pos_id])
                    logger.info(
                        "管理状態復元: ticket=%s flags=%s",
                        pos_id,
                        {
                            k: v
                            for k, v in saved_states[pos_id].items()
                            if isinstance(v, bool) and v
                        },
                    )

        active_ids = {str(p.ticket) for p in positions}
        self.cleanup_stale_states(active_ids)

    async def sync_positions_on_toggle(
        self,
        running: bool,
        active_symbol: str,
        open_trades: dict[int, str],
        bot,
    ) -> None:
        """自動取引ON切替時のポジション同期

        Args:
            running: エンジン実行中か
            active_symbol: アクティブシンボル
            open_trades: ticket→trade_idマッピング
            bot: UnifiedTradeBot
        """
        if not running:
            return
        try:
            await self.sync_positions(
                active_symbol,
                open_trades,
                bot,
            )
        except (
            MT5Error,
            TradingError,
            OSError,
            RuntimeError,
        ):
            logger.error(
                "トグル時ポジション同期失敗",
                exc_info=True,
            )

    async def close_ghost_db_records(
        self,
        active_tickets: set[int],
    ) -> None:
        """MT5に存在しないDBゴーストレコードを決済済みに更新

        Args:
            active_tickets: MT5で現在有効なチケットIDの集合
        """
        try:
            ghost_data = await asyncio.to_thread(
                self.fetch_ghost_records,
                active_tickets,
            )
            if not ghost_data:
                return

            updates: list[dict] = []
            for ticket, trade_id in ghost_data:
                deal = await self._executor.get_deal_by_position_id_async(
                    ticket
                )
                if deal:
                    closed_at = (
                        datetime.fromtimestamp(
                            deal["time"],
                            tz=UTC,
                        )
                        if deal["time"] > 0
                        else datetime.now(UTC)
                    )
                    updates.append(
                        {
                            "ticket": ticket,
                            "trade_id": trade_id,
                            "exit_price": deal["price"],
                            "profit_loss": deal["profit"],
                            "exit_reason": (
                                _mt5_reason_to_exit_reason(deal["reason_code"])
                            ),
                            "closed_at": closed_at,
                        }
                    )
                    logger.info(
                        "ゴースト復元(MT5履歴):"
                        " ticket=%s reason=%s"
                        " price=%.5f profit=%.2f",
                        ticket,
                        updates[-1]["exit_reason"],
                        updates[-1]["exit_price"],
                        updates[-1]["profit_loss"],
                    )
                else:
                    updates.append(
                        {
                            "ticket": ticket,
                            "trade_id": trade_id,
                            "exit_price": None,
                            "profit_loss": None,
                            "exit_reason": (ExitReason.GHOST_CLEANUP.value),
                            "closed_at": datetime.now(UTC),
                        }
                    )
                    logger.info(
                        "ゴーストレコード掃除: ticket=%s trade_id=%s",
                        ticket,
                        trade_id,
                    )

            if updates:
                await asyncio.to_thread(
                    self.apply_ghost_updates,
                    updates,
                )
                logger.info(
                    "ゴーストレコード %d件を is_open=false に更新",
                    len(updates),
                )
        except Exception as e:
            logger.warning("ゴーストレコード掃除スキップ: %s", e)

    def fetch_ghost_records(
        self,
        active_tickets: set[int],
        active_symbol: str = "",
    ) -> list[tuple[int, str]]:
        """DBからゴーストレコードを同期取得

        Args:
            active_tickets: MT5で有効なチケットIDの集合
            active_symbol: フィルタ対象シンボル（空文字で全シンボル）

        Returns:
            list[tuple[int, str]]: (ticket, trade_id)
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        db_url = get_settings().database_url
        with get_session(db_url) as db:
            query = db.query(TradeRecord).filter(
                TradeRecord.is_open.is_(True),
            )
            if active_symbol:
                query = query.filter(
                    TradeRecord.symbol == active_symbol,
                )
            records = query.all()
            return [
                (r.ticket, r.trade_id)
                for r in records
                if r.ticket not in active_tickets
            ]

    def apply_ghost_updates(
        self,
        updates: list[dict],
    ) -> None:
        """ゴーストレコードの決済情報をDBに同期書き込み

        Args:
            updates: 各ゴーストの更新データリスト
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        db_url = get_settings().database_url
        with get_session(db_url) as db:
            for upd in updates:
                record = (
                    db.query(TradeRecord)
                    .filter(
                        TradeRecord.ticket == upd["ticket"],
                        TradeRecord.is_open.is_(True),
                    )
                    .first()
                )
                if record is None:
                    continue
                record.is_open = False
                record.exit_reason = upd["exit_reason"]
                record.closed_at = upd["closed_at"]
                if upd["exit_price"] is not None:
                    record.exit_price = upd["exit_price"]
                if upd["profit_loss"] is not None:
                    record.profit_loss = upd["profit_loss"]
            db.flush()

    def restore_open_trades_from_db(
        self,
        tickets: list[int],
        open_trades: dict[int, str],
    ) -> None:
        """DBからオープントレードのtrade_idを復元

        Args:
            tickets: 現在のMT5チケットIDリスト
            open_trades: ticket→trade_idマッピング
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        try:
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                records = (
                    db.query(TradeRecord)
                    .filter(
                        TradeRecord.is_open.is_(True),
                        TradeRecord.ticket.in_(tickets),
                    )
                    .all()
                )
                for r in records:
                    if r.ticket not in open_trades:
                        open_trades[r.ticket] = r.trade_id
                        logger.info(
                            "trade_id復元: ticket=%d trade_id=%s",
                            r.ticket,
                            r.trade_id,
                        )
        except Exception as e:
            logger.warning("trade_id復元スキップ: %s", e)

    def load_position_states(
        self,
    ) -> dict[str, dict]:
        """ローカルDBから全管理状態を取得

        Returns:
            dict[str, dict]: position_id→状態dict
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        result: dict[str, dict] = {}
        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                for rec in repo.get_all():
                    result[rec.position_id] = {
                        "position_id": rec.position_id,
                        "highest_price": rec.highest_price,
                        "lowest_price": rec.lowest_price,
                        "highest_r": rec.highest_r,
                        "bars_held": rec.bars_held,
                        "trailing_activated": (rec.trailing_activated),
                        "partial_closed_1r": (rec.partial_closed_1r),
                        "partial_closed_2r": (rec.partial_closed_2r),
                        "tp_disabled": rec.tp_disabled,
                        "early_be_applied": (rec.early_be_applied),
                        "insurance_sl_applied": (rec.insurance_sl_applied),
                        "insurance_partial_applied": (
                            rec.insurance_partial_applied
                        ),
                        "half_r_partial_applied": (rec.half_r_partial_applied),
                    }
        except Exception as e:
            logger.warning("管理状態ロードスキップ: %s", e)
        return result

    def save_position_state(
        self,
        position_id: str,
    ) -> None:
        """ポジション管理状態をローカルDBに保存

        Args:
            position_id: ポジションID
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        state = self._pm.export_state(position_id)
        if state is None:
            return
        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                repo.upsert(state)
        except Exception as e:
            logger.warning(
                "管理状態保存エラー(pos=%s): %s",
                position_id,
                e,
            )

    def delete_position_state(
        self,
        position_id: str,
    ) -> None:
        """ポジション管理状態をローカルDBから削除

        Args:
            position_id: ポジションID
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                repo.delete(position_id)
        except Exception as e:
            logger.warning(
                "管理状態削除エラー(pos=%s): %s",
                position_id,
                e,
            )

    def cleanup_stale_states(
        self,
        active_ids: set[str],
    ) -> None:
        """MT5に存在しない陳腐化レコードを削除

        Args:
            active_ids: 現在MT5で有効なポジションIDの集合
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                stale_ids = [
                    rec.position_id
                    for rec in repo.get_all()
                    if rec.position_id not in active_ids
                ]
                for pid in stale_ids:
                    repo.delete(pid)
                    logger.info(
                        "陳腐化管理状態削除: %s",
                        pid,
                    )
        except Exception as e:
            logger.warning("陳腐化状態クリーンアップエラー: %s", e)
