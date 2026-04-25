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

    # データ前処理
    uv run python -m autotrader.backtest prepare --symbol USDJPY --start 2020 --end 2025

    # キャッシュ状態確認
    uv run python -m autotrader.backtest status --symbol USDJPY
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def cmd_run(args: argparse.Namespace) -> None:
    """バックテスト実行"""
    from autotrader.backtest.day_runner import (
        DayRunner,
        DayRunnerConfig,
    )

    symbols = _parse_symbols(args)
    data_dir = Path(args.data_dir) if args.data_dir else None

    for symbol in symbols:
        config = DayRunnerConfig(
            symbol=symbol,
            start_date=date(args.start, 1, 1),
            end_date=date(args.end, 12, 31),
            base_timeframe=args.timeframe,
            data_dir=data_dir,
            job_id=f"{symbol}_{args.start}_{args.end}",
            use_tick_exit=args.tick_exit,
        )

        # bot_config / sim_config の構築
        bot_config = None
        sim_config = None
        if args.config:
            bot_config, sim_config = _load_config_file(
                args.config, symbol,
            )

        runner = DayRunner(
            config=config,
            bot_config=bot_config,
            sim_config=sim_config,
        )

        result = runner.run(resume=args.resume)

        print(f"\n{'=' * 50}")
        print(f"■ {result.symbol}")
        print(f"{'=' * 50}")
        print(f"期間:       {result.start_date} → {result.end_date}")
        print(f"処理日数:   {result.processed_days}/{result.total_days}")
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


def cmd_prepare(args: argparse.Namespace) -> None:
    """データ前処理"""
    from autotrader.backtest.data_pipeline import prepare

    symbols = _parse_symbols(args)
    data_dir = Path(args.data_dir) if args.data_dir else None

    for symbol in symbols:
        result = prepare(
            symbol=symbol,
            start_year=args.start,
            end_year=args.end,
            timeframes=args.timeframes,
            data_dir=data_dir,
            force=args.force,
            include_ticks=not args.no_ticks,
        )
        print(json.dumps(result, indent=2, default=str))


def cmd_status(args: argparse.Namespace) -> None:
    """キャッシュ状態確認"""
    from autotrader.backtest.data_pipeline import main as pipeline_main

    # data_pipeline の status コマンドに委譲
    sys.argv = [
        "data_pipeline", "status",
        "--symbol", args.symbol,
    ]
    if args.data_dir:
        sys.argv.extend(["--data-dir", args.data_dir])
    pipeline_main()


def _parse_symbols(args: argparse.Namespace) -> list[str]:
    """--symbol / --symbols からシンボルリストを取得"""
    if hasattr(args, "symbols") and args.symbols:
        return [
            s.strip()
            for s in args.symbols.split(",")
        ]
    if hasattr(args, "symbol") and args.symbol:
        return [args.symbol]
    return ["USDJPY"]


def _load_config_file(
    config_path: str,
    symbol: str,
) -> tuple:
    """設定ファイルからbot_config/sim_configを読み込み"""
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
    p_run.add_argument(
        "--symbols", help="通貨ペア（複数、カンマ区切り）",
    )
    p_run.add_argument(
        "--start", type=int, required=True, help="開始年",
    )
    p_run.add_argument(
        "--end", type=int, required=True, help="終了年",
    )
    p_run.add_argument(
        "--timeframe", default="M15", help="基準時間足（デフォルト: M15）",
    )
    p_run.add_argument(
        "--tick-exit", action="store_true",
        help="ティックベースSL/TP判定を有効化",
    )
    p_run.add_argument(
        "--resume", action="store_true",
        help="チェックポイントから再開",
    )
    p_run.add_argument("--config", help="設定JSONファイルパス")
    p_run.add_argument("--data-dir", help="データディレクトリ")

    # --- prepare ---
    p_prep = sub.add_parser("prepare", help="データ前処理")
    p_prep.add_argument("--symbol", help="通貨ペア（単体）")
    p_prep.add_argument("--symbols", help="通貨ペア（複数、カンマ区切り）")
    p_prep.add_argument(
        "--start", type=int, default=2010, help="開始年",
    )
    p_prep.add_argument(
        "--end", type=int, default=2025, help="終了年",
    )
    p_prep.add_argument(
        "--timeframes", nargs="*", default=None, help="時間足",
    )
    p_prep.add_argument(
        "--force", action="store_true", help="強制再生成",
    )
    p_prep.add_argument(
        "--no-ticks", action="store_true", help="ティックスキップ",
    )
    p_prep.add_argument("--data-dir", help="データディレクトリ")

    # --- status ---
    p_status = sub.add_parser("status", help="キャッシュ状態")
    p_status.add_argument(
        "--symbol", required=True, help="通貨ペア",
    )
    p_status.add_argument("--data-dir", help="データディレクトリ")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "prepare":
        cmd_prepare(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
