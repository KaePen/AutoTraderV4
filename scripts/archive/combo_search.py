#!/usr/bin/env python3
"""パラメータ組み合わせ探索スクリプト

データを1回だけ読み込み、複数のパラメータ組み合わせを
効率的にテストして最良の設定を見つける。

使用例:
    uv run python scripts/combo_search.py --years 2023-2025
    uv run python scripts/combo_search.py --years 2023-2025 --phase 2
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


@dataclass
class ComboResult:
    """組み合わせ結果"""

    name: str
    params: dict
    trades: int
    win_rate: float
    non_loss_rate: float
    pf: float
    net_profit: float
    max_dd: float
    annual_return: float
    sharpe: float
    monthly_positive_rate: float


def run_combo(
    runner,
    name: str,
    start_year: int,
    end_year: int,
    bot_kwargs: dict,
    pm_kwargs: dict,
) -> ComboResult:
    """1つの組み合わせを実行"""
    from autotrader.decision.unified import UnifiedBotConfig
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )

    bot_config = UnifiedBotConfig(
        timeframes=["M15", "H1", "H4", "D1"],
        **bot_kwargs,
    )
    pm_config = PositionManagerConfig(**pm_kwargs)

    result = runner.run_unified(
        start_year,
        end_year,
        bot_config,
        use_m1=True,
        pm_config=pm_config,
    )

    # 月間プラス率
    monthly = result.monthly_results or []
    total_m = len(monthly)
    pos_m = sum(1 for r in monthly if r["return_pct"] > 0)
    monthly_pct = (pos_m / total_m * 100) if total_m > 0 else 0.0

    return ComboResult(
        name=name,
        params={**bot_kwargs, **pm_kwargs},
        trades=result.trades,
        win_rate=result.win_rate,
        non_loss_rate=getattr(result, "non_loss_rate", 0.0),
        pf=result.profit_factor,
        net_profit=result.net_profit,
        max_dd=result.max_drawdown,
        annual_return=result.annual_return,
        sharpe=result.sharpe_ratio,
        monthly_positive_rate=monthly_pct,
    )


# デフォルト値
DEFAULT_BOT = {
    "range_day_bbw_threshold": 0.20,
    "range_day_score_premium": 0.0,
    "weak_hours_enabled": True,
    "weak_hours_score_premium": 0.5,
}
DEFAULT_PM = {
    "stagnation_exit_minutes": 120.0,
    "stagnation_min_mfe_r": 0.2,
    "range_day_be_disabled": True,
    "range_day_early_be_r": 1.0,
    "range_day_fast_be_enabled": True,
    "range_day_fast_be_minutes": 90.0,
    "range_day_stagnation_enabled": True,
    "range_day_stagnation_stage1_minutes": 45.0,
    "range_day_stagnation_stage1_min_mfe_r": 0.05,
    "range_day_stagnation_stage2_minutes": 60.0,
    "range_day_stagnation_stage2_min_mfe_r": 0.10,
    "range_day_insurance_enabled": True,
    "range_day_insurance_max_minutes": 30.0,
    "range_day_insurance_sl_offset_r": -0.1,
    "range_day_insurance_partial_ratio": 0.20,
    "insurance_trigger_r": 0.7,
    "insurance_block_high_mfe_r": 0.8,
    "insurance_min_holding_minutes": 15.0,
}


def get_phase1_combos():
    """フェーズ1: 高インパクト個別パラメータ（20パターン）"""
    combos = []

    # ベースライン
    combos.append(("BASELINE", {}, {}))

    # --- 入口フィルター（6パターン） ---
    combos.append(("bbw=0.16", {"range_day_bbw_threshold": 0.16}, {}))
    combos.append(("bbw=0.25", {"range_day_bbw_threshold": 0.25}, {}))
    combos.append(("score_prem=0.5", {"range_day_score_premium": 0.5}, {}))
    combos.append(("no_weak_hours", {"weak_hours_enabled": False}, {}))
    combos.append(("wh_prem=0.3", {"weak_hours_score_premium": 0.3}, {}))
    combos.append(("wh_prem=1.0", {"weak_hours_score_premium": 1.0}, {}))

    # --- PM出口系（13パターン） ---
    combos.append(("stag_min=90", {}, {"stagnation_exit_minutes": 90.0}))
    combos.append(("stag_min=150", {}, {"stagnation_exit_minutes": 150.0}))
    combos.append(("stag_mfe=0.15", {}, {"stagnation_min_mfe_r": 0.15}))
    combos.append(("stag_mfe=0.30", {}, {"stagnation_min_mfe_r": 0.30}))
    combos.append(("be_r=0.5", {}, {"range_day_early_be_r": 0.5}))
    combos.append(("be_r=1.5", {}, {"range_day_early_be_r": 1.5}))
    combos.append(("no_fast_be", {}, {"range_day_fast_be_enabled": False}))
    combos.append(("no_range_stag", {}, {"range_day_stagnation_enabled": False}))
    combos.append(("no_insurance", {}, {"range_day_insurance_enabled": False}))
    combos.append(("ins_trigger=0.5", {}, {"insurance_trigger_r": 0.5}))
    combos.append(("ins_trigger=1.0", {}, {"insurance_trigger_r": 1.0}))
    combos.append(("ins_block=999", {}, {"insurance_block_high_mfe_r": 999.0}))
    combos.append(("ins_hold=30", {}, {"insurance_min_holding_minutes": 30.0}))

    return combos


def get_phase2_combos():
    """フェーズ2: フェーズ1上位パラメータの組み合わせ

    フェーズ1結果:
      score_prem=0.5: PF+0.30, DD-0.18% (圧倒的1位)
      ins_trigger=1.0: DD-0.25% (DD最小)
      no_range_stag:   PF+0.05, DD-0.02%, M+100%
      stag_mfe=0.15:   PF+0.05, P+19k
      be_r=0.5:        PF+0.04
    """
    combos = []
    combos.append(("BASELINE", {}, {}))

    # 1位: score_prem=0.5 を軸に
    sp = {"range_day_score_premium": 0.5}
    combos.append(("SP05", sp, {}))

    # SP + ins_trigger=1.0
    combos.append(("SP05+IT10", sp, {"insurance_trigger_r": 1.0}))

    # SP + no_range_stag
    combos.append((
        "SP05+noRStag",
        sp,
        {"range_day_stagnation_enabled": False},
    ))

    # SP + stag_mfe=0.15
    combos.append((
        "SP05+smfe15",
        sp,
        {"stagnation_min_mfe_r": 0.15},
    ))

    # SP + be_r=0.5
    combos.append(("SP05+be05", sp, {"range_day_early_be_r": 0.5}))

    # SP + IT10 + noRStag
    combos.append((
        "SP05+IT10+noRStag",
        sp,
        {
            "insurance_trigger_r": 1.0,
            "range_day_stagnation_enabled": False,
        },
    ))

    # SP + IT10 + smfe15
    combos.append((
        "SP05+IT10+smfe15",
        sp,
        {
            "insurance_trigger_r": 1.0,
            "stagnation_min_mfe_r": 0.15,
        },
    ))

    # SP + IT10 + be05
    combos.append((
        "SP05+IT10+be05",
        sp,
        {
            "insurance_trigger_r": 1.0,
            "range_day_early_be_r": 0.5,
        },
    ))

    # SP + noRStag + smfe15
    combos.append((
        "SP05+noRStag+smfe15",
        sp,
        {
            "range_day_stagnation_enabled": False,
            "stagnation_min_mfe_r": 0.15,
        },
    ))

    # SP + IT10 + noRStag + smfe15
    combos.append((
        "SP05+IT10+noRS+smfe15",
        sp,
        {
            "insurance_trigger_r": 1.0,
            "range_day_stagnation_enabled": False,
            "stagnation_min_mfe_r": 0.15,
        },
    ))

    # SP + IT10 + noRStag + be05
    combos.append((
        "SP05+IT10+noRS+be05",
        sp,
        {
            "insurance_trigger_r": 1.0,
            "range_day_stagnation_enabled": False,
            "range_day_early_be_r": 0.5,
        },
    ))

    # 全部盛り
    combos.append((
        "SP05+IT10+noRS+smfe15+be05",
        sp,
        {
            "insurance_trigger_r": 1.0,
            "range_day_stagnation_enabled": False,
            "stagnation_min_mfe_r": 0.15,
            "range_day_early_be_r": 0.5,
        },
    ))

    # score_premの値変動テスト（IT10固定）
    for sv in [0.3, 0.4, 0.6, 0.7]:
        combos.append((
            f"SP{sv}+IT10",
            {"range_day_score_premium": sv},
            {"insurance_trigger_r": 1.0},
        ))

    # no_insurance（IT10の対極）+ SP05
    combos.append((
        "SP05+noIns",
        sp,
        {"range_day_insurance_enabled": False},
    ))

    return combos


def get_phase3_combos():
    """フェーズ3: ベスト付近の微調整

    フェーズ2ベスト: SP05+IT10+noRS+smfe15+be05
      PF=3.05, DD=0.98%, SR=6.28, AR=38.1%, M+=100%
      パラメータ:
        range_day_score_premium=0.5
        insurance_trigger_r=1.0
        range_day_stagnation_enabled=False
        stagnation_min_mfe_r=0.15
        range_day_early_be_r=0.5
    """
    combos = []
    combos.append(("BASELINE", {}, {}))

    # ベスト設定
    best_bot = {"range_day_score_premium": 0.5}
    best_pm = {
        "insurance_trigger_r": 1.0,
        "range_day_stagnation_enabled": False,
        "stagnation_min_mfe_r": 0.15,
        "range_day_early_be_r": 0.5,
    }
    combos.append(("BEST", best_bot, best_pm))

    # score_premium微調整
    for sp in [0.4, 0.45, 0.55, 0.6]:
        combos.append((
            f"sp={sp}",
            {"range_day_score_premium": sp},
            best_pm,
        ))

    # insurance_trigger_r微調整
    for itr in [0.8, 0.9, 1.1, 1.2]:
        combos.append((
            f"itr={itr}",
            best_bot,
            {**best_pm, "insurance_trigger_r": itr},
        ))

    # stag_mfe微調整
    for mfe in [0.10, 0.12, 0.18, 0.20]:
        combos.append((
            f"smfe={mfe}",
            best_bot,
            {**best_pm, "stagnation_min_mfe_r": mfe},
        ))

    # be_r微調整
    for br in [0.3, 0.4, 0.6, 0.7]:
        combos.append((
            f"be_r={br}",
            best_bot,
            {**best_pm, "range_day_early_be_r": br},
        ))

    # stag有効+他パラメータ（noRStagなし版）
    combos.append((
        "withRStag",
        best_bot,
        {
            "insurance_trigger_r": 1.0,
            "range_day_stagnation_enabled": True,
            "stagnation_min_mfe_r": 0.15,
            "range_day_early_be_r": 0.5,
        },
    ))

    # bbw閾値との組み合わせ
    for bbw in [0.22, 0.25]:
        combos.append((
            f"bbw={bbw}",
            {
                "range_day_score_premium": 0.5,
                "range_day_bbw_threshold": bbw,
            },
            best_pm,
        ))

    # weak_hours調整
    combos.append((
        "noWH",
        {
            "range_day_score_premium": 0.5,
            "weak_hours_enabled": False,
        },
        best_pm,
    ))

    combos.append((
        "wh=1.0",
        {
            "range_day_score_premium": 0.5,
            "weak_hours_score_premium": 1.0,
        },
        best_pm,
    ))

    return combos


def get_validation_combos():
    """検証: トップ候補を全期間で検証"""
    best_pm = {
        "insurance_trigger_r": 1.0,
        "range_day_stagnation_enabled": False,
        "stagnation_min_mfe_r": 0.15,
        "range_day_early_be_r": 0.5,
    }
    combos = []
    combos.append(("BASELINE", {}, {}))

    # フェーズ2ベスト（sp=0.5 全部盛り）
    combos.append((
        "F2_BEST_sp05",
        {"range_day_score_premium": 0.5},
        best_pm,
    ))

    # フェーズ3ベスト1: sp=0.55
    combos.append((
        "F3_sp055",
        {"range_day_score_premium": 0.55},
        best_pm,
    ))

    # フェーズ3ベスト2: be_r=0.3 + sp=0.5
    combos.append((
        "F3_be03",
        {"range_day_score_premium": 0.5},
        {**best_pm, "range_day_early_be_r": 0.3},
    ))

    # フェーズ3ベスト3: sp=0.55 + be_r=0.3
    combos.append((
        "F3_sp055_be03",
        {"range_day_score_premium": 0.55},
        {**best_pm, "range_day_early_be_r": 0.3},
    ))

    # フェーズ3ベスト4: bbw=0.25 + sp=0.5
    combos.append((
        "F3_bbw25",
        {
            "range_day_score_premium": 0.5,
            "range_day_bbw_threshold": 0.25,
        },
        best_pm,
    ))

    # 組み合わせ: sp=0.55 + be_r=0.3 + bbw=0.25
    combos.append((
        "F3_sp055_be03_bbw25",
        {
            "range_day_score_premium": 0.55,
            "range_day_bbw_threshold": 0.25,
        },
        {**best_pm, "range_day_early_be_r": 0.3},
    ))

    return combos


def run_phase(runner, combos, start_year, end_year):
    """フェーズ実行"""
    results: list[ComboResult] = []
    total = len(combos)

    for i, item in enumerate(combos):
        if len(item) == 3:
            name, bot_ov, pm_ov = item
        else:
            name, bot_ov, pm_ov = item[0], item[1], item[2]

        bot_kw = {**DEFAULT_BOT, **bot_ov}
        pm_kw = {**DEFAULT_PM, **pm_ov}

        t1 = time.time()
        r = run_combo(
            runner, name, start_year, end_year,
            bot_kw, pm_kw,
        )
        elapsed = time.time() - t1

        results.append(r)
        print(
            f"[{i+1:2d}/{total}] {name:<35s} "
            f"T={r.trades:4d} "
            f"WR={r.win_rate:5.1f}% "
            f"NLR={r.non_loss_rate:5.1f}% "
            f"PF={r.pf:5.2f} "
            f"P={r.net_profit:+10,.0f} "
            f"DD={r.max_dd:5.2f}% "
            f"AR={r.annual_return:5.1f}% "
            f"SR={r.sharpe:5.2f} "
            f"M+={r.monthly_positive_rate:5.1f}% "
            f"({elapsed:.0f}s)"
        )

    return results


def print_rankings(results: list[ComboResult]):
    """ランキング表示"""
    baseline = results[0] if results[0].name == "BASELINE" else None

    # PFランキング
    print("\n" + "=" * 110)
    print("PFランキング (top 15)")
    print("=" * 110)
    sorted_pf = sorted(results, key=lambda r: r.pf, reverse=True)
    for i, r in enumerate(sorted_pf[:15]):
        delta = ""
        if baseline:
            d = r.pf - baseline.pf
            delta = f" ({d:+.2f})" if abs(d) > 0.001 else " (BASE)"
        print(
            f"  {i+1:2d}. {r.name:<35s} "
            f"PF={r.pf:5.2f}{delta:<10s} "
            f"T={r.trades:4d} WR={r.win_rate:5.1f}% "
            f"DD={r.max_dd:5.2f}% AR={r.annual_return:5.1f}% "
            f"M+={r.monthly_positive_rate:5.1f}%"
        )

    # Sharpeランキング
    print("\nSharpeランキング (top 15)")
    print("-" * 110)
    sorted_sr = sorted(
        results, key=lambda r: r.sharpe, reverse=True
    )
    for i, r in enumerate(sorted_sr[:15]):
        delta = ""
        if baseline:
            d = r.sharpe - baseline.sharpe
            delta = f" ({d:+.2f})" if abs(d) > 0.001 else " (BASE)"
        print(
            f"  {i+1:2d}. {r.name:<35s} "
            f"SR={r.sharpe:5.2f}{delta:<10s} "
            f"PF={r.pf:5.2f} DD={r.max_dd:5.2f}% "
            f"AR={r.annual_return:5.1f}% "
            f"M+={r.monthly_positive_rate:5.1f}%"
        )

    # 総合スコア（PF×Sharpe / DD）
    print("\n総合スコア (PF*Sharpe/DD) ランキング (top 15)")
    print("-" * 110)

    def composite_score(r: ComboResult) -> float:
        dd = max(r.max_dd, 0.01)
        return r.pf * r.sharpe / dd

    sorted_comp = sorted(
        results, key=composite_score, reverse=True
    )
    for i, r in enumerate(sorted_comp[:15]):
        cs = composite_score(r)
        base_cs = composite_score(baseline) if baseline else 0
        delta = ""
        if baseline:
            d = cs - base_cs
            delta = f" ({d:+.1f})" if abs(d) > 0.01 else " (BASE)"
        print(
            f"  {i+1:2d}. {r.name:<35s} "
            f"CS={cs:7.1f}{delta:<10s} "
            f"PF={r.pf:5.2f} SR={r.sharpe:5.2f} "
            f"DD={r.max_dd:5.2f}% AR={r.annual_return:5.1f}% "
            f"M+={r.monthly_positive_rate:5.1f}%"
        )

    # ベースラインからの改善サマリー
    if baseline:
        print("\nベースラインからの変動サマリー")
        print("-" * 110)
        print(
            f"  {'パラメータ':<35s} "
            f"{'ΔPF':>7s} {'ΔSR':>7s} {'ΔDD':>7s} "
            f"{'ΔAR':>7s} {'ΔP':>12s} {'ΔT':>5s}"
        )
        for r in results:
            if r.name == "BASELINE":
                continue
            print(
                f"  {r.name:<35s} "
                f"{r.pf - baseline.pf:+6.2f} "
                f"{r.sharpe - baseline.sharpe:+6.2f} "
                f"{r.max_dd - baseline.max_dd:+6.2f}% "
                f"{r.annual_return - baseline.annual_return:+6.1f}% "
                f"{r.net_profit - baseline.net_profit:+11,.0f} "
                f"{r.trades - baseline.trades:+5d}"
            )


def main():
    """メイン関数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="パラメータ組み合わせ探索",
    )
    parser.add_argument(
        "--years", default="2023-2025",
        help="バックテスト期間",
    )
    parser.add_argument(
        "--phase", default="1",
        choices=["1", "2", "3", "v"],
        help="探索フェーズ: 1=個別, 2=組合せ, 3=微調整, v=検証",
    )
    args = parser.parse_args()

    parts = args.years.split("-")
    start_year, end_year = int(parts[0]), int(parts[1])

    # データ読み込み（1回のみ）
    from autotrader.backtest.service import (
        BacktestService,
        BacktestServiceConfig,
    )

    config = BacktestServiceConfig(
        start_year=start_year,
        end_year=end_year,
        initial_balance=1_000_000.0,
        volume=1.0,
        data_dir="data/csv",
        symbol="USDJPY",
        timeframe="M15",
        max_positions=1,
        verbose=False,
        use_short_timeframe=True,
    )

    service = BacktestService(config)
    runner = service.create_runner()

    print("データ読み込み中...")
    t0 = time.time()
    runner.load_data()
    print(f"データ読み込み完了 ({time.time() - t0:.1f}秒)")

    # フェーズ選択
    phase = args.phase
    if phase == "1":
        combos = get_phase1_combos()
    elif phase == "2":
        combos = get_phase2_combos()
    elif phase == "3":
        combos = get_phase3_combos()
    else:
        combos = get_validation_combos()

    total = len(combos)
    est_min = total * 3.5
    print(f"\nフェーズ{phase}: {total}パターン (推定{est_min:.0f}分)")
    print("=" * 110)

    results = run_phase(runner, combos, start_year, end_year)
    print_rankings(results)


if __name__ == "__main__":
    main()
