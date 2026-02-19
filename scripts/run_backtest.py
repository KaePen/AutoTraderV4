#!/usr/bin/env python3
"""バックテスト実行スクリプト

統合トレードボット（UnifiedTradeBot）でバックテストを実行する。

使用例:
    # 基本実行
    uv run python scripts/run_backtest.py

    # 年範囲指定
    uv run python scripts/run_backtest.py --years 2020-2024

    # ウォークフォワード検証
    uv run python scripts/run_backtest.py --walk-forward --years 2015-2025
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path

# Windows cp932エンコーディング問題の回避
# RichがLegacy Windows Terminalで非ASCII文字を出力できない
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace",
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace",
    )
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# プロジェクトルートをパスに追加
try:
    project_root = Path(__file__).parent.parent
except NameError:
    project_root = Path("/home/yamas/projects/AutoTraderV4")
sys.path.insert(0, str(project_root / "src"))


def setup_logging(verbose: bool = False) -> None:
    """ロギング設定

    Args:
        verbose: 詳細出力モード
    """
    try:
        from rich.logging import RichHandler
        from rich.console import Console

        console = Console()
        level = logging.DEBUG if verbose else logging.INFO

        logging.basicConfig(
            level=level,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[
                RichHandler(
                    console=console,
                    rich_tracebacks=True,
                    show_time=False,
                    show_path=False,
                )
            ],
        )
    except ImportError:
        # richがない場合は標準ロギング
        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(message)s",
        )


def parse_args() -> argparse.Namespace:
    """引数パース

    Returns:
        argparse.Namespace: パース結果
    """
    parser = argparse.ArgumentParser(
        description="AutoTraderV4 バックテスト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 基本実行（2020-2024年）
  uv run python scripts/run_backtest.py

  # 年範囲指定
  uv run python scripts/run_backtest.py --years 2020-2025

  # ウォークフォワード検証
  uv run python scripts/run_backtest.py --walk-forward --years 2015-2025

  # 診断モード
  uv run python scripts/run_backtest.py --diagnose --years 2023

  # シグナルデバッグ
  uv run python scripts/run_backtest.py --debug-signal "2023-03-15 10:30"

  # パラメータ最適化
  uv run python scripts/run_backtest.py --optimize

  # 高速並列バックテスト
  uv run python scripts/run_backtest.py --fast --years 2023

  # 詳細設定
  uv run python scripts/run_backtest.py --symbol USDJPY --timeframe M15 --volume 1.0
        """,
    )

    # 期間設定
    parser.add_argument(
        "--years",
        type=str,
        default="2020-2024",
        help="バックテスト期間（例: 2020-2024）",
    )

    # シンボル・時間足設定
    parser.add_argument(
        "--symbol",
        default="USDJPY",
        help="シンボル（デフォルト: USDJPY）",
    )
    parser.add_argument(
        "-tf", "--timeframe",
        default="M15",
        choices=["M1", "M5", "M15", "H1", "H4", "D1"],
        help="基準時間足（デフォルト: M15）",
    )

    # 資金設定
    parser.add_argument(
        "--initial-balance",
        type=float,
        default=1_000_000.0,
        help="初期残高（デフォルト: 1000000）",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=1.0,
        help="取引ボリューム（デフォルト: 1.0）",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=1,
        help="最大ポジション数（デフォルト: 1）",
    )

    # 実行モード
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="ウォークフォワード検証を実行",
    )
    parser.add_argument(
        "--use-short-tf",
        action="store_true",
        default=True,
        help="短い時間足（M5）を基準に使用（デフォルト: True）",
    )
    parser.add_argument(
        "--no-short-tf",
        action="store_true",
        help="短い時間足を使用しない（M15基準）",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="並列マルチTFバックテストを実行（全TFでエントリー可能）",
    )
    parser.add_argument(
        "--enable-scalping",
        action="store_true",
        help="スキャルピングモード有効化（M1/M5からエントリー可能）",
    )

    # 拡張モード
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="診断モード（データ品質チェック、シグナル統計）",
    )
    parser.add_argument(
        "--debug-signal",
        type=str,
        default=None,
        metavar="TIME",
        help="特定時刻のシグナルデバッグ（例: '2023-03-15 10:30'）",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="パラメータ最適化を実行",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="高速並列バックテストを実行",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="軽量バックテスト（サンプリング実行）",
    )
    # RANGE×DAY BE制御
    parser.add_argument(
        "--no-range-day-be-fix",
        action="store_true",
        help="DAY_TRADE×RANGE BE修正を無効化",
    )
    parser.add_argument(
        "--range-day-be-r",
        type=float,
        default=0.3,
        help="DAY_TRADE×RANGEのBE閾値R（デフォルト: 0.3）",
    )
    parser.add_argument(
        "--no-fast-be",
        action="store_true",
        help="速度ベースBE無効化",
    )
    parser.add_argument(
        "--fast-be-minutes",
        type=float,
        default=90.0,
        help="速い到達の時間閾値（デフォルト: 90分）",
    )
    parser.add_argument(
        "--range-stag",
        action="store_true",
        help="RANGE×DAY stagnation段階化を有効化（デフォルトOFF）",
    )
    parser.add_argument(
        "--range-stag-s1-min",
        type=float,
        default=45.0,
        help="RANGE×DAY stagnation Stage1時間（デフォルト: 45分）",
    )
    parser.add_argument(
        "--range-stag-s1-mfe",
        type=float,
        default=0.05,
        help="RANGE×DAY stagnation Stage1 MFE閾値（デフォルト: 0.05）",
    )
    parser.add_argument(
        "--range-stag-s2-min",
        type=float,
        default=60.0,
        help="RANGE×DAY stagnation Stage2時間（デフォルト: 60分）",
    )
    parser.add_argument(
        "--range-stag-s2-mfe",
        type=float,
        default=0.10,
        help="RANGE×DAY stagnation Stage2 MFE閾値（デフォルト: 0.10）",
    )
    parser.add_argument(
        "--early-partial-close",
        action="store_true",
        help="0.5R小利確を有効化（P0-3）",
    )
    parser.add_argument(
        "--early-partial-ratio",
        type=float,
        default=0.25,
        help="小利確比率（デフォルト: 0.25）",
    )
    # RANGE×DAY 軽い保険
    parser.add_argument(
        "--no-range-insurance",
        action="store_true",
        help="RANGE×DAY保険ロジック無効化",
    )
    parser.add_argument(
        "--insurance-max-min",
        type=float,
        default=30.0,
        help="保険: 0.3R到達の時間制限（デフォルト: 30分）",
    )
    parser.add_argument(
        "--insurance-sl-r",
        type=float,
        default=-0.1,
        help="保険: SLオフセットR値（デフォルト: -0.1）",
    )
    parser.add_argument(
        "--insurance-partial-ratio",
        type=float,
        default=0.20,
        help="保険: 部分利確率（デフォルト: 0.20）",
    )
    parser.add_argument(
        "--insurance-trigger-r",
        type=float,
        default=1.0,
        help="保険: 部分利確トリガーR値（デフォルト: 1.0）",
    )
    parser.add_argument(
        "--no-insurance-mfe-block",
        action="store_true",
        help="保険: MFE>=0.8Rブロック無効化",
    )
    parser.add_argument(
        "--insurance-min-hold",
        type=float,
        default=15.0,
        help="保険: 最低保有時間（分）",
    )
    # Weak Hours RANGEフィルター
    parser.add_argument(
        "--no-weak-hours",
        action="store_true",
        help="Weak Hours RANGEフィルター無効化",
    )
    parser.add_argument(
        "--weak-hours-premium",
        type=float,
        default=0.5,
        help="Weak Hoursスコアプレミアム（デフォルト: 0.5）",
    )
    # 東京深夜SWINGフィルター
    parser.add_argument(
        "--no-tokyo-night-swing",
        action="store_true",
        help="東京深夜SWINGフィルター無効化",
    )
    parser.add_argument(
        "--tokyo-night-swing-premium",
        type=float,
        default=0.3,
        help="東京深夜SWINGスコアプレミアム（デフォルト: 0.3）",
    )
    # 通常Stagnation設定
    parser.add_argument(
        "--stag-exit-minutes",
        type=float,
        default=120.0,
        help="通常Stagnation時間（デフォルト: 120分）",
    )
    parser.add_argument(
        "--stag-min-mfe",
        type=float,
        default=0.15,
        help="通常Stagnation MFE閾値（デフォルト: 0.15）",
    )
    # SWING専用Stagnation設定
    parser.add_argument(
        "--swing-stag-minutes",
        type=float,
        default=120.0,
        help="SWING Stagnation時間（デフォルト: 120分）",
    )
    parser.add_argument(
        "--swing-stag-mfe",
        type=float,
        default=0.15,
        help="SWING Stagnation MFE閾値（デフォルト: 0.15）",
    )
    # SWING×TREND専用Stagnation設定
    parser.add_argument(
        "--no-swing-trend-stag",
        action="store_true",
        help="SWING×TREND stagnation厳格化を無効化",
    )
    parser.add_argument(
        "--swing-trend-stag-minutes",
        type=float,
        default=90.0,
        help="SWING×TREND Stagnation時間（デフォルト: 90分）",
    )
    parser.add_argument(
        "--swing-trend-stag-mfe",
        type=float,
        default=0.15,
        help="SWING×TREND Stagnation MFE閾値（デフォルト: 0.15）",
    )
    # RANGE×DAY 入口フィルター
    parser.add_argument(
        "--range-day-bbw",
        type=float,
        default=0.20,
        help="RANGE×DAY bb_width閾値（デフォルト: 0.20）",
    )
    parser.add_argument(
        "--range-day-score-premium",
        type=float,
        default=0.55,
        help="RANGE×DAYスコアプレミアム（デフォルト: 0.55）",
    )
    parser.add_argument(
        "--no-range-day-score-premium",
        action="store_true",
        help="RANGE×DAYスコアプレミアム無効化",
    )
    # 動的ロットサイズ・資金管理
    parser.add_argument(
        "--fixed-lot",
        action="store_true",
        help="固定ロットサイズ（動的サイジング無効、--volumeの値を使用）",
    )
    parser.add_argument(
        "--max-lot-per-trade",
        type=float,
        default=2.0,
        help="1トレードあたりの上限ロット（デフォルト: 2.0）",
    )
    parser.add_argument(
        "--max-total-exposure",
        type=float,
        default=5.0,
        help="合計オープンロット上限（デフォルト: 5.0）",
    )
    parser.add_argument(
        "--risk-pct",
        type=float,
        default=0.02,
        help="基本リスク率（デフォルト: 0.02=2%%）",
    )
    parser.add_argument(
        "--equity-floor",
        type=float,
        default=0.30,
        help="取引停止の資金下限率（デフォルト: 0.30=30%%）",
    )
    parser.add_argument(
        "--slippage-buffer",
        type=float,
        default=2.0,
        help="SLスリッページバッファpips（デフォルト: 2.0）",
    )
    # RANGE×DAY 0.5R部分利確（デフォルトON）
    parser.add_argument(
        "--no-range-day-half-r-partial",
        action="store_true",
        help="RANGE×DAY 0.5R部分利確を無効化",
    )
    # spread/slippage上書き
    parser.add_argument(
        "--spread", type=float, default=None,
        help="スプレッド上書き（pips）",
    )
    parser.add_argument(
        "--slippage", type=float, default=None,
        help="スリッページ上書き（pips）",
    )
    # その他
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/csv",
        help="データディレクトリ（デフォルト: data/csv）",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="詳細出力",
    )

    return parser.parse_args()


