"""6 JPYペア ポートフォリオ検証スクリプト

現行プリセットで6ペア同時稼働の真のポートフォリオ性能を検証し、
品質フィルタ + リスク調整で年間60%・DD<5%の設定を特定する。

使い方:
    python scripts/run_portfolio_backtest.py --data-dir data
    python scripts/run_portfolio_backtest.py --data-dir data --steps baseline
    python scripts/run_portfolio_backtest.py --data-dir data --steps all
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

# プロジェクトルートをパスに追加
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from autotrader.backtest.runner import BacktestResult  # noqa: E402
from autotrader.backtest.service import (  # noqa: E402
    BacktestService,
    BacktestServiceConfig,
)
from autotrader.config.trading_params import get_pip_unit, get_preset, get_quote_ccy_rate  # noqa: E402
from autotrader.decision.unified import UnifiedBotConfig  # noqa: E402

# --- 定数 ---
SYMBOLS = ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"]
START_YEAR = 2020
END_YEAR = 2025
INITIAL_BALANCE = 1_000_000.0
TARGET_ANNUAL_RETURN = 60.0  # %

logger = logging.getLogger(__name__)


# --- データクラス ---
@dataclass
class PairResult:
    """個別ペアのバックテスト結果"""

    symbol: str
    result: BacktestResult
    bot_config: UnifiedBotConfig
    monthly_pnl: dict[tuple[int, int], float] = field(
        default_factory=dict,
    )


@dataclass
class PortfolioMetrics:
    """ポートフォリオ集約メトリクス"""

    test_name: str
    pair_results: list[PairResult]
    total_profit: float = 0.0
    annual_return_pct: float = 0.0
    max_dd_pct: float = 0.0
    sharpe_ratio: float = 0.0
    portfolio_wr: float = 0.0
    portfolio_pf: float = 0.0
    monthly_win_rate: float = 0.0
    num_years: int = 6
    correlation_matrix: dict[str, dict[str, float]] = field(
        default_factory=dict,
    )


# --- YAML signal設定読み込み ---
def load_signal_overrides(
    symbol: str,
    preset_path: Path | None = None,
) -> dict[str, Any]:
    """symbol_presets.yaml からsignal設定を読み込み

    プリセットYAMLのトップレベルsignal（デフォルト）と
    symbols[symbol].signal（ペア固有）をマージして返す。

    Args:
        symbol: 通貨ペア名
        preset_path: YAMLパス

    Returns:
        dict: signal設定（bca_min_edge等）
    """
    path = preset_path or (_PROJECT_ROOT / "config" / "symbol_presets.yaml")
    if not path.exists():
        return {}

    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    # トップレベルsignal（全ペア共通デフォルト）
    defaults = dict(raw.get("signal", {}))

    # ペア固有signal
    symbols = raw.get("symbols", {})
    sym_data = symbols.get(symbol, {})
    if isinstance(sym_data, dict):
        sym_signal = sym_data.get("signal", {})
        if sym_signal:
            defaults.update(sym_signal)

    return defaults


def build_bot_config(
    symbol: str,
    extra_overrides: dict[str, Any] | None = None,
) -> UnifiedBotConfig:
    """プリセット + signal設定からUnifiedBotConfigを構築

    Args:
        symbol: 通貨ペア名
        extra_overrides: 追加オーバーライド

    Returns:
        UnifiedBotConfig: 構築済み設定
    """
    preset = get_preset(symbol)
    signal = load_signal_overrides(symbol)

    # UnifiedBotConfigの有効フィールドのみ抽出
    valid_fields = {f.name for f in dataclasses.fields(UnifiedBotConfig)}

    overrides: dict[str, Any] = {}

    # プリセット値（位置管理・リスク管理）
    _pip_unit = get_pip_unit(symbol)
    _qcr = get_quote_ccy_rate(symbol)
    overrides.update(
        {
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": preset.max_total_exposure_lot,
            "equity_floor_pct": preset.equity_floor_pct,
            "pip_unit": _pip_unit,
            "quote_ccy_rate": _qcr,
        }
    )

    # signal設定（bca_min_edge, consensus_threshold等）
    for k, v in signal.items():
        if k in valid_fields:
            overrides[k] = v

    # 追加オーバーライド
    if extra_overrides:
        overrides.update(extra_overrides)

    return UnifiedBotConfig(**overrides)


def run_single_pair(
    symbol: str,
    data_dir: str,
    bot_config: UnifiedBotConfig,
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
) -> PairResult:
    """単一ペアのバックテスト実行

    Args:
        symbol: 通貨ペア名
        data_dir: データディレクトリ
        bot_config: ボット設定
        start_year: 開始年
        end_year: 終了年

    Returns:
        PairResult: ペア結果
    """
    preset = get_preset(symbol)

    svc_config = BacktestServiceConfig(
        start_year=start_year,
        end_year=end_year,
        initial_balance=INITIAL_BALANCE,
        data_dir=data_dir,
        symbol=symbol,
        spread_pips=preset.spread_pips,
        slippage_pips=preset.slippage_pips,
        bonus_max_positions=bot_config.bonus_max_positions,
        bonus_score_threshold=bot_config.bonus_score_threshold,
        pip_value=preset.pip_value,
        commission_per_lot=preset.commission_per_lot,
    )

    service = BacktestService(svc_config)
    runner = service.create_runner()
    runner.load_data()

    result = runner.run_unified(
        start_year,
        end_year,
        bot_config,
        sequential=False,
        max_year_workers=3,
    )

    # 月次PnL辞書を構築
    monthly_pnl: dict[tuple[int, int], float] = {}
    for m in result.monthly_results:
        key = (m["year"], m["month"])
        monthly_pnl[key] = m["pnl"]

    return PairResult(
        symbol=symbol,
        result=result,
        bot_config=bot_config,
        monthly_pnl=monthly_pnl,
    )


# --- ポートフォリオ集約 ---
def aggregate_portfolio(
    test_name: str,
    pair_results: list[PairResult],
    num_years: int = 6,
) -> PortfolioMetrics:
    """ペア結果をポートフォリオに集約

    Args:
        test_name: テスト名
        pair_results: ペア結果リスト
        num_years: 検証年数

    Returns:
        PortfolioMetrics: ポートフォリオメトリクス
    """
    # 全月を収集
    all_months: set[tuple[int, int]] = set()
    for pr in pair_results:
        all_months.update(pr.monthly_pnl.keys())

    sorted_months = sorted(all_months)

    # 月次PnL合算
    portfolio_monthly: list[float] = []
    for ym in sorted_months:
        total = sum(pr.monthly_pnl.get(ym, 0.0) for pr in pair_results)
        portfolio_monthly.append(total)

    # 総利益
    total_profit = sum(portfolio_monthly)

    # 年間収益率（初期残高に対する%）
    annual_return = (
        (total_profit / INITIAL_BALANCE) / num_years * 100
        if num_years > 0
        else 0.0
    )

    # ポートフォリオDD
    # 方法1: 月次equity curveからのDD
    equity = INITIAL_BALANCE
    peak = equity
    max_dd_monthly = 0.0
    for pnl in portfolio_monthly:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd_monthly:
            max_dd_monthly = dd

    # 方法2: 各ペアの個別DDの最大値（より保守的）
    max_dd_pair = max(
        (pr.result.max_drawdown for pr in pair_results),
        default=0.0,
    )
    # 両方の大きい方を採用（保守的推定）
    max_dd = max(max_dd_monthly, max_dd_pair)

    # Sharpe比（月次→年換算）
    if len(portfolio_monthly) > 1:
        monthly_arr = np.array(portfolio_monthly)
        mean_m = np.mean(monthly_arr)
        std_m = np.std(monthly_arr, ddof=1)
        sharpe = (mean_m / std_m) * math.sqrt(12) if std_m > 0 else 0.0
    else:
        sharpe = 0.0

    # ポートフォリオWR/PF（全ペアのトレードを合算）
    total_trades = sum(pr.result.trades for pr in pair_results)
    total_wins = sum(
        round(pr.result.win_rate / 100 * pr.result.trades)
        for pr in pair_results
    )
    portfolio_wr = total_wins / total_trades * 100 if total_trades > 0 else 0.0

    # PF: 各ペアのnet_profitから逆算（PF = GP/GL, NP = GP-GL）
    # GP = NP * PF/(PF-1), GL = NP/(PF-1) （PF>1の場合）
    total_gp = 0.0
    total_gl = 0.0
    for pr in pair_results:
        pf = pr.result.profit_factor
        np_ = pr.result.net_profit
        if pf > 1.0 and np_ > 0:
            gl = np_ / (pf - 1)
            gp = np_ + gl
            total_gp += gp
            total_gl += gl
        elif np_ <= 0:
            total_gl += abs(np_)
        else:
            total_gp += np_
    portfolio_pf = total_gp / total_gl if total_gl > 0 else float("inf")

    # 月間勝率
    winning_months = sum(1 for p in portfolio_monthly if p > 0)
    monthly_wr = (
        winning_months / len(portfolio_monthly) * 100
        if portfolio_monthly
        else 0.0
    )

    # 相関マトリクス
    corr_matrix = compute_correlation(pair_results, sorted_months)

    return PortfolioMetrics(
        test_name=test_name,
        pair_results=pair_results,
        total_profit=total_profit,
        annual_return_pct=annual_return,
        max_dd_pct=max_dd,
        sharpe_ratio=sharpe,
        portfolio_wr=portfolio_wr,
        portfolio_pf=portfolio_pf,
        monthly_win_rate=monthly_wr,
        num_years=num_years,
        correlation_matrix=corr_matrix,
    )


def compute_correlation(
    pair_results: list[PairResult],
    sorted_months: list[tuple[int, int]],
) -> dict[str, dict[str, float]]:
    """ペア間月次PnL相関を計算

    Args:
        pair_results: ペア結果リスト
        sorted_months: ソート済み月リスト

    Returns:
        dict: 相関マトリクス
    """
    if len(pair_results) < 2:
        return {}

    # 各ペアの月次PnL配列
    arrays: dict[str, np.ndarray] = {}
    for pr in pair_results:
        arr = np.array([pr.monthly_pnl.get(ym, 0.0) for ym in sorted_months])
        arrays[pr.symbol] = arr

    symbols = list(arrays.keys())
    corr: dict[str, dict[str, float]] = {}
    for i, s1 in enumerate(symbols):
        corr[s1] = {}
        for j, s2 in enumerate(symbols):
            if i == j:
                corr[s1][s2] = 1.0
            else:
                c = np.corrcoef(arrays[s1], arrays[s2])[0, 1]
                corr[s1][s2] = round(float(c), 3)

    return corr


# --- テスト実行 ---
def run_test(
    test_name: str,
    data_dir: str,
    available_symbols: list[str],
    bot_overrides: dict[str, dict[str, Any]] | None = None,
    global_overrides: dict[str, Any] | None = None,
) -> PortfolioMetrics:
    """テストケースを実行

    Args:
        test_name: テスト名
        data_dir: データディレクトリ
        available_symbols: 利用可能シンボルリスト
        bot_overrides: ペア別オーバーライド
        global_overrides: 全ペア共通オーバーライド

    Returns:
        PortfolioMetrics: ポートフォリオメトリクス
    """
    _print_header(f"テスト: {test_name}")

    pair_results: list[PairResult] = []
    for symbol in available_symbols:
        per_pair = {}
        if bot_overrides and symbol in bot_overrides:
            per_pair.update(bot_overrides[symbol])
        if global_overrides:
            per_pair.update(global_overrides)

        bot_config = build_bot_config(symbol, per_pair or None)

        _t0 = time.time()
        pr = run_single_pair(symbol, data_dir, bot_config)
        elapsed = time.time() - _t0

        _print_pair_summary(pr, elapsed)
        pair_results.append(pr)

    metrics = aggregate_portfolio(test_name, pair_results)
    _print_portfolio_summary(metrics)
    return metrics


def find_available_symbols(data_dir: str) -> list[str]:
    """データが存在するシンボルを検出

    Args:
        data_dir: データディレクトリ

    Returns:
        list[str]: 利用可能なシンボルリスト
    """
    available = []
    base = Path(data_dir)
    for sym in SYMBOLS:
        sym_dir = base / sym
        if sym_dir.exists() and any(sym_dir.iterdir()):
            available.append(sym)
    return available


# --- 出力ヘルパー ---
def _print_header(title: str) -> None:
    """セクションヘッダー出力"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_pair_summary(pr: PairResult, elapsed: float) -> None:
    """ペア結果サマリー出力"""
    r = pr.result
    print(
        f"  {pr.symbol:8s} | "
        f"Profit: {r.net_profit:>+10,.0f} | "
        f"WR: {r.win_rate:5.1f}% | "
        f"PF: {r.profit_factor:5.2f} | "
        f"DD: {r.max_drawdown:5.2f}% | "
        f"Sharpe: {r.sharpe_ratio:5.2f} | "
        f"Trades: {r.trades:4d} | "
        f"{elapsed:.0f}s"
    )


