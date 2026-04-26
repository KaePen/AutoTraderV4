"""バックテスト統一CLIエントリーポイント

Usage:
    # 単体通貨バックテスト
    uv run python -m autotrader.backtest run --symbol USDJPY --start 2024 --end 2024

    # マルチ通貨
    uv run python -m autotrader.backtest run --symbols USDJPY,EURJPY,GBPJPY --start 2023 --end 2025

    # ティックSL/TP有効
    uv run python -m autotrader.backtest run --symbol USDJPY --tick-exit --start 2024 --end 2024

    # 途中再開
    uv run python -m autotrader.backtest run --symbol USDJPY --resume --start 2024 --end 2024

    # OHLCV データ前処理
    uv run python -m autotrader.backtest prepare-ohlcv --symbol USDJPY --start 2010 --end 2026

    # ティック データ前処理
    uv run python -m autotrader.backtest prepare-ticks --symbol USDJPY

    # キャッシュ状態確認
    uv run python -m autotrader.backtest status --symbol USDJPY
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def cmd_run(args: argparse.Namespace) -> None:
    """バックテスト実行"""
    from autotrader.backtest.day_runner import (
        MonthRunner,
        MonthRunnerConfig,
    )

    symbols = _parse_symbols(args)
    data_dir = Path(args.data_dir) if args.data_dir else None

    for symbol in symbols:
        config = MonthRunnerConfig(
            symbol=symbol,
            start_year=args.start,
            start_month=1,
            end_year=args.end,
            end_month=12,
            base_timeframe=args.timeframe,
            data_dir=data_dir,
            job_id=f"{symbol}_{args.start}_{args.end}",
            use_tick_exit=args.tick_exit,
        )

        bot_config = None
        sim_config = None
        if args.config:
            bot_config, sim_config = _load_config_file(args.config, symbol)

        runner = MonthRunner(
            config=config,
            bot_config=bot_config,
            sim_config=sim_config,
        )

        result = runner.run(resume=args.resume)

        print(f"\n{'=' * 50}")
        print(f"■ {result.symbol}")
        print(f"{'=' * 50}")
        print(f"期間:       {result.start_month} → {result.end_month}")
        print(f"処理月数:   {result.processed_months}/{result.total_months}")
        print(f"トレード:   {result.trades}")
        print(f"勝率:       {result.win_rate:.1f}%")
        print(f"PF:         {result.profit_factor:.2f}")
        print(f"純利益:     ¥{result.net_profit:,.0f}")
        print(f"最大DD:     {result.max_drawdown:.1f}%")
        print(f"シャープ:   {result.sharpe:.2f}")
        if result.events_skipped > 0:
            print(f"指標スキップ: {result.events_skipped}回")
        if result.resumed_from:
            print(f"再開地点:   {result.resumed_from}")


def cmd_prepare_ohlcv(args: argparse.Namespace) -> None:
    """OHLCVデータ前処理"""
    from autotrader.backtest.data_pipeline import prepare_ohlcv

    symbols = _parse_symbols(args)
    data_dir = Path(args.data_dir) if args.data_dir else None

    for symbol in symbols:
        result = prepare_ohlcv(
            symbol=symbol,
            start_year=args.start,
            end_year=args.end,
            timeframes=args.timeframes,
            data_dir=data_dir,
            force=args.force,
        )
        print(json.dumps(result, indent=2, default=str))


def cmd_prepare_ticks(args: argparse.Namespace) -> None:
    """ティックデータ前処理"""
    from autotrader.backtest.data_pipeline import prepare_ticks

    symbols = _parse_symbols(args)
    data_dir = Path(args.data_dir) if args.data_dir else None

    for symbol in symbols:
        result = prepare_ticks(
            symbol=symbol,
            tick_csv=args.tick_csv,
            data_dir=data_dir,
            force=args.force,
        )
        print(json.dumps(result, indent=2, default=str))


def cmd_status(args: argparse.Namespace) -> None:
    """キャッシュ状態確認"""
    from autotrader.backtest.data_pipeline import main as pipeline_main

    sys.argv = ["data_pipeline", "status", "--symbol", args.symbol]
    if args.data_dir:
        sys.argv.extend(["--data-dir", args.data_dir])
    pipeline_main()


def _parse_symbols(args: argparse.Namespace) -> list[str]:
    if hasattr(args, "symbols") and args.symbols:
        return [s.strip() for s in args.symbols.split(",")]
    if hasattr(args, "symbol") and args.symbol:
        return [args.symbol]
    return ["USDJPY"]


def _load_config_file(config_path: str, symbol: str) -> tuple:
    path = Path(config_path)
    if not path.exists():
        logger.warning("設定ファイルなし: %s", path)
        return None, None

    data = json.loads(path.read_text(encoding="utf-8"))
    bot_config = None
    sim_config = None

    if "bot" in data:
        from autotrader.decision.unified import UnifiedBotConfig
        bot_config = UnifiedBotConfig(**data["bot"])
    if "sim" in data:
        from autotrader.backtest.simulator import SimulatorConfig
        sim_config = SimulatorConfig(**data["sim"])

    return bot_config, sim_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="python -m autotrader.backtest",
        description="AutoTraderV4 バックテスト統一CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # --- run ---
    p_run = sub.add_parser("run", help="バックテスト実行")
    p_run.add_argument("--symbol", help="通貨ペア（単体）")
    p_run.add_argument("--symbols", help="通貨ペア（複数、カンマ区切り）")
    p_run.add_argument("--start", type=int, required=True, help="開始年")
    p_run.add_argument("--end", type=int, required=True, help="終了年")
    p_run.add_argument("--timeframe", default="M15", help="基準時間足（デフォルト: M15）")
    p_run.add_argument("--tick-exit", action="store_true", help="ティックベースSL/TP判定")
    p_run.add_argument("--resume", action="store_true", help="チェックポイントから再開")
    p_run.add_argument("--config", help="設定JSONファイルパス")
    p_run.add_argument("--data-dir", help="データディレクトリ")

    # --- prepare-ohlcv ---
    p_ohlcv = sub.add_parser("prepare-ohlcv", help="OHLCV → インジケータ → 月別キャッシュ")
    p_ohlcv.add_argument("--symbol", help="通貨ペア（単体）")
    p_ohlcv.add_argument("--symbols", help="通貨ペア（複数、カンマ区切り）")
    p_ohlcv.add_argument("--start", type=int, default=2010, help="開始年")
    p_ohlcv.add_argument("--end", type=int, default=2026, help="終了年")
    p_ohlcv.add_argument("--timeframes", nargs="*", default=None, help="時間足")
    p_ohlcv.add_argument("--force", action="store_true", help="強制再生成")
    p_ohlcv.add_argument("--data-dir", help="データディレクトリ")

    # --- prepare-ticks ---
    p_ticks = sub.add_parser("prepare-ticks", help="ティック単一CSV → 月別キャッシュ")
    p_ticks.add_argument("--symbol", help="通貨ペア（単体）")
    p_ticks.add_argument("--symbols", help="通貨ペア（複数、カンマ区切り）")
    p_ticks.add_argument("--tick-csv", default=None, help="ティックCSVパス（省略時: 自動検出）")
    p_ticks.add_argument("--force", action="store_true", help="強制再生成")
    p_ticks.add_argument("--data-dir", help="データディレクトリ")

    # --- status ---
    p_status = sub.add_parser("status", help="キャッシュ状態確認")
    p_status.add_argument("--symbol", required=True, help="通貨ペア")
    p_status.add_argument("--data-dir", help="データディレクトリ")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "prepare-ohlcv": cmd_prepare_ohlcv,
        "prepare-ticks": cmd_prepare_ticks,
        "status": cmd_status,
    }
    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