def parse_years(years_str: str) -> tuple[int, int]:
    """年範囲をパース

    Args:
        years_str: 年範囲（例: "2020-2024"）

    Returns:
        tuple[int, int]: (開始年, 終了年)
    """
    if "-" in years_str:
        parts = years_str.split("-")
        return int(parts[0]), int(parts[1])
    year = int(years_str)
    return year, year


def print_header(args: argparse.Namespace) -> None:
    """ヘッダーを表示"""
    use_short_tf = not args.no_short_tf
    base_tf = "M5（5分毎判断）" if use_short_tf else "M15（15分毎判断）"

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        # 設定テーブル作成
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("項目", style="cyan")
        table.add_column("値", style="white")

        table.add_row("シンボル", args.symbol)
        table.add_row("判断頻度", base_tf)
        table.add_row("評価時間足", "M5, M15, H1, H4, D1")
        table.add_row("期間", args.years)
        table.add_row("初期残高", f"JPY{args.initial_balance:,.0f}")
        table.add_row("ボリューム", str(args.volume))
        if args.parallel:
            table.add_row("並列TF", "[green]有効[/green]")
        if args.enable_scalping:
            table.add_row("スキャルピング", "[green]有効[/green]")

        console.print()
        console.print(Panel(
            table,
            title="[bold blue]AutoTraderV4 バックテスト[/bold blue]",
            border_style="blue",
        ))
        console.print()

    except ImportError:
        # richがない場合は従来の出力
        print("=" * 80)
        print("AutoTraderV4 バックテスト")
        print("=" * 80)
        print(f"シンボル: {args.symbol}")
        print(f"トレード判断頻度: {base_tf}")
        print(f"評価時間足: M5, M15, H1, H4, D1（マルチタイムフレーム）")
        print(f"期間: {args.years}")
        print(f"初期残高: JPY{args.initial_balance:,.0f}")
        print(f"ボリューム: {args.volume}")
        print("=" * 80)