def _print_portfolio_summary(m: PortfolioMetrics) -> None:
    """ポートフォリオメトリクスサマリー出力"""
    print(f"\n  --- {m.test_name} ポートフォリオ集約 ---")
    print(f"  総利益:        {m.total_profit:>+12,.0f}")
    print(f"  年間収益率:    {m.annual_return_pct:>8.1f}%")
    print(f"  ポートフォリオDD: {m.max_dd_pct:>6.2f}%")
    print(f"  Sharpe比:      {m.sharpe_ratio:>8.2f}")
    print(f"  WR(全トレード): {m.portfolio_wr:>6.1f}%")
    print(f"  PF(全トレード): {m.portfolio_pf:>6.2f}")
    print(f"  月間勝率:      {m.monthly_win_rate:>6.1f}%")

    if m.correlation_matrix:
        print("\n  ペア間相関マトリクス:")
        syms = list(m.correlation_matrix.keys())
        header = "         " + "  ".join(f"{s:>7s}" for s in syms)
        print(f"  {header}")
        for s1 in syms:
            row = "  ".join(
                f"{m.correlation_matrix[s1].get(s2, 0):>7.3f}" for s2 in syms
            )
            print(f"  {s1:8s} {row}")


# --- レポート生成 ---
def generate_report(
    results: dict[str, PortfolioMetrics],
    available_symbols: list[str],
    output_path: Path,
) -> None:
    """Markdownレポートを生成

    Args:
        results: テスト名→メトリクスの辞書
        available_symbols: 検証に使用したシンボル
        output_path: 出力パス
    """
    lines: list[str] = []
    lines.append("# 6 JPYペア ポートフォリオ検証レポート\n")
    lines.append(
        f"検証期間: {START_YEAR}-{END_YEAR} ({END_YEAR - START_YEAR + 1}年)\n"
    )
    lines.append(f"対象ペア: {', '.join(available_symbols)}\n")
    lines.append(f"初期残高: {INITIAL_BALANCE:,.0f}\n")
    lines.append("")

    # サマリーテーブル
    lines.append("## テスト結果サマリー\n")
    lines.append(
        "| テスト | 総利益 | 年間収益率 | DD | Sharpe | WR | PF | 月間勝率 |"
    )
    lines.append(
        "|--------|--------|-----------|-----|--------|-----|-----|---------|"
    )
    for name, m in results.items():
        lines.append(
            f"| {name} | "
            f"{m.total_profit:+,.0f} | "
            f"{m.annual_return_pct:.1f}% | "
            f"{m.max_dd_pct:.2f}% | "
            f"{m.sharpe_ratio:.2f} | "
            f"{m.portfolio_wr:.1f}% | "
            f"{m.portfolio_pf:.2f} | "
            f"{m.monthly_win_rate:.1f}% |"
        )
    lines.append("")

    # 個別ペア詳細
    for name, m in results.items():
        lines.append(f"## {name} 詳細\n")
        lines.append(
            "| ペア | 利益 | WR | PF | DD | Sharpe | Trades | risk% | pos |"
        )
        lines.append(
            "|------|------|-----|-----|-----|--------|--------|-------|-----|"
        )
        for pr in m.pair_results:
            r = pr.result
            bc = pr.bot_config
            lines.append(
                f"| {pr.symbol} | "
                f"{r.net_profit:+,.0f} | "
                f"{r.win_rate:.1f}% | "
                f"{r.profit_factor:.2f} | "
                f"{r.max_drawdown:.2f}% | "
                f"{r.sharpe_ratio:.2f} | "
                f"{r.trades} | "
                f"{bc.base_risk_pct:.3f} | "
                f"{bc.max_positions} |"
            )
        lines.append("")

    # 相関マトリクス（最初のテストから）
    first = next(iter(results.values()), None)
    if first and first.correlation_matrix:
        lines.append("## ペア間相関マトリクス (Baseline)\n")
        syms = list(first.correlation_matrix.keys())
        lines.append("| | " + " | ".join(syms) + " |")
        lines.append("|---|" + "|".join(["---"] * len(syms)) + "|")
        for s1 in syms:
            row = " | ".join(
                f"{first.correlation_matrix[s1].get(s2, 0):.3f}" for s2 in syms
            )
            lines.append(f"| {s1} | {row} |")
        lines.append("")

    # 推奨設定
    lines.append("## 推奨設定\n")

    # 最良テストを選定
    best_name = ""
    best_score = -999.0
    for name, m in results.items():
        # 年間60%±10%かつDD<5%のテストからSharpe最大を選択
        if m.max_dd_pct < 5.0 and m.annual_return_pct > 0:
            # 60%ターゲットとの距離ペナルティ
            dist = abs(m.annual_return_pct - TARGET_ANNUAL_RETURN)
            score = m.sharpe_ratio - dist * 0.01
            if score > best_score:
                best_score = score
                best_name = name

    if best_name:
        bm = results[best_name]
        lines.append(f"推奨テスト: **{best_name}**\n")
        lines.append(f"- 年間収益率: {bm.annual_return_pct:.1f}%")
        lines.append(f"- ポートフォリオDD: {bm.max_dd_pct:.2f}%")
        lines.append(f"- Sharpe: {bm.sharpe_ratio:.2f}")
        lines.append(f"- WR: {bm.portfolio_wr:.1f}%")
        lines.append(f"- PF: {bm.portfolio_pf:.2f}")
        lines.append(f"- 月間勝率: {bm.monthly_win_rate:.1f}%")
    else:
        lines.append("条件を満たすテストなし。リスク調整が必要。\n")

    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nレポート出力: {output_path}")


