"""トレーディングルーター

MT5接続管理・自動取引ON/OFF・口座切替のAPIエンドポイント。
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from autotrader.config.accounts_loader import AccountsLoader
from autotrader.web.auth.dependencies import (
    get_current_user,
    require_admin,
)
from autotrader.web.dependencies import (
    get_engine_manager,
    get_live_engine,
)
from autotrader.web.middleware import limiter
from autotrader.web.schemas import (
    AccountPresetRequest,
    ApiResponse,
    MT5StatusResponse,
    SwitchAccountRequest,
    TradingModeResponse,
)
from autotrader.web.schemas.responses import (
    AccountInfoResponse,
    AccountPresetResponse,
    AccountPresetsResponse,
)
from autotrader.web.utils import sanitize_error_message

_accounts_loader = AccountsLoader()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trading")


def _account_to_response(acct) -> AccountInfoResponse:
    """AccountInfoエンティティをレスポンスに変換

    Args:
        acct: AccountInfoエンティティ

    Returns:
        AccountInfoResponse: レスポンス
    """
    return AccountInfoResponse(
        balance=acct.balance,
        equity=acct.equity,
        margin=acct.margin,
        free_margin=acct.free_margin,
        margin_level=acct.margin_level,
        profit=acct.profit,
        login=acct.login,
        server=acct.server,
        name=acct.name,
        currency=acct.currency,
        leverage=acct.leverage,
    )


def _build_trading_mode_response(
    engine=None,
    mgr=None,
) -> ApiResponse[TradingModeResponse]:
    """エンジン/マネージャーからTradingModeレスポンスを構築

    Args:
        engine: LiveTradingEngine | None（後方互換）
        mgr: EngineManager | None

    Returns:
        ApiResponse[TradingModeResponse]: モード情報
    """
    # EngineManager経由で集約
    if mgr and mgr.engines:
        # 任意エンジンから接続状態・デモモードを取得
        first_engine = next(iter(mgr.engines.values()))
        any_running = any(
            e.running for e in mgr.engines.values()
        )
        any_auto = any(
            e.enable_auto_trade
            for e in mgr.engines.values()
        )
        return ApiResponse(
            data=TradingModeResponse(
                mode="live",
                label="Live Trading",
                connected=mgr.connected,
                auto_trade=any_auto,
                engine_running=any_running,
                demo_mode=first_engine.demo_mode_enabled,
                symbol_auto_trade=(
                    mgr.symbol_auto_trade_states
                ),
                symbol_demo_mode=(
                    mgr.symbol_demo_mode_states
                ),
            )
        )

    # 後方互換: 単一エンジン
    if engine:
        return ApiResponse(
            data=TradingModeResponse(
                mode="live",
                label="Live Trading",
                connected=engine.connected,
                auto_trade=engine.enable_auto_trade,
                engine_running=engine.running,
                demo_mode=engine.demo_mode_enabled,
                symbol_auto_trade=(
                    engine.symbol_auto_trade_states
                ),
                symbol_demo_mode=(
                    engine.symbol_demo_mode_states
                ),
            )
        )
    return ApiResponse(
        data=TradingModeResponse(
            mode="offline",
            label="Offline",
        )
    )


@router.get(
    "/mode",
    response_model=ApiResponse[TradingModeResponse],
)
@limiter.limit("60/minute")
async def get_trading_mode(
    request: Request,
    user: Annotated[dict[str, any], Depends(get_current_user)],
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[TradingModeResponse]:
    """現在のトレーディングモード取得

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[TradingModeResponse]: モード情報
    """
    return _build_trading_mode_response(engine, mgr)


def _build_mt5_status_response(
    engine,
) -> ApiResponse[MT5StatusResponse]:
    """エンジンからMT5ステータスレスポンスを構築

    Args:
        engine: LiveTradingEngine | None

    Returns:
        ApiResponse[MT5StatusResponse]: MT5状態
    """
    if not engine:
        return ApiResponse(
            data=MT5StatusResponse(connected=False)
        )

    account = None
    if engine.account_info:
        account = _account_to_response(engine.account_info)

    return ApiResponse(
        data=MT5StatusResponse(
            connected=engine.connected,
            account=account,
        )
    )


@router.get(
    "/mt5/status",
    response_model=ApiResponse[MT5StatusResponse],
)
@limiter.limit("60/minute")
async def get_mt5_status(
    request: Request,
    user: Annotated[dict[str, any], Depends(get_current_user)],
    engine=Depends(get_live_engine),
) -> ApiResponse[MT5StatusResponse]:
    """MT5接続状態取得

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine

    Returns:
        ApiResponse[MT5StatusResponse]: MT5状態
    """
    return _build_mt5_status_response(engine)


@router.post(
    "/mt5/connect",
    response_model=ApiResponse[MT5StatusResponse],
)
@limiter.limit("10/minute")
async def connect_mt5(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[MT5StatusResponse]:
    """MT5接続開始

    EngineManager経由で接続。未設定時はオンデマンド作成。

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[MT5StatusResponse]: 接続結果
    """
    # EngineManager経由
    if mgr:
        try:
            await mgr.connect()
            await mgr.start_all()
            logger.info("MT5接続成功（API/Manager経由）")
            engine = next(
                iter(mgr.engines.values()), None
            )
            return _build_mt5_status_response(engine)
        except Exception as e:
            return ApiResponse(
                success=False,
                error=sanitize_error_message(e, "MT5接続"),
                data=MT5StatusResponse(
                    connected=False
                ),
            )

    # 後方互換
    if not engine:
        try:
            from autotrader.web.main import (
                _create_live_engine,
            )
            engine = _create_live_engine()
            request.app.state.live_engine = engine
            logger.info("エンジンをオンデマンド作成")
        except Exception as e:
            return ApiResponse(
                success=False,
                error=sanitize_error_message(
                    e, "エンジン作成"
                ),
                data=MT5StatusResponse(
                    connected=False
                ),
            )

    try:
        await engine.start()
        logger.info("MT5接続成功（API経由）")
    except Exception as e:
        return ApiResponse(
            success=False,
            error=sanitize_error_message(e, "MT5接続"),
            data=MT5StatusResponse(connected=False),
        )

    return _build_mt5_status_response(engine)


@router.post(
    "/mt5/disconnect",
    response_model=ApiResponse[MT5StatusResponse],
)
@limiter.limit("10/minute")
async def disconnect_mt5(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[MT5StatusResponse]:
    """MT5切断

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[MT5StatusResponse]: 切断結果
    """
    if mgr:
        try:
            await mgr.disconnect()
            logger.info(
                "MT5切断成功（API/Manager経由）"
            )
        except Exception as e:
            logger.error("MT5切断エラー: %s", e)
        return ApiResponse(
            data=MT5StatusResponse(connected=False)
        )

    if not engine:
        return ApiResponse(
            data=MT5StatusResponse(connected=False)
        )

    try:
        await engine.stop()
        logger.info("MT5切断成功（API経由）")
    except Exception as e:
        logger.error("MT5切断エラー: %s", e)

    return ApiResponse(
        data=MT5StatusResponse(connected=False)
    )


@router.post(
    "/auto-trade",
    response_model=ApiResponse[TradingModeResponse],
)
@limiter.limit("10/minute")
async def toggle_auto_trade(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    enable: bool = False,
    engine=Depends(get_live_engine),
) -> ApiResponse[TradingModeResponse]:
    """自動取引ON/OFF

    Args:
        request: FastAPIリクエスト
        enable: 有効化するか
        engine: LiveTradingEngine

    Returns:
        ApiResponse[TradingModeResponse]: 更新後のモード
    """
    if not engine:
        return ApiResponse(
            success=False,
            error="ライブエンジンが設定されていません",
            data=TradingModeResponse(),
        )

    engine.enable_auto_trade = enable
    logger.info(
        "自動取引: %s（API経由）",
        "ON" if enable else "OFF",
    )

    # ONトグル時にポジション同期を実行
    if enable:
        await engine.sync_positions_on_toggle()

    return _build_trading_mode_response(engine)


@router.post(
    "/symbol-auto-trade",
    response_model=ApiResponse[TradingModeResponse],
)
@limiter.limit("10/minute")
async def toggle_symbol_auto_trade(
    request: Request,
    user: Annotated[dict[str, any], Depends(get_current_user)],
    symbol: str,
    enable: bool = False,
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[TradingModeResponse]:
    """シンボルごとの自動取引ON/OFF

    EngineManager経由でシンボル別エンジンを取得。
    エンジンがなければ自動追加する。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
        enable: 有効化するか
        engine: LiveTradingEngine（後方互換）
        mgr: EngineManager

    Returns:
        ApiResponse[TradingModeResponse]: 更新後のモード
    """
    if mgr:
        target = mgr.get_engine(symbol)

        # エンジンがなければ自動追加
        if target is None and enable:
            from autotrader.web.main import (
                build_engine_config,
            )
            config = build_engine_config(symbol)
            target = await mgr.add_symbol(config)

        if target is None:
            return _build_trading_mode_response(
                mgr=mgr
            )

        target.enable_auto_trade = enable
        logger.info(
            "シンボル自動取引: %s %s（API経由）",
            symbol,
            "ON" if enable else "OFF",
        )

        if enable:
            await target.sync_positions_on_toggle()
            if not target.running:
                try:
                    await target.start()
                except Exception as e:
                    return ApiResponse(
                        success=False,
                        error=sanitize_error_message(
                            e, "エンジン起動"
                        ),
                        data=TradingModeResponse(),
                    )
        elif target.running:
            target.reset_data_update_timer()

        return _build_trading_mode_response(mgr=mgr)

    # 後方互換: EngineManagerなし
    if not engine:
        return ApiResponse(
            success=False,
            error="ライブエンジンが設定されていません",
            data=TradingModeResponse(),
        )

    symbol_changed = (
        hasattr(engine, "active_symbol")
        and symbol != engine.active_symbol
    )
    await engine.set_symbol_auto_trade(symbol, enable)
    logger.info(
        "シンボル自動取引: %s %s（API経由）",
        symbol,
        "ON" if enable else "OFF",
    )

    if enable and not symbol_changed:
        await engine.sync_positions_on_toggle()

    if enable and not engine.running:
        try:
            await engine.start()
        except Exception as e:
            return ApiResponse(
                success=False,
                error=sanitize_error_message(
                    e, "エンジン起動"
                ),
                data=TradingModeResponse(),
            )
    elif engine.running:
        engine.reset_data_update_timer()

    return _build_trading_mode_response(engine)


@router.post(
    "/switch-symbol",
    response_model=ApiResponse[TradingModeResponse],
)
@limiter.limit("10/minute")
async def switch_symbol(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    symbol: str,
    engine=Depends(get_live_engine),
) -> ApiResponse[TradingModeResponse]:
    """アクティブシンボルを切替

    エンジンの処理対象シンボルを変更し、
    コンポーネントを再初期化する。

    Args:
        request: FastAPIリクエスト
        symbol: 切替先の通貨ペアシンボル
        engine: LiveTradingEngine

    Returns:
        ApiResponse[TradingModeResponse]: 更新後のモード
    """
    if not engine:
        return ApiResponse(
            success=False,
            error="ライブエンジンが設定されていません",
            data=TradingModeResponse(),
        )

    try:
        await engine.change_symbol(symbol)
    except ValueError as e:
        return ApiResponse(
            success=False,
            error=sanitize_error_message(
                e, "シンボル切替"
            ),
            data=TradingModeResponse(),
        )
    logger.info(
        "シンボル切替: %s（API経由）", symbol
    )

    return _build_trading_mode_response(engine)


@router.post(
    "/symbol-demo-mode",
    response_model=ApiResponse[TradingModeResponse],
)
@limiter.limit("10/minute")
async def toggle_symbol_demo_mode(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    symbol: str,
    enable: bool = False,
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[TradingModeResponse]:
    """シンボルごとのデモモードON/OFF

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
        enable: デモモードを有効にするか
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[TradingModeResponse]: 更新後のモード
    """
    from autotrader.web.services.settings_service import (
        get_settings_service,
    )
    svc = get_settings_service()

    # EngineManager経由
    if mgr:
        target = mgr.get_engine(symbol)
        if target is None:
            return ApiResponse(
                success=False,
                error=f"シンボル {symbol} のエンジンがありません",
                data=TradingModeResponse(),
            )

        if enable:
            svc.enable_demo_mode()
        else:
            svc.disable_demo_mode()

        target.set_symbol_demo_mode(symbol, enable)

        if not target.running:
            try:
                await target.start()
            except Exception as e:
                return ApiResponse(
                    success=False,
                    error=sanitize_error_message(
                        e, "エンジン起動"
                    ),
                    data=TradingModeResponse(),
                )
        else:
            target.reset_data_update_timer()

        logger.info(
            "シンボルデモモード: %s %s（API経由）",
            symbol,
            "ON" if enable else "OFF",
        )
        return _build_trading_mode_response(mgr=mgr)

    # 後方互換
    if not engine:
        return ApiResponse(
            success=False,
            error="ライブエンジンが設定されていません",
            data=TradingModeResponse(),
        )

    if enable:
        svc.enable_demo_mode()
    else:
        svc.disable_demo_mode()

    engine.set_symbol_demo_mode(symbol, enable)

    if not engine.running:
        try:
            await engine.start()
        except Exception as e:
            return ApiResponse(
                success=False,
                error=sanitize_error_message(
                    e, "エンジン起動"
                ),
                data=TradingModeResponse(),
            )
    else:
        engine.reset_data_update_timer()

    logger.info(
        "シンボルデモモード: %s %s（API経由）",
        symbol,
        "ON" if enable else "OFF",
    )
    return _build_trading_mode_response(engine)


@router.post(
    "/mt5/switch-account",
    response_model=ApiResponse[MT5StatusResponse],
)
@limiter.limit("3/minute")
async def switch_account(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    body: SwitchAccountRequest,
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[MT5StatusResponse]:
    """MT5口座切替

    旧マネージャー破棄→新マネージャー作成。

    Args:
        request: FastAPIリクエスト
        body: 口座切替リクエスト
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[MT5StatusResponse]: 新口座の接続結果
    """
    # 既存マネージャー/エンジン停止
    if mgr:
        try:
            await mgr.disconnect()
        except Exception as e:
            logger.warning(
                "既存マネージャー停止エラー: %s", e
            )
    elif engine and engine.running:
        try:
            await engine.stop()
        except Exception as e:
            logger.warning(
                "既存エンジン停止エラー: %s", e
            )

    # 新マネージャー作成
    try:
        from autotrader.adapters.mt5.config import MT5Config
        from autotrader.live.engine_manager import (
            EngineManager,
        )
        from autotrader.web.main import (
            build_engine_config,
        )
        from autotrader.web.services.settings_service import (
            get_settings_service,
        )

        mt5_config = MT5Config(
            login=body.login,
            password=body.password,
            server=body.server,
            terminal_path=os.environ.get(
                "MT5_TERMINAL_PATH", ""
            ),
        )
        new_mgr = EngineManager(mt5_config)
        request.app.state.engine_manager = new_mgr

        # 接続+デフォルトシンボル追加
        await new_mgr.connect()
        default_symbol = os.environ.get(
            "AUTOTRADER_SYMBOL", "USDJPY"
        )
        config = build_engine_config(default_symbol)
        new_engine = await new_mgr.add_symbol(config)
        request.app.state.live_engine = new_engine

        svc = get_settings_service()
        svc.set_engine_manager(new_mgr)
        svc.set_engine(new_engine)

        logger.info(
            "口座切替成功: login=%d server=%s",
            body.login,
            body.server,
        )
    except Exception as e:
        return ApiResponse(
            success=False,
            error=sanitize_error_message(e, "口座切替"),
            data=MT5StatusResponse(connected=False),
        )

    return _build_mt5_status_response(new_engine)


# ==== シンボル管理API ====


@router.get("/symbols")
@limiter.limit("60/minute")
async def get_symbols(
    request: Request,
    user: Annotated[dict[str, any], Depends(get_current_user)],
    mgr=Depends(get_engine_manager),
) -> ApiResponse[list[str]]:
    """アクティブシンボル一覧取得

    Args:
        request: FastAPIリクエスト
        mgr: EngineManager

    Returns:
        ApiResponse[list[str]]: シンボル一覧
    """
    if not mgr:
        return ApiResponse(data=[])
    return ApiResponse(data=mgr.symbols)


@router.post("/symbols/add")
@limiter.limit("10/minute")
async def add_symbol(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    symbol: str,
    mgr=Depends(get_engine_manager),
) -> ApiResponse[list[str]]:
    """シンボルのエンジンを追加

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
        mgr: EngineManager

    Returns:
        ApiResponse[list[str]]: 更新後のシンボル一覧
    """
    if not mgr:
        return ApiResponse(
            success=False,
            error="EngineManagerが設定されていません",
            data=[],
        )

    from autotrader.web.main import build_engine_config

    config = build_engine_config(symbol)
    await mgr.add_symbol(config)
    logger.info("シンボル追加: %s", symbol)
    return ApiResponse(data=mgr.symbols)


@router.post("/symbols/remove")
@limiter.limit("10/minute")
async def remove_symbol(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    symbol: str,
    mgr=Depends(get_engine_manager),
) -> ApiResponse[list[str]]:
    """シンボルのエンジンを除去

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
        mgr: EngineManager

    Returns:
        ApiResponse[list[str]]: 更新後のシンボル一覧
    """
    if not mgr:
        return ApiResponse(
            success=False,
            error="EngineManagerが設定されていません",
            data=[],
        )

    await mgr.remove_symbol(symbol)
    logger.info("シンボル除去: %s", symbol)
    return ApiResponse(data=mgr.symbols)


@router.get(
    "/accounts",
    response_model=ApiResponse[AccountPresetsResponse],
)
@limiter.limit("60/minute")
async def get_account_presets(
    request: Request,
    user: Annotated[dict[str, any], Depends(get_current_user)],
) -> ApiResponse[AccountPresetsResponse]:
    """口座プリセット一覧取得

    Args:
        request: FastAPIリクエスト

    Returns:
        ApiResponse[AccountPresetsResponse]: プリセット一覧
    """
    accounts = _accounts_loader.load()
    return ApiResponse(
        data=AccountPresetsResponse(
            accounts=[
                AccountPresetResponse(
                    login=a["login"],
                    server=a["server"],
                    name=a.get("name", ""),
                )
                for a in accounts
            ]
        )
    )


@router.post(
    "/accounts",
    response_model=ApiResponse[AccountPresetsResponse],
)
@limiter.limit("10/minute")
async def add_account_preset(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    body: AccountPresetRequest,
) -> ApiResponse[AccountPresetsResponse]:
    """口座プリセット登録/更新

    Args:
        request: FastAPIリクエスト
        body: プリセット登録リクエスト

    Returns:
        ApiResponse[AccountPresetsResponse]: 更新後のプリセット一覧
    """
    accounts = _accounts_loader.add_or_update(
        body.login, body.server, body.name
    )
    logger.info(
        "口座プリセット登録: login=%d server=%s name=%s",
        body.login,
        body.server,
        body.name,
    )
    return ApiResponse(
        data=AccountPresetsResponse(
            accounts=[
                AccountPresetResponse(
                    login=a["login"],
                    server=a["server"],
                    name=a.get("name", ""),
                )
                for a in accounts
            ]
        )
    )


@router.delete(
    "/accounts/{login}",
    response_model=ApiResponse[AccountPresetsResponse],
)
@limiter.limit("10/minute")
async def delete_account_preset(
    request: Request,
    user: Annotated[dict[str, any], Depends(require_admin)],
    login: int,
) -> ApiResponse[AccountPresetsResponse]:
    """口座プリセット削除

    Args:
        request: FastAPIリクエスト
        login: 削除するログインID

    Returns:
        ApiResponse[AccountPresetsResponse]: 更新後のプリセット一覧
    """
    accounts = _accounts_loader.delete(login)
    logger.info("口座プリセット削除: login=%d", login)
    return ApiResponse(
        data=AccountPresetsResponse(
            accounts=[
                AccountPresetResponse(
                    login=a["login"],
                    server=a["server"],
                    name=a.get("name", ""),
                )
                for a in accounts
            ]
        )
    )