def print_results(result) -> None:
    """結果を表示"""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel

        console = Console()

        # 結果テーブル
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("指標", style="cyan")
        table.add_column("値", justify="right")

        table.add_row("総取引数", str(result.trades))

        # 勝率に色付け
        win_color = "green" if result.win_rate >= 50 else "yellow"
        table.add_row("勝率", f"[{win_color}]{result.win_rate:.1f}%[/{win_color}]")

        # 非敗率
        nlr = result.non_loss_rate
        nlr_color = "green" if nlr >= 60 else "yellow"
        table.add_row("非敗率", f"[{nlr_color}]{nlr:.1f}%[/{nlr_color}]")

        # PFに色付け
        pf_color = "green" if result.profit_factor >= 1.5 else (
            "yellow" if result.profit_factor >= 1.0 else "red"
        )
        table.add_row(
            "プロフィットファクター",
            f"[{pf_color}]{result.profit_factor:.2f}[/{pf_color}]"
        )

        # 純利益に色付け
        profit_color = "green" if result.net_profit > 0 else "red"
        table.add_row(
            "純利益",
            f"[{profit_color}]JPY{result.net_profit:+,.0f}[/{profit_color}]"
        )

        # ドローダウン
        dd_color = "green" if result.max_drawdown < 10 else (
            "yellow" if result.max_drawdown < 20 else "red"
        )
        table.add_row(
            "最大ドローダウン",
            f"[{dd_color}]{result.max_drawdown:.2f}%[/{dd_color}]"
        )

        table.add_row("シャープレシオ", f"{result.sharpe_ratio:.2f}")

        # 年間収益率に色付け
        ar_color = "green" if result.annual_return > 0 else "red"
        table.add_row(
            "年間平均収益率",
            f"[{ar_color}]{result.annual_return:.1f}%[/{ar_color}]"
        )

        console.print()
        console.print(Panel(
            table,
            title="[bold]バックテスト結果[/bold]",
            border_style="blue",
        ))

    except ImportError:
        print(f"\n{'-' * 80}")
        print("バックテスト結果")
        print(f"{'-' * 80}")
        print(f"総取引数: {result.trades}")
        print(f"勝率: {result.win_rate:.1f}%")
        nlr = result.non_loss_rate
        print(f"非敗率: {nlr:.1f}%")
        print(f"プロフィットファクター: {result.profit_factor:.2f}")
        print(f"純利益: JPY{result.net_profit:+,.0f}")
        print(f"最大ドローダウン: {result.max_drawdown:.2f}%")
        print(f"シャープレシオ: {result.sharpe_ratio:.2f}")
        print(f"年間平均収益率: {result.annual_return:.1f}%")


