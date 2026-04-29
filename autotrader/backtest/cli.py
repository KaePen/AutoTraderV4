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


def cmd_run_single(args: argparse.Namespace) -> None:
    """単独ペアBT（run_unified ベース、TickSim 有効）"""
    from datetime import datetime
    from autotrader.backtest.single_pair_runner import (
        SinglePairConfig, run_single_pair,
    )

    pstart = (
        datetime.fromisoformat(args.period_start)
        if args.period_start else None
    )
    pend = (
        datetime.fromisoformat(args.period_end)
        if args.period_end else None
    )

    cfg = SinglePairConfig(
        symbol=args.symbol,
        start_year=args.start,
        end_year=args.end,
        use_tick_sim=not args.no_tick_sim,
        sequential=not args.no_sequential,
        period_start=pstart,
        period_end=pend,
    )
    summary = run_single_pair(cfg)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


def cmd_run_portfolio(args: argparse.Namespace) -> None:
    """マルチペア統合BT（時系列インターリーブ + 共有ポートフォリオ）"""
    from datetime import datetime
    from autotrader.backtest.multi_pair_runner import (
        MultiPairConfig, run_multi_pair_period,
    )

    symbols = _parse_symbols(args)
    pstart = (
        datetime.fromisoformat(args.period_start)
        if args.period_start else None
    )
    pend = (
        datetime.fromisoformat(args.period_end)
        if args.period_end else None
    )
    cfg = MultiPairConfig(
        symbols=symbols,
        start_year=args.start,
        end_year=args.end,
        initial_equity=args.initial_equity,
        global_max_positions=args.global_max_positions,
        per_pair_max_positions=args.per_pair_max_positions,
        global_max_exposure_lot=args.global_max_exposure_lot,
        max_same_direction_jpy=args.max_same_direction_jpy,
        sequential_years=not args.no_sequential,
        data_load_workers=args.data_load_workers,
        period_start=pstart,
        period_end=pend,
        use_tick_exit=args.use_tick_exit,
        tick_check_tf_minutes=args.tick_check_tf_minutes,
        output_trades_csv=args.trades_csv,
    )

    bot_overrides: dict = {}
    if args.htf_counter_block:
        bot_overrides["htf_counter_block_enabled"] = True
    if args.htf_counter_threshold is not None:
        bot_overrides["htf_counter_block_threshold"] = args.htf_counter_threshold
    if args.bot_override_json:
        try:
            extra = json.loads(args.bot_override_json)
            if not isinstance(extra, dict):
                raise ValueError("--bot-override-json must be a JSON object")
            bot_overrides.update(extra)
        except Exception as e:
            print(f"ERROR --bot-override-json: {e}")
            sys.exit(1)

    result = run_multi_pair_period(cfg, bot_overrides=bot_overrides or None)
    summary = result.summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"\n結果保存: {out_path}")


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

    # --- run-single ---（TickSim付き単独ペアBT）
    p_single = sub.add_parser(
        "run-single",
        help="単独ペアBT（run_unified + TickSim）",
    )
    p_single.add_argument("--symbol", required=True, help="通貨ペア")
    p_single.add_argument("--start", type=int, required=True, help="開始年")
    p_single.add_argument("--end", type=int, required=True, help="終了年")
    p_single.add_argument(
        "--no-tick-sim", action="store_true",
        help="TickSimを無効化（M1 OHLC のみで判定）",
    )
    p_single.add_argument(
        "--no-sequential", action="store_true",
        help="年間 bot 状態引き継ぎを無効化",
    )
    p_single.add_argument(
        "--period-start", default=None,
        help="期間開始 ISO日時 (例: 2025-06-01)",
    )
    p_single.add_argument(
        "--period-end", default=None,
        help="期間終了 ISO日時 (exclusive、例: 2025-07-01)",
    )

    # --- run-portfolio ---（マルチペア統合BT）
    p_port = sub.add_parser(
        "run-portfolio",
        help="マルチペアBT（時系列インターリーブ + 共有資金プール）",
    )
    p_port.add_argument("--symbols", required=True, help="通貨ペア（カンマ区切り）")
    p_port.add_argument("--start", type=int, required=True, help="開始年")
    p_port.add_argument("--end", type=int, required=True, help="終了年")
    p_port.add_argument("--initial-equity", type=float, default=1_000_000.0,
                        help="初期資金 JPY（デフォルト 1,000,000）")
    p_port.add_argument("--global-max-positions", type=int, default=4,
                        help="全ペア合計最大同時ポジション数")
    p_port.add_argument("--per-pair-max-positions", type=int, default=1,
                        help="ペア当たり最大同時ポジション数")
    p_port.add_argument("--global-max-exposure-lot", type=float, default=10.0,
                        help="全ペア合計最大ロット")
    p_port.add_argument("--max-same-direction-jpy", type=int, default=3,
                        help="JPYペア同方向制限（0=無制限）")
    p_port.add_argument("--no-sequential", action="store_true",
                        help="年間 bot 状態引き継ぎを無効化")
    p_port.add_argument("--data-load-workers", type=int, default=6,
                        help="データロード並列ワーカー数")
    p_port.add_argument("--out", help="サマリ JSON 出力パス")
    p_port.add_argument("--period-start", default=None,
                        help="期間開始 ISO日時 (start==end の年内フィルタ用)")
    p_port.add_argument("--period-end", default=None,
                        help="期間終了 ISO日時 (exclusive)")
    p_port.add_argument("--htf-counter-block", action="store_true",
                        help="HTF逆行ハードブロックを有効化")
    p_port.add_argument("--htf-counter-threshold", type=float, default=None,
                        help="HTF逆行ブロック閾値 (例: 0.3)")
    p_port.add_argument("--bot-override-json", default=None,
                        help='UnifiedBotConfig 任意フィールドを JSON で上書き '
                             '(例: \'{"base_risk_pct":0.005,"trend_strength_max":0.5}\')')
    p_port.add_argument("--use-tick-exit", action="store_true",
                        help="ティックレベル SL/TP 精密判定を有効化")
    p_port.add_argument("--tick-check-tf-minutes", type=int, default=1,
                        help="ティック判定で使う基準TFのバー長(分)")
    p_port.add_argument("--trades-csv", default=None,
                        help="trade-by-trade CSV 出力パス")

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
        "run-single": cmd_run_single,
        "run-portfolio": cmd_run_portfolio,
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
