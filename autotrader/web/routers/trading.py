"""トレーディングルーター

MT5接続管理・自動取引ON/OFF・口座切替のAPIエンドポイント。
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, Request

from autotrader.config.accounts_loader import AccountsLoader
from autotrader.web.dependencies import get_live_engine
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


@router.get(
    "/mode",
    response_model=ApiResponse[TradingModeResponse],
)
async def get_trading_mode(
    request: Request,
    engine=Depends(get_live_engine),
) -> ApiResponse[TradingModeResponse]:
    """現在のトレーディングモード取得

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine

    Returns:
        ApiResponse[TradingModeResponse]: モード情報
    """
    if engine:
        return ApiResponse(
            data=TradingModeResponse(
                mode="live",
                label="Live Trading",
                connected=engine.connected,
                auto_trade=engine.enable_auto_trade,
                engine_running=engine.running,
                demo_mode=engine.demo_mode_enabled,
                symbol_auto_trade=engine.symbol_auto_trade_states,
                symbol_demo_mode=engine.symbol_demo_mode_states,
            )
        )
    return ApiResponse(
        data=TradingModeResponse(
            mode="offline",
            label="Offline",
        )
    )


@router.get(
    "/mt5/status",
    response_model=ApiResponse[MT5StatusResponse],
)
async def get_mt5_status(
    request: Request,
    engine=Depends(get_live_engine),
) -> ApiResponse[MT5StatusResponse]:
    """MT5接続状態取得

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine

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


@router.post(
    "/mt5/connect",
    response_model=ApiResponse[MT5StatusResponse],
)
async def connect_mt5(
    request: Request,
    engine=Depends(get_live_engine),
) -> ApiResponse[MT5StatusResponse]:
    """MT5接続開始

    エンジン未設定の場合はオンデマンドで作成する。

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine

    Returns:
        ApiResponse[MT5StatusResponse]: 接続結果
    """
    if not engine:
        try:
            from autotrader.web.main import (
                _create_live_engine,
            )
            engine = _create_live_engine()
            request.app.state.live_engine = engine
            logger.info("エンジンをオンデマンド作成")
        except Exception as e:
            logger.error("エンジン作成失敗: %s", e)
            return ApiResponse(
                success=False,
                error=f"エンジン作成失敗: {e}",
                data=MT5StatusResponse(
                    connected=False
                ),
            )

    try:
        await engine.start()
        logger.info("MT5接続成功（API経由）")
    except Exception as e:
        logger.error("MT5接続失敗: %s", e)
        return ApiResponse(
            success=False,
            error=str(e),
            data=MT5StatusResponse(connected=False),
        )

    return await get_mt5_status(request)