def print_yearly_results(yearly_results: list) -> None:
    """年別結果を表示"""
    if not yearly_results:
        return

    print(f"\n{'=' * 80}")
    print("年別詳細")
    print(f"{'=' * 80}")
    print(
        f"{'年':<6} {'取引数':>8} {'勝率':>8} {'非敗率':>8} "
        f"{'PF':>8} {'利益':>14} {'DD':>8}"
    )
    print("-" * 90)

    for r in yearly_results:
        nlr = r.get("non_loss_rate", 0.0)
        print(
            f"{r['year']:<6} "
            f"{r['trades']:>8} "
            f"{r['win_rate']:>7.1f}% "
            f"{nlr:>7.1f}% "
            f"{r['profit_factor']:>7.2f} "
            f"JPY{r['net_profit']:>+12,.0f} "
            f"{r['max_drawdown']:>7.2f}%"
        )

    print("-" * 90)
    profitable = sum(1 for r in yearly_results if r["net_profit"] > 0)
    print(f"黒字年: {profitable}/{len(yearly_results)}")


def print_monthly_summary(monthly_results: list, verbose: bool = False) -> None:
    """月別サマリーを表示"""
    if not monthly_results:
        return

    print(f"\n{'=' * 80}")
    print("月別サマリー")
    print(f"{'=' * 80}")

    # 月間+5%達成率を計算
    target_months = sum(1 for r in monthly_results if r["return_pct"] >= 5.0)
    total_months = len(monthly_results)
    avg_return = sum(r["return_pct"] for r in monthly_results) / total_months
    positive_months = sum(1 for r in monthly_results if r["return_pct"] > 0)

    print(f"月間プラス率: {positive_months}/{total_months} "
          f"({100*positive_months/total_months:.1f}%)")
    print(f"月間+5%達成: {target_months}/{total_months} "
          f"({100*target_months/total_months:.1f}%)")
    print(f"月間平均収益率: {avg_return:.2f}%")

    # 直近12ヶ月
    if len(monthly_results) >= 12:
        recent = monthly_results[-12:]
        recent_target = sum(1 for r in recent if r["return_pct"] >= 5.0)
        recent_avg = sum(r["return_pct"] for r in recent) / 12
        print(f"\n直近12ヶ月:")
        print(f"  +5%達成: {recent_target}/12")
        print(f"  平均収益率: {recent_avg:.2f}%")


