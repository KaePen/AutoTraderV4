"""注文実行サービス

MT5への注文発行・ポジションアクション実行を担当する。
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime

from autotrader.adapters.mt5.data_provider import MT5DataProvider
from autotrader.adapters.mt5.exceptions import MT5DataError
from autotrader.adapters.mt5.trade_executor import MT5TradeExecutor
from autotrader.config.trading_params import get_pip_unit, get_pip_value
from autotrader.core.entities import Signal
from autotrader.core.enums import (
    MarketRegime,
    SignalType,
)
from autotrader.core.event_bus import event_bus
from autotrader.core.interfaces.position_sizing import SizingContext
from autotrader.decision.unified.position_manager import (
    ManagementActionType,
    PositionManager,
)
from autotrader.decision.unified.position_sizer import PositionSizer
from autotrader.decision.unified.trade_bot import UnifiedTradeBot
from autotrader.live.tick_entry_optimizer import TickEntryOptimizer

logger = logging.getLogger(__name__)


class OrderService:
    """注文実行サービス

    エントリー判定・注文発行・ポジション管理アクション実行を担う。

    Attributes:
        _executor: MT5トレード実行
        _data_provider: MT5データプロバイダ
        _bot: TradeBotへの参照
        _pm: PositionManagerへの参照
        _sizer: PositionSizerへの参照
        _tick_optimizer: TickEntryOptimizer
    """

    def __init__(
        self,
        executor: MT5TradeExecutor,
        data_provider: MT5DataProvider,
        bot: UnifiedTradeBot,
        pm: PositionManager,
        sizer: PositionSizer,
        tick_optimizer: TickEntryOptimizer,
    ) -> None:
        """初期化

        Args:
            executor: MT5トレード実行
            data_provider: MT5データプロバイダ
            bot: TradeBotへの参照
            pm: PositionManagerへの参照
            sizer: PositionSizerへの参照
            tick_optimizer: TickEntryOptimizer
        """
        self._executor = executor
        self._data_provider = data_provider
        self._bot = bot
        self._pm = pm
        self._sizer = sizer
        self._tick_optimizer = tick_optimizer

    @staticmethod
    def get_pip_size(symbol: str) -> float:
        """通貨ペアのpipサイズを返す（JPY系=0.01、その他=0.0001）

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            float: pipサイズ
        """
        return get_pip_unit(symbol)

    @staticmethod
    def get_pip_value(symbol: str) -> float:
        """通貨ペアの1lot/1pipあたりの価値を返す

        公式: 100,000 × pip_unit × quote_ccy_rate

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            float: pip価値（円）
        """
        return get_pip_value(symbol)

    def should_use_tick_optimizer(
        self,
        tick_entry_config,
        bot_config,
    ) -> bool:
        """ティック最適化を使用すべきか判定

        Args:
            tick_entry_config: TickEntryConfig
            bot_config: UnifiedBotConfig

        Returns:
            bool: ティック最適化を使用すべきか
        """
        if not tick_entry_config.enabled:
            return False
        # デモモードでは無効
        if getattr(bot_config, "demo_mode", False):
            return False
        return True

    async def execute_entry(
        self,
        signal: Signal,
        active_symbol: str,
        account_info,
        open_trades: dict[int, str],
        closed_trades: list[dict],
        cached_positions: list[dict],
        write_entry_to_db,
        save_position_state,
    ) -> None:
        """エントリー実行

        Args:
            signal: トレードシグナル
            active_symbol: アクティブシンボル
            account_info: 口座情報
            open_trades: ticket→trade_idマッピング
            closed_trades: クローズ済みトレード履歴
            cached_positions: キャッシュ済みポジション
            write_entry_to_db: DB書き込みコールバック
            save_position_state: 状態保存コールバック
        """
        # 既存ポジションチェック（設定値に基づく上限）
        positions = await self._executor.get_open_positions_async(
            active_symbol
        )
        # MT5接続エラー時はエントリーを安全にスキップ
        if positions is None:
            logger.warning("MT5ポジション取得失敗 — エントリースキップ")
            return
        cfg = self._bot.config
        base_max = (
            cfg.demo_max_positions if cfg.demo_mode else cfg.max_positions
        )
        bonus = getattr(cfg, "bonus_max_positions", 0)
        threshold = getattr(cfg, "bonus_score_threshold", 7.0)
        if (
            bonus > 0
            and signal.consensus_score is not None
            and signal.consensus_score >= threshold
        ):
            max_pos = base_max + bonus
        else:
            max_pos = base_max
        if len(positions) >= max_pos:
            logger.info(
                "既存ポジション上限(%d)、エントリースキップ",
                max_pos,
            )
            return

        # ロット計算
        if account_info is None:
            logger.warning("口座情報なし、エントリースキップ")
            return

        # signal.stop_lossはpips値
        sl_pips = (
            signal.stop_loss
            if signal.stop_loss is not None and signal.stop_loss > 0
            else 30.0
        )

        # SizingContextを作成
        regime = MarketRegime.RANGE
        if signal.regime:
            try:
                regime = MarketRegime(signal.regime)
            except ValueError:
                pass

        # 現在のエクスポージャーを計算
        _buy_lot = sum(
            p.volume for p in positions
            if p.signal_type == SignalType.BUY
        )
        _sell_lot = sum(
            p.volume for p in positions
            if p.signal_type == SignalType.SELL
        )
        _exposure_lot = _buy_lot + _sell_lot
        _same_dir_lot = (
            _buy_lot
            if signal.signal_type == SignalType.BUY
            else _sell_lot
        )

        # 連続負け数（直近のclosed_tradesから計算）
        _consec_losses = 0
        for t in reversed(closed_trades):
            _pnl = t.get("pnl_pips", 0)
            if _pnl < 0:
                _consec_losses += 1
            else:
                break

        # 現在のDD%（equity vs balance）
        _dd_pct = 0.0
        if account_info.balance > 0:
            _dd_pct = max(
                0.0,
                (
                    1.0
                    - account_info.equity
                    / account_info.balance
                )
                * 100.0,
            )

        sizing_ctx = SizingContext(
            equity=account_info.equity,
            sl_pips=sl_pips if sl_pips > 0 else 30.0,
            confidence=signal.confidence,
            regime=regime,
            consecutive_losses=_consec_losses,
            current_dd_pct=_dd_pct,
            initial_equity=account_info.balance,
            open_exposure_lot=_exposure_lot,
            open_same_direction_lot=_same_dir_lot,
        )
        sizing_result = self._sizer.calculate(sizing_ctx)

        if sizing_result.blocked:
            logger.warning("サイジング拒否: %s", sizing_result.reasoning)
            return

        lot = sizing_result.lot

        if lot <= 0:
            logger.warning("ロット計算結果=0、エントリースキップ")
            return

        # Signal にlotを付与
        signal_with_lot = signal.model_copy(update={"lot": lot})

        # MT5発注（発注直前のtick価格を取得してentry_priceに使用）
        entry_tick = await self._data_provider.get_tick(active_symbol)
        result = await self._executor.open_position_async(signal_with_lot, lot)

        if result.success:
            logger.info(
                "エントリー成功: ticket=%d %.2f lots",
                result.ticket or 0,
                lot,
            )
            # PositionManagerに登録
            trade_id = ""
            if result.ticket:
                await self._register_new_position(
                    result.ticket,
                    signal_with_lot,
                    lot,
                    entry_tick,
                    active_symbol,
                    save_position_state,
                )
                # DB書き込み（エントリー記録）
                trade_id = (
                    write_entry_to_db(
                        result.ticket,
                        signal_with_lot,
                        lot,
                        entry_tick,
                    )
                    or ""
                )
                if trade_id:
                    open_trades[result.ticket] = trade_id

            # _cached_positionsに即時追加（次tick待ち不要）
            entry_price = 0.0
            if entry_tick:
                price_key = (
                    "ask"
                    if signal_with_lot.signal_type == SignalType.BUY
                    else "bid"
                )
                entry_price = (
                    float(entry_tick.get(price_key, 0)) or entry_price
                )
            cached_positions.append(
                {
                    "position_id": str(result.ticket or 0),
                    "trade_id": trade_id,
                    "ticket": result.ticket or 0,
                    "symbol": active_symbol,
                    "signal_type": (signal_with_lot.signal_type.value),
                    "volume": lot,
                    "entry_price": entry_price,
                    "current_price": entry_price,
                    "stop_loss": signal_with_lot.stop_loss,
                    "take_profit": signal_with_lot.take_profit,
                    "opened_at": datetime.now(UTC).isoformat(),
                    "unrealized_pnl": 0.0,
                    "unrealized_pnl_pips": 0.0,
                    "remaining_minutes": None,
                    "max_hold_minutes": None,
                    "elapsed_minutes": 0,
                }
            )

            # TradeBotに通知（取引時刻を渡す）
            self._bot.on_trade_executed(signal.created_at)
            # EventBus経由でポジション更新ブロードキャスト
            event_bus.publish_nowait(
                "position.opened",
                {"symbol": active_symbol},
            )
        else:
            logger.error("エントリー失敗: %s", result.message)

    async def _register_new_position(
        self,
        ticket: int,
        signal: Signal,
        volume: float,
        entry_tick: dict | None,
        active_symbol: str,
        save_position_state,
    ) -> None:
        """新ポジションをPositionManagerに登録

        Args:
            ticket: MT5チケットID
            signal: トレードシグナル
            volume: ロット数
            entry_tick: エントリー時のtick情報（ask/bid）
            active_symbol: アクティブシンボル
            save_position_state: 状態保存コールバック
        """
        from autotrader.core.enums import TradingStrategyMode
        from autotrader.decision.unified.mode_selector import (
            TradingModeSelector,
        )

        # エントリー価格（実際のask/bid価格）
        is_buy = signal.signal_type == SignalType.BUY
        if entry_tick:
            entry_price = float(
                entry_tick.get("ask", 0)
                if is_buy
                else entry_tick.get("bid", 0)
            )
        else:
            entry_price = 0.0

        # signal.stop_loss/take_profitはpips値 → 価格レベルに変換
        pip_size = self.get_pip_size(signal.symbol)
        sl_price = 0.0
        tp_price = 0.0
        if entry_price > 0:
            if signal.stop_loss and signal.stop_loss > 0:
                sl_dist = signal.stop_loss * pip_size
                sl_price = (
                    entry_price - sl_dist if is_buy else entry_price + sl_dist
                )
            if signal.take_profit and signal.take_profit > 0:
                tp_dist = signal.take_profit * pip_size
                tp_price = (
                    entry_price + tp_dist if is_buy else entry_price - tp_dist
                )

        logger.info(
            "PM登録: ticket=%d entry=%.3f sl=%.3f tp=%.3f",
            ticket,
            entry_price,
            sl_price,
            tp_price,
        )

        # signal.modeから実際のモードを解決
        mode = TradingStrategyMode.UNIVERSAL
        if signal.mode:
            try:
                mode = TradingStrategyMode(signal.mode.upper())
            except ValueError:
                logger.warning(
                    "不明なモード: %s、UNIVERSALを使用",
                    signal.mode,
                )

        # ModeSelector経由でモード別プランパラメータを取得
        _base_plan = TradingModeSelector().get_plan_for_mode(mode)
        plan = dataclasses.replace(
            _base_plan,
            selection_reason="live",
            regime=signal.regime,
        )
        logger.info(
            "PM登録プラン: mode=%s primary_tf=%s",
            mode.value,
            plan.primary_tf,
        )

        self._pm.register_position(
            position_id=str(ticket),
            direction=signal.signal_type,
            entry_price=entry_price,
            entry_time=datetime.now(UTC),
            sl=sl_price,
            tp=tp_price,
            volume=volume,
            plan=plan,
        )
        # 新規登録時に管理状態をローカルDBに保存
        save_position_state(str(ticket))

    async def execute_action(
        self,
        position,
        action,
        current_price: float,
        active_symbol: str,
        write_close_to_db,
        delete_position_state,
        update_sl_in_db=None,
        update_volume_in_db=None,
    ) -> None:
        """管理アクション実行

        Args:
            position: ポジションエンティティ
            action: ManagementAction
            current_price: 現在価格（SL検証用）
            active_symbol: アクティブシンボル
            write_close_to_db: DB決済記録コールバック
            delete_position_state: 状態削除コールバック
            update_sl_in_db: SL変更時のDB同期コールバック
            update_volume_in_db: 部分決済時のDB同期コールバック
        """
        if action.action_type == ManagementActionType.HOLD:
            return

        if action.action_type == ManagementActionType.UPDATE_SL:
            if action.new_sl is not None:
                # SL値のバリデーション
                sl_valid = True
                if current_price > 0:
                    if position.signal_type == SignalType.BUY:
                        sl_valid = action.new_sl < current_price
                    else:
                        sl_valid = action.new_sl > current_price
                if not sl_valid:
                    logger.warning(
                        "SL値が無効（価格と逆側）: ticket=%d"
                        " SL=%.3f price=%.3f スキップ",
                        position.ticket,
                        action.new_sl,
                        current_price,
                    )
                else:
                    result = await self._executor.modify_position_async(
                        position,
                        stop_loss=action.new_sl,
                    )
                    if result.success:
                        logger.info(
                            "SL更新: ticket=%d → %.3f (%s)",
                            position.ticket,
                            action.new_sl,
                            action.reason,
                        )
                        # DB上のSLも同期更新
                        if update_sl_in_db:
                            update_sl_in_db(
                                position.ticket,
                                action.new_sl,
                            )

        elif action.action_type == ManagementActionType.PARTIAL_CLOSE:
            close_vol = round(position.volume * action.close_ratio, 2)
            if close_vol > 0:
                result = await self._executor.close_partial_async(
                    position,
                    close_vol,
                    action.reason,
                )
                if result.success:
                    logger.info(
                        "部分決済: ticket=%d %.2f lots (%s)",
                        position.ticket,
                        close_vol,
                        action.reason,
                    )
                    # DBのvolumeを更新
                    remaining = round(
                        position.volume - close_vol, 2
                    )
                    _partial_pnl = 0.0
                    try:
                        deal = (
                            await self._executor.get_deal_by_position_async(
                                position.ticket
                            )
                        )
                        if deal:
                            _partial_pnl = deal["profit"]
                    except (
                        KeyError,
                        ValueError,
                        TypeError,
                        MT5DataError,
                    ):
                        pass
                    if update_volume_in_db:
                        update_volume_in_db(
                            position.ticket,
                            remaining,
                            _partial_pnl,
                        )
                    # SL変更もあれば実行（バリデーション付き）
                    if action.new_sl is not None:
                        _sl_ok = True
                        if current_price > 0:
                            if position.signal_type == SignalType.BUY:
                                _sl_ok = action.new_sl < current_price
                            else:
                                _sl_ok = action.new_sl > current_price
                        if _sl_ok:
                            sl_result = (
                                await self._executor.modify_position_async(
                                    position,
                                    stop_loss=action.new_sl,
                                )
                            )
                            if sl_result.success and update_sl_in_db:
                                update_sl_in_db(
                                    position.ticket,
                                    action.new_sl,
                                )

        elif action.action_type == ManagementActionType.FULL_CLOSE:
            result = await self._executor.close_position_async(
                position,
                action.reason,
            )
            if result.success:
                logger.info(
                    "全決済: ticket=%d (%s)",
                    position.ticket,
                    action.reason,
                )
                # DB記録（決済）
                _actual_price = (
                    result.exit_price
                    if result.exit_price and result.exit_price > 0
                    else current_price
                )
                _profit_loss = 0.0
                try:
                    deal = await self._executor.get_deal_by_position_async(
                        position.ticket,
                    )
                    if deal:
                        _profit_loss = deal["profit"]
                        if deal["price"] > 0:
                            _actual_price = deal["price"]
                except (
                    KeyError,
                    ValueError,
                    TypeError,
                    MT5DataError,
                ):
                    pass
                if _actual_price > 0:
                    _exit_reason_str = (
                        action.exit_reason.value
                        if action.exit_reason
                        else "FORCE_CLOSE"
                    )
                    write_close_to_db(
                        position.ticket,
                        _actual_price,
                        _exit_reason_str,
                        _profit_loss,
                    )
                # ローカルDB管理状態を削除
                delete_position_state(str(position.ticket))
                # DB記録後にPMからポジションを削除
                self._pm.unregister_position(str(position.ticket))