@router.post(
    "/mt5/disconnect",
    response_model=ApiResponse[MT5StatusResponse],
)
async def disconnect_mt5(
    request: Request,
    engine=Depends(get_live_engine),
) -> ApiResponse[MT5StatusResponse]:
    """MT5切断

    Args:
        request: FastAPIリクエスト
        engine: LiveTradingEngine

    Returns:
        ApiResponse[MT5StatusResponse]: 切断結果
    """
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
async def toggle_auto_trade(
    request: Request,
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

    return await get_trading_mode(request)


@router.post(
    "/symbol-auto-trade",
    response_model=ApiResponse[TradingModeResponse],
)
async def toggle_symbol_auto_trade(
    request: Request,
    symbol: str,
    enable: bool = False,
    engine=Depends(get_live_engine),
) -> ApiResponse[TradingModeResponse]:
    """シンボルごとの自動取引ON/OFF

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
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

    engine.set_symbol_auto_trade(symbol, enable)
    logger.info(
        "シンボル自動取引: %s %s（API経由）",
        symbol,
        "ON" if enable else "OFF",
    )

    # エンジン未起動時は自動起動
    if enable and not engine.running:
        try:
            await engine.start()
            logger.info(
                "エンジン自動起動（自動取引ON時）"
            )
        except Exception as e:
            logger.error("エンジン起動失敗: %s", e)
            return ApiResponse(
                success=False,
                error=f"エンジン起動失敗: {e}",
                data=TradingModeResponse(),
            )
    elif engine.running:
        engine.reset_data_update_timer()

    return await get_trading_mode(request)


@router.post(
    "/symbol-demo-mode",
    response_model=ApiResponse[TradingModeResponse],
)
async def toggle_symbol_demo_mode(
    request: Request,
    symbol: str,
    enable: bool = False,
    engine=Depends(get_live_engine),
) -> ApiResponse[TradingModeResponse]:
    """シンボルごとのデモモードON/OFF

    bot設定（閾値・フィルター）をデモ/本番に切り替える。
    自動取引の ON/OFF は別途 symbol-auto-trade で制御する。
    エンジン未起動時は自動起動する。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
        enable: デモモードを有効にするか
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

    from autotrader.web.services.settings_service import (
        get_settings_service,
    )
    svc = get_settings_service()

    # bot設定（閾値等）をデモ/本番設定に切り替え
    # （update_bot_config内で市場データ引き継ぎ）
    if enable:
        svc.enable_demo_mode()
    else:
        svc.disable_demo_mode()

    # シンボルのデモモードフラグを更新（UI表示用）
    engine.set_symbol_demo_mode(symbol, enable)

    # エンジン未起動時は自動起動
    if not engine.running:
        try:
            await engine.start()
            logger.info(
                "エンジン自動起動（デモモード切替時）"
            )
        except Exception as e:
            logger.error("エンジン起動失敗: %s", e)
            return ApiResponse(
                success=False,
                error=f"エンジン起動失敗: {e}",
                data=TradingModeResponse(),
            )
    else:
        # 実行中の場合はデータ更新タイマーをリセット
        engine.reset_data_update_timer()

    logger.info(
        "シンボルデモモード: %s %s（API経由）"
        " running=%s demo=%s",
        symbol,
        "ON" if enable else "OFF",
        engine.running,
        engine.demo_mode_enabled,
    )

    return await get_trading_mode(request)


@router.post(
    "/mt5/switch-account",
    response_model=ApiResponse[MT5StatusResponse],
)
async def switch_account(
    request: Request,
    body: SwitchAccountRequest,
    engine=Depends(get_live_engine),
) -> ApiResponse[MT5StatusResponse]:
    """MT5口座切替

    現在のエンジンを停止し、新しい口座情報でエンジンを再作成。

    Args:
        request: FastAPIリクエスト
        body: 口座切替リクエスト
        engine: LiveTradingEngine

    Returns:
        ApiResponse[MT5StatusResponse]: 新口座の接続結果
    """
    # 既存エンジン停止
    if engine and engine.running:
        try:
            await engine.stop()
            logger.info("口座切替: 既存エンジン停止")
        except Exception as e:
            logger.warning("既存エンジン停止エラー: %s", e)

    # 新エンジン作成
    try:
        from autotrader.adapters.mt5.config import MT5Config
        from autotrader.live.config import LiveTradingConfig
        from autotrader.live.engine import LiveTradingEngine

        mt5_config = MT5Config(
            login=body.login,
            password=body.password,
            server=body.server,
            terminal_path=os.environ.get(
                "MT5_TERMINAL_PATH", ""
            ),
        )
        live_config = LiveTradingConfig(
            symbol=os.environ.get(
                "AUTOTRADER_SYMBOL", "USDJPY"
            ),
            mt5_config=mt5_config,
            enable_auto_trade=False,
        )
        engine = LiveTradingEngine(live_config)
        request.app.state.live_engine = engine

        # 接続開始
        await engine.start()
        logger.info(
            "口座切替成功: login=%d server=%s",
            body.login,
            body.server,
        )
    except Exception as e:
        logger.error("口座切替失敗: %s", e)
        return ApiResponse(
            success=False,
            error=f"口座切替失敗: {e}",
            data=MT5StatusResponse(connected=False),
        )

    return await get_mt5_status(request)


@router.get(
    "/accounts",
    response_model=ApiResponse[AccountPresetsResponse],
)
async def get_account_presets() -> ApiResponse[AccountPresetsResponse]:
    """口座プリセット一覧取得

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
async def add_account_preset(
    body: AccountPresetRequest,
) -> ApiResponse[AccountPresetsResponse]:
    """口座プリセット登録/更新

    Args:
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
async def delete_account_preset(
    login: int,
) -> ApiResponse[AccountPresetsResponse]:
    """口座プリセット削除

    Args:
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