def print_walk_forward_results(results: list) -> None:
    """ウォークフォワード結果を表示"""
    print(f"\n{'=' * 80}")
    print("ウォークフォワード検証結果")
    print(f"{'=' * 80}")
    print(
        f"{'訓練期間':<12} {'検証期間':<12} "
        f"{'訓練収益':>10} {'検証収益':>10} {'取引数':>8}"
    )
    print("-" * 80)

    for r in results:
        print(
            f"{r['train_period']:<12} "
            f"{r['valid_period']:<12} "
            f"{r['train_return']:>9.1f}% "
            f"{r['valid_return']:>9.1f}% "
            f"{r['valid_trades']:>8}"
        )

    print("-" * 80)

    # オーバーフィット警告
    overfit_count = 0
    for r in results:
        if r["train_return"] > 0 and r["valid_return"] < 0:
            overfit_count += 1
        elif (
            r["train_return"] > 0
            and r["valid_return"] < r["train_return"] * 0.5
        ):
            overfit_count += 1

    if overfit_count > len(results) * 0.5:
        print("警告: オーバーフィットの可能性があります")
    else:
        print("検証期間でも安定したパフォーマンス")


def run_single_backtest(args: argparse.Namespace):
    """単一バックテスト実行"""
    from autotrader.backtest.service import (
        BacktestService,
        BacktestServiceConfig,
    )
    from autotrader.backtest.events import RichEventListener

    start_year, end_year = parse_years(args.years)

    # 短い時間足オプション（--no-short-tfが指定されていなければTrue）
    use_short_tf = not args.no_short_tf

    config = BacktestServiceConfig(
        start_year=start_year,
        end_year=end_year,
        initial_balance=args.initial_balance,
        volume=args.volume,
        data_dir=args.data_dir,
        symbol=args.symbol,
        timeframe=args.timeframe,
        max_positions=args.max_positions,
        verbose=False,
        use_short_timeframe=use_short_tf,
        use_parallel_tf=args.parallel,
        enable_scalping=args.enable_scalping,
    )
    # spread/slippage上書き
    if args.spread is not None:
        config.spread_pips = args.spread
    if args.slippage is not None:
        config.slippage_pips = args.slippage

    # サービス作成とRichリスナー追加
    service = BacktestService(config)
    rich_listener = RichEventListener(verbose=args.verbose)
    service.add_listener(rich_listener)

    # データ読み込み（進捗表示付き）
    try:
        from rich.console import Console
        from rich.progress import Progress, SpinnerColumn, TextColumn

        console = Console()

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("データ読み込み中...", total=None)
            runner = service.create_runner()
            runner.load_data()

        console.print("[green]✓[/green] データ読み込み完了")

    except ImportError:
        print("データ読み込み中...")
        runner = service.create_runner()
        runner.load_data()
        print("データ読み込み完了")

    # バックテスト実行
    from autotrader.decision.unified import UnifiedBotConfig
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )
    _score_prem = (
        0.0 if args.no_range_day_score_premium
        else args.range_day_score_premium
    )
    bot_config = UnifiedBotConfig(
        timeframes=["M15", "H1", "H4", "D1"],
        range_day_bbw_threshold=args.range_day_bbw,
        range_day_score_premium=_score_prem,
        weak_hours_enabled=not args.no_weak_hours,
        weak_hours_score_premium=args.weak_hours_premium,
        tokyo_night_swing_enabled=not args.no_tokyo_night_swing,
        tokyo_night_swing_premium=args.tokyo_night_swing_premium,
        use_dynamic_lot=not args.fixed_lot,
        max_lot_per_trade=args.max_lot_per_trade,
        max_total_exposure_lot=args.max_total_exposure,
        base_risk_pct=args.risk_pct,
        equity_floor_pct=args.equity_floor,
        slippage_buffer_pips=args.slippage_buffer,
    )

    # PositionManagerConfig構築
    pm_config = PositionManagerConfig(
        stagnation_exit_minutes=args.stag_exit_minutes,
        stagnation_min_mfe_r=args.stag_min_mfe,
        range_day_be_disabled=not args.no_range_day_be_fix,
        range_day_early_be_r=args.range_day_be_r,
        range_day_fast_be_enabled=not args.no_fast_be,
        range_day_fast_be_minutes=args.fast_be_minutes,
        range_day_stagnation_enabled=args.range_stag,
        range_day_stagnation_stage1_minutes=args.range_stag_s1_min,
        range_day_stagnation_stage1_min_mfe_r=args.range_stag_s1_mfe,
        range_day_stagnation_stage2_minutes=args.range_stag_s2_min,
        range_day_stagnation_stage2_min_mfe_r=args.range_stag_s2_mfe,
        early_partial_close_enabled=args.early_partial_close,
        early_partial_close_ratio=args.early_partial_ratio,
        range_day_insurance_enabled=not args.no_range_insurance,
        range_day_insurance_max_minutes=args.insurance_max_min,
        range_day_insurance_sl_offset_r=args.insurance_sl_r,
        range_day_insurance_partial_ratio=args.insurance_partial_ratio,
        insurance_trigger_r=args.insurance_trigger_r,
        insurance_block_high_mfe_r=(
            999.0 if args.no_insurance_mfe_block else 0.8
        ),
        insurance_min_holding_minutes=args.insurance_min_hold,
        swing_stagnation_exit_minutes=args.swing_stag_minutes,
        swing_stagnation_min_mfe_r=args.swing_stag_mfe,
        swing_trend_stagnation_enabled=(
            not args.no_swing_trend_stag
        ),
        swing_trend_stagnation_exit_minutes=(
            args.swing_trend_stag_minutes
        ),
        swing_trend_stagnation_min_mfe_r=(
            args.swing_trend_stag_mfe
        ),
        range_day_half_r_partial_enabled=(
            not args.no_range_day_half_r_partial
        ),
    )

    result = runner.run_unified(
        start_year,
        end_year,
        bot_config,
        use_m1=use_short_tf,
        use_parallel_tf=args.parallel,
        enable_scalping=args.enable_scalping,
        pm_config=pm_config,
    )

    print_results(result)
    print_yearly_results(result.yearly_results)
    print_monthly_summary(result.monthly_results, args.verbose)


