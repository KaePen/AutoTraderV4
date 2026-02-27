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
    project_root = Path("D:/Projects/AutoTraderV4")
sys.path.insert(0, str(project_root))


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
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="開始日（日単位指定、--yearsより優先、例: 2023-06-01）",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="終了日（日単位指定、--yearsより優先、例: 2025-09-30）",
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
        choices=[
            "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12",
            "M15", "M20", "M30", "H1", "H2", "H3", "H4",
            "H6", "H8", "H12", "D1",
        ],
        help="基準時間足（デフォルト: M15）",
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default=None,
        help="使用TFリスト（カンマ区切り。例: M1,M5,M15,H1,H4,D1）",
    )
    parser.add_argument(
        "--max-year-workers",
        type=int,
        default=5,
        help="年並列の最大同時実行数（デフォルト: 5）",
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
        help="短い時間足（M1）を基準に使用（デフォルト: True）",
    )
    parser.add_argument(
        "--no-short-tf",
        action="store_true",
        help="短い時間足を使用しない（M15基準）",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="シーケンシャル実行を強制（デバッグ用。デフォルトは年単位並列）",
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
        "--fast",
        action="store_true",
        help=argparse.SUPPRESS,  # 非推奨: 通常モードで並列化済み
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
        default=5.0,
        help="1トレードあたりの上限ロット（デフォルト: 5.0）",
    )
    parser.add_argument(
        "--max-total-exposure",
        type=float,
        default=10.0,
        help="合計オープンロット上限（デフォルト: 10.0）",
    )
    parser.add_argument(
        "--risk-pct",
        type=float,
        default=0.04,
        help="基本リスク率（デフォルト: 0.04=4%%）",
    )
    parser.add_argument(
        "--max-risk-pct-abs",
        type=float,
        default=0.07,
        help="絶対最大リスク率（デフォルト: 0.07=7%%）",
    )
    parser.add_argument(
        "--equity-floor",
        type=float,
        default=0.30,
        help="取引停止の資金下限率（デフォルト: 0.30=30%%）",
    )
    parser.add_argument(
        "--equity-caution",
        type=float,
        default=0.50,
        help="ロット減額開始の資金注意率（デフォルト: 0.50=50%%）",
    )
    parser.add_argument(
        "--tp-sl-ratio",
        type=float,
        default=1.2,
        help="TP/SL比率乗数（デフォルト: 1.2）",
    )
    parser.add_argument(
        "--slippage-buffer",
        type=float,
        default=2.0,
        help="SLスリッページバッファpips（デフォルト: 2.0）",
    )
    parser.add_argument(
        "--keep-tp-after-partial",
        action="store_true",
        help="1R部分利確後もTPを維持（デフォルト: 無効=TP無効化）",
    )
    parser.add_argument(
        "--consensus-threshold",
        type=float,
        default=8.0,
        help="コンセンサス閾値（デフォルト: 8.0）",
    )
    parser.add_argument(
        "--penalty-cap",
        type=float,
        default=0.3,
        help="ペナルティ上限（デフォルト: 0.3）",
    )
    parser.add_argument(
        "--trend-strength-max",
        type=float,
        default=0.8,
        help="トレンド強度上限（デフォルト: 0.8）",
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
    # --- ポジション管理（PM）設定 ---
    parser.add_argument(
        "--partial-1r-ratio",
        type=float,
        default=0.05,
        help="1R到達時の部分決済比率（デフォルト: 0.05）",
    )
    parser.add_argument(
        "--partial-2r-ratio",
        type=float,
        default=0.05,
        help="2R到達時の部分決済比率（デフォルト: 0.05）",
    )
    parser.add_argument(
        "--no-breakeven-1r",
        action="store_true",
        help="1R建値移動を無効化",
    )
    parser.add_argument(
        "--trailing-start-r",
        type=float,
        default=1.5,
        help="トレーリング開始R値（デフォルト: 1.5）",
    )
    parser.add_argument(
        "--trailing-atr-mult",
        type=float,
        default=1.5,
        help="ATRトレーリング倍率（デフォルト: 1.5）",
    )
    parser.add_argument(
        "--early-be-r",
        type=float,
        default=0.5,
        help="早期BE移動のR閾値（デフォルト: 0.5）",
    )
    parser.add_argument(
        "--no-early-be",
        action="store_true",
        help="早期BE移動を無効化",
    )
    parser.add_argument(
        "--signal-rev-ratio",
        type=float,
        default=0.5,
        help="シグナル反転決済比率（デフォルト: 0.5）",
    )
    parser.add_argument(
        "--half-r-ratio",
        type=float,
        default=0.20,
        help="0.5R部分利確比率（デフォルト: 0.20）",
    )
    parser.add_argument(
        "--half-r-trigger",
        type=float,
        default=0.5,
        help="0.5R部分利確トリガーR値（デフォルト: 0.5）",
    )
    parser.add_argument(
        "--no-time-exit",
        action="store_true",
        help="時間決済を無効化",
    )
    # --- ボット設定 ---
    parser.add_argument(
        "--bonus-max-positions",
        type=int,
        default=0,
        help="高品質シグナル追加枠数（デフォルト: 0=無効）",
    )
    parser.add_argument(
        "--bonus-score-threshold",
        type=float,
        default=7.0,
        help="bonus発動コンセンサス閾値（デフォルト: 7.0）",
    )
    parser.add_argument(
        "--no-position-sizing",
        action="store_true",
        help="ポジションサイジング無効化",
    )
    # --- シミュレーター設定 ---
    parser.add_argument(
        "--commission",
        type=float,
        default=None,
        help="ロット当たり手数料上書き（デフォルト: preset値）",
    )
    parser.add_argument(
        "--session-spread",
        action="store_true",
        help="セッション別スプレッド有効化",
    )
    # --- コンセンサス逆転exit ---
    parser.add_argument(
        "--consensus-exit",
        action="store_true",
        help="コンセンサス逆転exitを有効化",
    )
    parser.add_argument(
        "--consensus-exit-threshold",
        type=float,
        default=6.0,
        help="逆方向スコア閾値（デフォルト: 6.0）",
    )
    parser.add_argument(
        "--consensus-exit-own-max",
        type=float,
        default=3.0,
        help="自方向スコア上限（デフォルト: 3.0）",
    )
    parser.add_argument(
        "--consensus-exit-loss-only",
        action="store_true",
        help="含み損時のみコンセンサス逆転exitを発動",
    )
    # --- 利益反転ガード ---
    parser.add_argument(
        "--profit-reversal",
        action="store_true",
        help="利益反転ガードを有効化",
    )
    parser.add_argument(
        "--profit-reversal-mfe-r",
        type=float,
        default=0.3,
        help="MFE閾値（R値、デフォルト: 0.3）",
    )
    parser.add_argument(
        "--profit-reversal-drop-r",
        type=float,
        default=0.25,
        help="下落閾値（R値、デフォルト: 0.25）",
    )
    parser.add_argument(
        "--profit-reversal-max-r",
        type=float,
        default=0.05,
        help="現在R上限（デフォルト: 0.05）",
    )
    # --- 段階的STAGNATION ---
    parser.add_argument(
        "--progressive-stagnation",
        action="store_true",
        help="段階的STAGNATIONを有効化",
    )
    # --- レジーム別閾値調整 ---
    parser.add_argument(
        "--regime-threshold",
        action="store_true",
        help="レジーム別閾値調整を有効化",
    )
    parser.add_argument(
        "--regime-trend-add",
        type=float,
        default=1.5,
        help="TREND閾値追加分（デフォルト: 1.5）",
    )
    # --- 低ATR+TRENDフィルター ---
    parser.add_argument(
        "--low-atr-trend-filter",
        action="store_true",
        help="低ATR+TRENDフィルターを有効化",
    )
    parser.add_argument(
        "--low-atr-trend-ratio",
        type=float,
        default=0.75,
        help="低ATR+TRENDフィルターのATR比率閾値（デフォルト: 0.75）",
    )
    # --- HTFスコアフィルター ---
    parser.add_argument(
        "--htf-score-filter",
        action="store_true",
        help="HTFスコアフィルターを有効化",
    )
    parser.add_argument(
        "--htf-score-threshold-add",
        type=float,
        default=1.0,
        help="HTF不一致時の追加閾値（デフォルト: 1.0）",
    )
    # --- ユニバーサル0.5R部分利確 ---
    parser.add_argument(
        "--universal-half-r",
        action="store_true",
        help="全レジームで0.5R部分利確を有効化",
    )
    parser.add_argument(
        "--universal-half-r-ratio",
        type=float,
        default=0.25,
        help="0.5R部分利確比率（デフォルト: 0.25）",
    )
    # --- アダプティブパラメータ調整 ---
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help="アダプティブパラメータ調整を有効化",
    )
    parser.add_argument(
        "--adaptive-window",
        type=int,
        default=30,
        help="アダプティブ調整ウィンドウサイズ（デフォルト: 30）",
    )
    parser.add_argument(
        "--adaptive-interval",
        type=int,
        default=5,
        help="アダプティブ再評価間隔（デフォルト: 5）",
    )
    # その他
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help=(
            "データ基底ディレクトリ（デフォルト: data）。"
            "通貨ペアサブディレクトリ（data/{symbol}/）が"
            "自動的に使用される。"
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="詳細出力",
    )
    # ファンダメンタルデータ
    parser.add_argument(
        "--fundamental",
        action="store_true",
        help=(
            "経済イベントCSVを自動読み込み。"
            "--fundamental-dir 配下の events_YYYY.csv を使用。"
        ),
    )
    parser.add_argument(
        "--fundamental-dir",
        type=str,
        default="data/fundamental",
        help="経済イベントCSVディレクトリ（デフォルト: data/fundamental）",
    )
    parser.add_argument(
        "--fundamental-guard",
        type=int,
        default=30,
        help="重要指標前の取引停止分数（デフォルト: 30）",
    )
    parser.add_argument(
        "--event-llm",
        action="store_true",
        help=(
            "イベントLLM分析CSVを自動読み込み。"
            "data/{symbol}/llm_events/ 配下の "
            "llm_events_SYMBOL_YYYY.csv を使用。"
        ),
    )
    parser.add_argument(
        "--news-llm",
        action="store_true",
        help=(
            "ニュースLLM分析CSVを自動読み込み。"
            "data/{symbol}/llm_news/ 配下の "
            "llm_news_SYMBOL_YYYY.csv を使用。"
        ),
    )
    # Phase 2b: ファンダメンタルロジック統合
    parser.add_argument(
        "--fundamental-phase2b",
        action="store_true",
        help=(
            "Phase 2b ファンダメンタル統合を有効化。"
            "--fundamental --event-llm --news-llm を暗黙有効化し"
            "assessor/softguard/pm を全てONにする。"
        ),
    )
    parser.add_argument(
        "--no-news-llm",
        action="store_true",
        help=(
            "ニュースLLMを無効化（--fundamental-phase2b 使用時"
            "のベースライン比較用）。"
        ),
    )
    parser.add_argument(
        "--fundamental-lag",
        type=int,
        default=30,
        help=(
            "LLM処理ラグ秒数（デフォルト: 30）。"
            "イベント発表後bias/surpriseが利用可能になるまでの遅延。"
        ),
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
    base_tf = "M1（1分毎判断）" if use_short_tf else "M15（15分毎判断）"

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
        table.add_row("評価時間足", "M1, M5, M15, M30, H1, H4, H8, D1")
        if args.start_date or args.end_date:
            _sy, _ey = parse_years(args.years)
            _s = args.start_date or f"{_sy}-01-01"
            _e = args.end_date or f"{_ey}-12-31"
            table.add_row("期間", f"{_s} ～ {_e}")
        else:
            table.add_row("期間", args.years)
        table.add_row("初期残高", f"JPY{args.initial_balance:,.0f}")
        table.add_row("ボリューム", str(args.volume))
        if args.sequential:
            table.add_row("実行モード", "[yellow]シーケンシャル[/yellow]")
        else:
            table.add_row("実行モード", "[green]年並列[/green]")
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
        print(f"評価時間足: M1, M5, M15, M30, H1, H4, H8, D1（マルチタイムフレーム）")
        if args.start_date or args.end_date:
            _sy, _ey = parse_years(args.years)
            _s = args.start_date or f"{_sy}-01-01"
            _e = args.end_date or f"{_ey}-12-31"
            print(f"期間: {_s} ～ {_e}")
        else:
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

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        n = len(yearly_results)
        profitable = sum(1 for r in yearly_results if r["net_profit"] > 0)

        table = Table(
            title=f"年別詳細（{n}年分）",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("年", style="bold", justify="right")
        table.add_column("取引数", justify="right")
        table.add_column("勝率", justify="right")
        table.add_column("非敗率", justify="right")
        table.add_column("PF", justify="right")
        table.add_column("利益", justify="right")
        table.add_column("DD", justify="right")

        for r in yearly_results:
            nlr = r.get("non_loss_rate", 0.0)
            profit = r["net_profit"]
            color = "green" if profit >= 0 else "red"
            table.add_row(
                str(r["year"]),
                str(r["trades"]),
                f"{r['win_rate']:.1f}%",
                f"{nlr:.1f}%",
                f"{r['profit_factor']:.2f}",
                f"[{color}]JPY{profit:+,.0f}[/{color}]",
                f"{r['max_drawdown']:.2f}%",
            )

        console.print()
        console.print(table)
        console.print(f"黒字年: {profitable}/{n}")

    except ImportError:
        print(f"\n{'=' * 80}")
        print(f"年別詳細（{len(yearly_results)}年分）")
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
    from autotrader.config.trading_params import get_preset as _get_preset

    from datetime import datetime as _dt, timedelta as _td

    start_year, end_year = parse_years(args.years)

    # 日単位の期間指定（--start-date/--end-date）
    period_start: _dt | None = None
    period_end: _dt | None = None
    if args.start_date:
        period_start = _dt.strptime(args.start_date, "%Y-%m-%d")
        start_year = period_start.year
    if args.end_date:
        _end_day = _dt.strptime(args.end_date, "%Y-%m-%d")
        # 終了日を含む（exclusive end = 翌日0時）
        period_end = _end_day + _td(days=1)
        end_year = _end_day.year

    # 短い時間足オプション（--no-short-tfが指定されていなければTrue）
    use_short_tf = not args.no_short_tf

    # シンボルプリセットからデフォルト値取得
    _preset = _get_preset(args.symbol)

    # commission: CLI明示指定 > preset値
    _commission = (
        args.commission if args.commission is not None
        else _preset.commission_per_lot
    )
    config = BacktestServiceConfig(
        start_year=start_year,
        end_year=end_year,
        initial_balance=args.initial_balance,
        volume=args.volume,
        data_dir=args.data_dir,
        symbol=args.symbol,
        timeframe=args.timeframe,
        max_positions=args.max_positions,
        spread_pips=_preset.spread_pips,
        slippage_pips=_preset.slippage_pips,
        verbose=False,
        use_short_timeframe=use_short_tf,
        enable_scalping=args.enable_scalping,
        commission_per_lot=_commission,
        use_session_spread=args.session_spread,
        bonus_max_positions=args.bonus_max_positions,
        bonus_score_threshold=args.bonus_score_threshold,
        pip_value=_preset.pip_value,
    )
    # spread/slippage上書き（--spread/--slippage の明示指定を優先）
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
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
        )

        console = Console()

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task(
                "読み込み中: H1", total=4
            )
            runner = service.create_runner()

            def _on_tf_loaded(tf: str, current: int, total: int) -> None:
                next_labels = {
                    "H1": "H4",
                    "H4": "D1",
                    "D1": "M15",
                    "M15": "完了",
                }
                next_tf = next_labels.get(tf, "完了")
                desc = (
                    f"読み込み中: {next_tf}"
                    if next_tf != "完了"
                    else "[green]データ読み込み完了[/green]"
                )
                progress.update(
                    task_id,
                    completed=current,
                    description=desc,
                )

            runner.load_data(on_tf_loaded=_on_tf_loaded)

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
    # --timeframes 解決チェーン:
    # CLI > SymbolPreset.timeframes > デフォルト8TF
    _tf_list = None
    if args.timeframes:
        _tf_list = [
            t.strip() for t in args.timeframes.split(",") if t.strip()
        ]

    bot_config = UnifiedBotConfig(
        range_day_bbw_threshold=args.range_day_bbw,
        range_day_score_premium=_score_prem,
        weak_hours_enabled=not args.no_weak_hours,
        weak_hours_score_premium=args.weak_hours_premium,
        use_dynamic_lot=not args.fixed_lot,
        max_lot_per_trade=args.max_lot_per_trade,
        max_total_exposure_lot=args.max_total_exposure,
        base_risk_pct=args.risk_pct,
        max_risk_pct_absolute=args.max_risk_pct_abs,
        equity_floor_pct=args.equity_floor,
        equity_caution_pct=args.equity_caution,
        slippage_buffer_pips=args.slippage_buffer,
        tp_sl_ratio=args.tp_sl_ratio,
        consensus_threshold=args.consensus_threshold,
        penalty_cap=args.penalty_cap,
        trend_strength_max=args.trend_strength_max,
        bonus_max_positions=args.bonus_max_positions,
        bonus_score_threshold=args.bonus_score_threshold,
        enable_position_sizing=not args.no_position_sizing,
        timeframes=_tf_list or [
            "M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"
        ],
        regime_threshold_enabled=args.regime_threshold,
        regime_trend_threshold_add=args.regime_trend_add,
        low_atr_trend_filter_enabled=args.low_atr_trend_filter,
        low_atr_trend_ratio_max=args.low_atr_trend_ratio,
        htf_score_filter_enabled=args.htf_score_filter,
        htf_score_filter_threshold_add=(
            args.htf_score_threshold_add
        ),
        fundamental_assessor_enabled=(
            args.fundamental_phase2b
        ),
        fundamental_softguard_enabled=(
            args.fundamental_phase2b
        ),
        fundamental_pm_enabled=(
            args.fundamental_phase2b
        ),
        fundamental_post_event_lag_seconds=(
            args.fundamental_lag
        ),
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
        range_day_half_r_partial_enabled=(
            not args.no_range_day_half_r_partial
        ),
        disable_tp_after_partial=not args.keep_tp_after_partial,
        partial_close_1r_ratio=args.partial_1r_ratio,
        partial_close_2r_ratio=args.partial_2r_ratio,
        breakeven_at_1r=not args.no_breakeven_1r,
        trailing_start_r=args.trailing_start_r,
        trailing_atr_multiplier=args.trailing_atr_mult,
        early_breakeven_r=args.early_be_r,
        early_breakeven_enabled=not args.no_early_be,
        signal_rev_close_ratio=args.signal_rev_ratio,
        range_day_half_r_partial_ratio=args.half_r_ratio,
        range_day_half_r_trigger=args.half_r_trigger,
        time_exit_enabled=not args.no_time_exit,
        consensus_exit_enabled=args.consensus_exit,
        consensus_exit_threshold=args.consensus_exit_threshold,
        consensus_exit_own_max=args.consensus_exit_own_max,
        consensus_exit_loss_only=args.consensus_exit_loss_only,
        profit_reversal_enabled=args.profit_reversal,
        profit_reversal_mfe_r=args.profit_reversal_mfe_r,
        profit_reversal_drop_r=args.profit_reversal_drop_r,
        profit_reversal_max_r=args.profit_reversal_max_r,
        progressive_stagnation_enabled=args.progressive_stagnation,
        universal_half_r_enabled=args.universal_half_r,
        universal_half_r_ratio=args.universal_half_r_ratio,
    )

    # ファンダメンタルCSVリスト構築
    # 検索順: data/fundamental/events/ → data/fundamental/
    _fundamental_csvs: list[str] | None = None
    if args.fundamental:
        _fund_dir = Path(args.fundamental_dir)
        _events_dir = _fund_dir / "events"
        if not _events_dir.exists():
            _events_dir = _fund_dir
        _fundamental_csvs = []
        for _yr in range(start_year, end_year + 1):
            _csv = _events_dir / f"events_{_yr}.csv"
            if _csv.exists():
                _fundamental_csvs.append(str(_csv))
        if not _fundamental_csvs:
            logging.warning(
                "[Fundamental] %s に events_YYYY.csv が見つかりません",
                _events_dir,
            )
            _fundamental_csvs = None

    # Phase 2b: --fundamental-phase2b で暗黙的に有効化
    if args.fundamental_phase2b:
        args.fundamental = True
        args.event_llm = True
        if not args.no_news_llm:
            args.news_llm = True

    # イベントLLM CSVリスト構築
    # 検索順: data/{symbol}/llm_events/ → data/fundamental/
    _event_llm_csvs: list[str] | None = None
    if args.event_llm:
        _sym = args.symbol
        _data_base = Path(args.data_dir)
        # 新構造: data/{symbol}/llm_events/
        _llm_dir = _data_base / _sym / "llm_events"
        if not _llm_dir.exists():
            # フォールバック: data/fundamental/
            _llm_dir = Path(args.fundamental_dir)
        _event_llm_csvs = []
        for _yr in range(start_year, end_year + 1):
            _csv = _llm_dir / f"llm_events_{_sym}_{_yr}.csv"
            if _csv.exists():
                _event_llm_csvs.append(str(_csv))
        if not _event_llm_csvs:
            logging.warning(
                "[EventLLM] %s に llm_events_%s_YYYY.csv "
                "が見つかりません",
                _llm_dir, _sym,
            )
            _event_llm_csvs = None
        else:
            logging.info(
                "[EventLLM] %d年分のCSV検出: %s",
                len(_event_llm_csvs), _llm_dir,
            )

    # ニュースLLM CSVリスト構築
    # 検索順: data/{symbol}/llm_news/
    _news_llm_csvs: list[str] | None = None
    if args.news_llm:
        _sym = args.symbol
        _data_base = Path(args.data_dir)
        _news_dir = _data_base / _sym / "llm_news"
        if _news_dir.exists():
            _news_llm_csvs = []
            for _yr in range(start_year, end_year + 1):
                _csv = (
                    _news_dir
                    / f"llm_news_{_sym}_{_yr}.csv"
                )
                if _csv.exists():
                    _news_llm_csvs.append(str(_csv))
            if not _news_llm_csvs:
                logging.warning(
                    "[NewsLLM] %s に llm_news_%s_YYYY.csv "
                    "が見つかりません",
                    _news_dir, _sym,
                )
                _news_llm_csvs = None
            else:
                logging.info(
                    "[NewsLLM] %d年分のCSV検出: %s",
                    len(_news_llm_csvs), _news_dir,
                )
        else:
            logging.warning(
                "[NewsLLM] ディレクトリ未存在: %s",
                _news_dir,
            )

    # アダプティブパラメータ調整設定
    _adaptive_config = None
    if args.adaptive:
        from autotrader.decision.unified.adaptive import TunerConfig
        _adaptive_config = TunerConfig(
            window_size=args.adaptive_window,
            eval_interval=args.adaptive_interval,
        )
        logging.info(
            "[Adaptive] 有効: window=%d, interval=%d",
            args.adaptive_window, args.adaptive_interval,
        )

    result = runner.run_unified(
        start_year,
        end_year,
        bot_config,
        use_m1=use_short_tf,
        enable_scalping=args.enable_scalping,
        pm_config=pm_config,
        period_start=period_start,
        period_end=period_end,
        sequential=args.sequential,
        fundamental_csv_list=_fundamental_csvs,
        event_llm_csv_list=_event_llm_csvs,
        news_llm_csv_list=_news_llm_csvs,
        fundamental_guard_minutes=args.fundamental_guard,
        max_year_workers=args.max_year_workers,
        adaptive_config=_adaptive_config,
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

    # 通貨ペア別サブディレクトリに解決
    data_dir = Path(args.data_dir) / args.symbol
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
    from autotrader.config.trading_params import get_preset as _get_preset
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
    # 通貨ペア別サブディレクトリに解決
    data_dir = Path(args.data_dir) / args.symbol

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
    _preset = _get_preset(args.symbol)
    sim_config = SimulatorConfig(
        initial_balance=initial_balance,
        spread_pips=_preset.spread_pips,
        pip_value=_preset.pip_value,
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
        elif args.fast:
            logging.warning(
                "[非推奨] --fast は廃止予定です。"
                "通常モードで並列化済みのため、--fast なしで実行してください。"
            )
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