# --- メイン実行ロジック ---
def main() -> None:
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description="6 JPYペア ポートフォリオ検証",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="データディレクトリ",
    )
    parser.add_argument(
        "--steps",
        default="all",
        choices=["baseline", "quality", "risk", "all"],
        help="実行ステップ（default: all）",
    )
    parser.add_argument(
        "--output",
        default="reports/portfolio_6jpy_verification.md",
        help="レポート出力先",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        help="対象シンボル（カンマ区切り、省略時は自動検出）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    # 利用可能シンボル検出
    if args.symbols:
        available = [s.strip() for s in args.symbols.split(",")]
    else:
        available = find_available_symbols(args.data_dir)

    if not available:
        print(
            f"エラー: {args.data_dir} にデータが見つかりません。"
            f"\n対象: {', '.join(SYMBOLS)}"
        )
        sys.exit(1)

    print(f"検出ペア: {', '.join(available)}")
    missing = set(SYMBOLS) - set(available)
    if missing:
        print(f"データなし（スキップ）: {', '.join(sorted(missing))}")

    results: dict[str, PortfolioMetrics] = {}
    run_all = args.steps == "all"

    # === Step 1: Baseline ===
    if run_all or args.steps == "baseline":
        results["T0_Baseline"] = run_test(
            "T0_Baseline",
            args.data_dir,
            available,
        )

    # === Step 2: 品質フィルタ ===
    if run_all or args.steps == "quality":
        # Q1: consensus_threshold=10.0（軽い品質フィルタ）
        results["Q1_CT10"] = run_test(
            "Q1_CT10",
            args.data_dir,
            available,
            global_overrides={"consensus_threshold": 10.0},
        )

        # Q2: consensus_threshold=11.0（強い品質フィルタ）
        results["Q2_CT11"] = run_test(
            "Q2_CT11",
            args.data_dir,
            available,
            global_overrides={"consensus_threshold": 11.0},
        )

        # Q3: consensus_threshold=10.0 + 全ペアbca_min_edge=0.65
        results["Q3_CT10_BCA65"] = run_test(
            "Q3_CT10_BCA65",
            args.data_dir,
            available,
            global_overrides={
                "consensus_threshold": 10.0,
                "bca_min_edge": 0.65,
            },
        )

    # === Step 3: リスク調整 ===
    if run_all or args.steps == "risk":
        # Baselineが無い場合は実行
        if "T0_Baseline" not in results:
            results["T0_Baseline"] = run_test(
                "T0_Baseline",
                args.data_dir,
                available,
            )

        # 品質テスト結果から最良を選定
        quality_tests = {k: v for k, v in results.items() if k.startswith("Q")}
        if not quality_tests:
            # 品質テストを実行
            results["Q1_CT10"] = run_test(
                "Q1_CT10",
                args.data_dir,
                available,
                global_overrides={"consensus_threshold": 10.0},
            )
            quality_tests = {"Q1_CT10": results["Q1_CT10"]}

        # 最良品質テストを選定（PF * WR でスコアリング）
        best_q_name = ""
        best_q_score = -1.0
        for name, m in quality_tests.items():
            score = m.portfolio_pf * m.portfolio_wr / 100
            if score > best_q_score:
                best_q_score = score
                best_q_name = name

        best_q = results.get(best_q_name)
        baseline = results["T0_Baseline"]

        # リスク調整: 目標60%に対する倍率計算
        # 最良品質テストのベースで調整
        source = best_q if best_q else baseline
        source_name = best_q_name if best_q else "T0_Baseline"

        if source and source.annual_return_pct > 0:
            ratio = TARGET_ANNUAL_RETURN / source.annual_return_pct
            print(
                f"\nリスク調整元: {source_name} "
                f"(年間{source.annual_return_pct:.1f}%)"
            )
            print(f"調整倍率: {ratio:.3f}")

            # 各ペアのbase_risk_pctを比例縮小
            risk_overrides: dict[str, dict[str, Any]] = {}
            # ソースのbot_configからoverridesを再現
            source_global: dict[str, Any] = {}
            if best_q_name == "Q1_CT10":
                source_global = {"consensus_threshold": 10.0}
            elif best_q_name == "Q2_CT11":
                source_global = {"consensus_threshold": 11.0}
            elif best_q_name == "Q3_CT10_BCA65":
                source_global = {
                    "consensus_threshold": 10.0,
                    "bca_min_edge": 0.65,
                }

            for pr in source.pair_results:
                original_risk = pr.bot_config.base_risk_pct
                adjusted_risk = round(original_risk * ratio, 4)
                # max_lot_per_tradeも比例縮小
                original_max_lot = pr.bot_config.max_lot_per_trade
                adjusted_max_lot = round(
                    original_max_lot * ratio,
                    2,
                )
                original_exposure = pr.bot_config.max_total_exposure_lot
                adjusted_exposure = round(
                    original_exposure * ratio,
                    2,
                )
                risk_overrides[pr.symbol] = {
                    "base_risk_pct": adjusted_risk,
                    "max_lot_per_trade": adjusted_max_lot,
                    "max_total_exposure_lot": adjusted_exposure,
                }

            results[f"R1_{source_name}_adj"] = run_test(
                f"R1_{source_name}_adj",
                args.data_dir,
                available,
                bot_overrides=risk_overrides,
                global_overrides=source_global or None,
            )

            # Baselineベースのリスク調整も実行
            if source_name != "T0_Baseline":
                ratio_bl = TARGET_ANNUAL_RETURN / baseline.annual_return_pct
                risk_overrides_bl: dict[str, dict[str, Any]] = {}
                for pr in baseline.pair_results:
                    orig_risk = pr.bot_config.base_risk_pct
                    adj_risk = round(orig_risk * ratio_bl, 4)
                    orig_lot = pr.bot_config.max_lot_per_trade
                    adj_lot = round(orig_lot * ratio_bl, 2)
                    orig_exp = pr.bot_config.max_total_exposure_lot
                    adj_exp = round(orig_exp * ratio_bl, 2)
                    risk_overrides_bl[pr.symbol] = {
                        "base_risk_pct": adj_risk,
                        "max_lot_per_trade": adj_lot,
                        "max_total_exposure_lot": adj_exp,
                    }

                results["R2_Baseline_adj"] = run_test(
                    "R2_Baseline_adj",
                    args.data_dir,
                    available,
                    bot_overrides=risk_overrides_bl,
                )

    # === レポート生成 ===
    output_path = Path(args.output)
    generate_report(results, available, output_path)

    # 最終サマリー
    _print_header("最終サマリー")
    print(
        f"{'テスト':20s} | {'年間収益率':>10s} | {'DD':>6s} | "
        f"{'Sharpe':>7s} | {'WR':>6s} | {'PF':>6s} | {'月間+':>6s}"
    )
    print("-" * 80)
    for name, m in results.items():
        print(
            f"{name:20s} | "
            f"{m.annual_return_pct:>9.1f}% | "
            f"{m.max_dd_pct:>5.2f}% | "
            f"{m.sharpe_ratio:>7.2f} | "
            f"{m.portfolio_wr:>5.1f}% | "
            f"{m.portfolio_pf:>5.2f} | "
            f"{m.monthly_win_rate:>5.1f}%"
        )


if __name__ == "__main__":
    main()