def run_walk_forward(args: argparse.Namespace):
    """ウォークフォワード検証実行"""
    from autotrader.backtest.runner import BacktestRunner, BacktestConfig

    start_year, end_year = parse_years(args.years)

    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        initial_balance=args.initial_balance,
        volume=args.volume,
        max_positions=args.max_positions,
    )

    runner = BacktestRunner(
        data_dir=args.data_dir,
        config=config,
        verbose=args.verbose,
    )

    print("\nデータ読み込み中...")
    runner.load_data()

    print("\nウォークフォワード検証実行中...")
    results = runner.run_walk_forward(
        "high-win-rate",
        train_years=3,
        valid_years=1,
        start_year=start_year,
        end_year=end_year,
    )

    print_walk_forward_results(results)


def run_diagnose(args: argparse.Namespace):
    """診断モード実行"""
    from autotrader.backtest.diagnostics import run_diagnostics

    start_year, _ = parse_years(args.years)
    run_diagnostics(
        year=start_year,
        data_dir=args.data_dir,
        symbol=args.symbol,
    )


def run_debug_signal_mode(args: argparse.Namespace):
    """シグナルデバッグモード実行"""
    from autotrader.backtest.diagnostics import run_debug_signal

    run_debug_signal(
        target_time=args.debug_signal,
        data_dir=args.data_dir,
        symbol=args.symbol,
    )


def run_optimize(args: argparse.Namespace):
    """パラメータ最適化実行"""
    from autotrader.backtest.optimizer import run_optimization

    start_year, end_year = parse_years(args.years)
    # 訓練期間=指定期間、検証期間=その後2年
    run_optimization(
        data_dir=args.data_dir,
        symbol=args.symbol,
        train_years=(start_year, end_year),
        valid_years=(end_year + 1, end_year + 3),
    )


def run_fast(args: argparse.Namespace):
    """高速並列バックテスト実行"""
    from datetime import datetime as dt

    from autotrader.backtest.data_loader import DataLoader
    from autotrader.backtest.fast_backtest import (
        FastBacktestConfig,
        FastBacktestEngine,
    )
    from autotrader.calculator.precompute import PrecomputeEngine
    from autotrader.core.enums import Timeframe

    start_year, end_year = parse_years(args.years)
    start_date = dt(start_year, 1, 1)
    end_date = dt(end_year + 1, 1, 1)
    base_tf = Timeframe(args.timeframe)

    data_dir = Path(args.data_dir)
    timeframes_map = {
        "M1": "M1", "M5": "M5", "M15": "M15",
        "H1": "H1", "H4": "H4",
    }
    raw_data: dict[str, any] = {}

    print("データ読み込み中...")
    for tf_str, file_tf in timeframes_map.items():
        pattern = f"{args.symbol}_{file_tf}_*.csv"
        files = list(data_dir.glob(pattern))
        if files:
            df = DataLoader.load_mt5_csv(files[0])
            df = df[
                (df["time"] >= start_date)
                & (df["time"] < end_date)
            ].copy()
            if not df.empty:
                raw_data[tf_str] = df
                print(f"  {tf_str}: {len(df)}行")

    if not raw_data:
        print("データが見つかりません")
        return

    print("指標計算中...")
    precompute = PrecomputeEngine()
    market_data: dict[str, any] = {}
    for tf_str, df in raw_data.items():
        tf = Timeframe(tf_str)
        df_indexed = df.set_index("time")
        indicator_df = precompute.precompute(
            df_indexed, args.symbol, tf, use_cache=False
        )
        market_data[tf_str] = indicator_df
        print(f"  {tf_str}: 指標計算完了")

    base_tf_str = base_tf.value
    if base_tf_str not in market_data:
        print(f"基準TF {base_tf_str} のデータなし")
        return

    base_df = market_data[base_tf_str]
    if "time" not in base_df.columns:
        base_df = base_df.reset_index()
        base_df = base_df.rename(columns={"index": "time"})

    config = FastBacktestConfig(
        symbol=args.symbol,
        start_date=start_date,
        end_date=end_date,
        chunk_months=3,
        base_timeframe=base_tf,
    )

    import time

    print("高速バックテスト実行中...")
    engine = FastBacktestEngine(config)
    t0 = time.time()
    result = engine.run(base_df, market_data)
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print("高速バックテスト結果")
    print(f"{'=' * 60}")
    print(f"総トレード数: {result.total_trades}")
    print(f"勝率: {result.win_rate:.2f}%")
    print(f"PF: {result.profit_factor:.2f}")
    print(f"収益率: {result.return_pct:.2f}%")
    print(f"最大DD: {result.max_drawdown_pct:.2f}%")
    print(f"処理時間: {elapsed:.2f}秒")


def run_quick(args: argparse.Namespace):
    """軽量バックテスト実行"""
    from autotrader.backtest.data_loader import DataLoader
    from autotrader.backtest.simulator import (
        SimulatorConfig,
        TradeSimulator,
    )
    from autotrader.config import DEFAULT_TRADING_PARAMS
    from autotrader.core.entities import Candle, Signal
    from autotrader.core.enums import (
        ExitReason,
        SignalType,
        Timeframe,
    )
    from autotrader.decision.unified import (
        UnifiedBotConfig,
        UnifiedTradeBot,
    )

    start_year, _ = parse_years(args.years)
    data_dir = Path(args.data_dir)

    # データ読み込み
    print(f"=== 軽量バックテスト {start_year}年 ===")
    print("データ読み込み中...")

    tf_patterns = {
        "M5": f"{args.symbol}_M5_*.csv",
        "M15": f"{args.symbol}_M15_*.csv",
        "H1": f"{args.symbol}_H1_*.csv",
        "H4": f"{args.symbol}_H4_*.csv",
        "D1": f"{args.symbol}_D1_*.csv",
    }
    market_data: dict[str, any] = {}
    for tf, pattern in tf_patterns.items():
        files = list(data_dir.glob(pattern))
        if files:
            df = DataLoader.load_mt5_csv(files[0])
            market_data[tf] = df
            print(f"  {tf}: {len(df):,}本")

    m5_df = market_data.get("M5")
    if m5_df is None:
        print("M5データなし")
        return

    from datetime import datetime as dt

    start_date = dt(start_year, 1, 1)
    end_date = dt(start_year + 1, 1, 1)
    period_df = m5_df[
        (m5_df["time"] >= start_date)
        & (m5_df["time"] < end_date)
    ].reset_index(drop=True)

    print(f"期間データ: {len(period_df):,}本")

    # ボット・シミュレーター初期化
    import pandas as pd

    bot_config = UnifiedBotConfig(
        timeframes=["M5", "M15", "H1", "H4", "D1"],
        enable_position_sizing=False,
    )
    bot = UnifiedTradeBot(bot_config)
    bot.set_market_data(market_data)

    initial_balance = args.initial_balance
    sim_config = SimulatorConfig(
        initial_balance=initial_balance,
        spread_pips=DEFAULT_TRADING_PARAMS.spread_pips,
        pip_value=DEFAULT_TRADING_PARAMS.pip_value,
        max_positions=1,
        default_volume=args.volume,
    )
    simulator = TradeSimulator(config=sim_config)

    # サンプリング実行（12本に1回=1時間に1回）
    sample_rate = 12
    last_candle = None
    for idx in range(0, len(period_df), sample_rate):
        row = period_df.iloc[idx]
        current_time = pd.Timestamp(row["time"])

        candle = Candle(
            symbol=args.symbol,
            timeframe=Timeframe.M5,
            time=row["time"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        last_candle = candle

        signal = None
        if len(simulator.get_open_positions()) == 0:
            consolidated = bot.generate_signal(current_time)
            if (
                consolidated.direction != SignalType.HOLD
                and consolidated.confidence >= 0.5
            ):
                close = candle.close
                sl_pips = consolidated.sl_pips
                tp_pips = consolidated.tp_pips
                if consolidated.direction == SignalType.BUY:
                    sl_price = close - sl_pips / 100
                    tp_price = close + tp_pips / 100
                else:
                    sl_price = close + sl_pips / 100
                    tp_price = close - tp_pips / 100

                signal = Signal(
                    symbol=args.symbol,
                    timeframe=Timeframe.M5,
                    signal_type=consolidated.direction,
                    confidence=consolidated.confidence,
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    reasoning=consolidated.rationale,
                )

        simulator.process_candle(candle, signal)

        if idx % (sample_rate * 100) == 0:
            progress = idx / len(period_df) * 100
            closed = len(simulator.get_closed_trades())
            print(f"  進捗: {progress:.1f}% トレード: {closed}")

    if last_candle:
        simulator.force_close_all(
            last_candle, ExitReason.FORCE_CLOSE
        )

    trades = simulator.get_closed_trades()
    if not trades:
        print("トレードなし")
        return

    wins = sum(1 for t in trades if (t.profit_loss or 0) > 0)
    total_pnl = sum((t.profit_loss or 0) for t in trades)
    gross_profit = sum(
        (t.profit_loss or 0) for t in trades
        if (t.profit_loss or 0) > 0
    )
    gross_loss = abs(sum(
        (t.profit_loss or 0) for t in trades
        if (t.profit_loss or 0) < 0
    ))
    pf = gross_profit / gross_loss if gross_loss > 0 else 0

    print(f"\n=== 結果 ===")
    print(f"トレード数: {len(trades)}")
    print(f"勝率: {wins / len(trades) * 100:.1f}%")
    print(f"損益: JPY{total_pnl:,.0f}")
    print(f"PF: {pf:.2f}")


def main():
    """メイン関数"""
    args = parse_args()

    # ロギング設定
    setup_logging(args.verbose)

    print_header(args)

    try:
        if args.diagnose:
            run_diagnose(args)
        elif args.debug_signal:
            run_debug_signal_mode(args)
        elif args.optimize:
            run_optimize(args)
        elif args.fast:
            run_fast(args)
        elif args.quick:
            run_quick(args)
        elif args.walk_forward:
            run_walk_forward(args)
        else:
            run_single_backtest(args)

        print("\n" + "=" * 80)
        print("完了")
        print("=" * 80)

    except KeyboardInterrupt:
        print("\n\n中断されました")
        sys.exit(130)
    except Exception as e:
        try:
            from rich.console import Console
            console = Console()
            console.print_exception()
        except ImportError:
            print(f"\nエラー: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
