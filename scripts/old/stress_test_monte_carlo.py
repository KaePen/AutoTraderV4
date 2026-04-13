"""Phase 5: モンテカルロ分析（BT再実行不要）

既存BT結果の trades.csv を読み込み、以下の3分析を実行:
1. トレード順序シャッフル → PF/DD分布
2. 復元抽出リサンプリング → PF/DD分布
3. PnL ±10-20%ノイズ → PF/DD分布

使用方法:
    uv run python scripts/stress_test_monte_carlo.py <result_id>
    uv run python scripts/stress_test_monte_carlo.py 0000074
    uv run python scripts/stress_test_monte_carlo.py 0000074 --sims 5000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# プロジェクトルートをパスに追加
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from autotrader.config.paths import get_results_dir

INITIAL_BALANCE = 1_000_000.0
DEFAULT_SIMS = 1000


def _calc_pf(pnls: np.ndarray) -> float:
    """Profit Factor計算"""
    gross_profit = float(np.sum(pnls[pnls > 0]))
    gross_loss = float(np.abs(np.sum(pnls[pnls < 0])))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 3)


def _calc_max_dd_pct(
    pnls: np.ndarray,
    initial_balance: float = INITIAL_BALANCE,
) -> float:
    """最大ドローダウン（%）計算"""
    equity = initial_balance + np.cumsum(pnls)
    equity = np.insert(equity, 0, initial_balance)
    peak = np.maximum.accumulate(equity)
    dd_pct = (peak - equity) / peak * 100
    return round(float(dd_pct.max()), 2)


def _calc_win_rate(pnls: np.ndarray) -> float:
    """勝率計算"""
    if len(pnls) == 0:
        return 0.0
    return round(float(np.sum(pnls > 0) / len(pnls) * 100), 1)


def trade_shuffle(
    pnls: np.ndarray,
    n_sims: int = DEFAULT_SIMS,
    initial_balance: float = INITIAL_BALANCE,
) -> dict:
    """トレード順序シャッフル → PF/DD分布

    Args:
        pnls: 各トレードの損益配列
        n_sims: シミュレーション回数
        initial_balance: 初期残高

    Returns:
        PF/DD分布の統計量
    """
    rng = np.random.default_rng(42)
    pf_arr = np.zeros(n_sims)
    dd_arr = np.zeros(n_sims)

    for i in range(n_sims):
        shuffled = rng.permutation(pnls)
        pf_arr[i] = _calc_pf(shuffled)
        dd_arr[i] = _calc_max_dd_pct(shuffled, initial_balance)

    return {
        "method": "trade_shuffle",
        "n_sims": n_sims,
        "pf_mean": round(float(np.mean(pf_arr)), 3),
        "pf_median": round(float(np.median(pf_arr)), 3),
        "pf_p5": round(float(np.percentile(pf_arr, 5)), 3),
        "pf_worst": round(float(np.min(pf_arr)), 3),
        "dd_mean": round(float(np.mean(dd_arr)), 2),
        "dd_p95": round(float(np.percentile(dd_arr, 95)), 2),
        "dd_worst": round(float(np.max(dd_arr)), 2),
    }


def bootstrap_resample(
    pnls: np.ndarray,
    n_sims: int = DEFAULT_SIMS,
    initial_balance: float = INITIAL_BALANCE,
) -> dict:
    """復元抽出リサンプリング → PF/DD分布

    Args:
        pnls: 各トレードの損益配列
        n_sims: シミュレーション回数
        initial_balance: 初期残高

    Returns:
        PF/DD分布の統計量
    """
    rng = np.random.default_rng(42)
    n = len(pnls)
    pf_arr = np.zeros(n_sims)
    dd_arr = np.zeros(n_sims)

    for i in range(n_sims):
        sample = rng.choice(pnls, size=n, replace=True)
        pf_arr[i] = _calc_pf(sample)
        dd_arr[i] = _calc_max_dd_pct(sample, initial_balance)

    return {
        "method": "bootstrap_resample",
        "n_sims": n_sims,
        "pf_mean": round(float(np.mean(pf_arr)), 3),
        "pf_median": round(float(np.median(pf_arr)), 3),
        "pf_p5": round(float(np.percentile(pf_arr, 5)), 3),
        "pf_worst": round(float(np.min(pf_arr)), 3),
        "dd_mean": round(float(np.mean(dd_arr)), 2),
        "dd_p95": round(float(np.percentile(dd_arr, 95)), 2),
        "dd_worst": round(float(np.max(dd_arr)), 2),
    }


def pnl_noise(
    pnls: np.ndarray,
    noise_pct: float = 0.1,
    n_sims: int = DEFAULT_SIMS,
    initial_balance: float = INITIAL_BALANCE,
) -> dict:
    """PnL ±ノイズ → PF/DD分布

    各トレードの損益に ±noise_pct のランダムノイズを加える。

    Args:
        pnls: 各トレードの損益配列
        noise_pct: ノイズ率（0.1 = ±10%）
        n_sims: シミュレーション回数
        initial_balance: 初期残高

    Returns:
        PF/DD分布の統計量
    """
    rng = np.random.default_rng(42)
    pf_arr = np.zeros(n_sims)
    dd_arr = np.zeros(n_sims)

    for i in range(n_sims):
        noise = rng.uniform(
            1.0 - noise_pct, 1.0 + noise_pct, size=len(pnls),
        )
        noised = pnls * noise
        pf_arr[i] = _calc_pf(noised)
        dd_arr[i] = _calc_max_dd_pct(noised, initial_balance)

    return {
        "method": f"pnl_noise_{int(noise_pct * 100)}pct",
        "noise_pct": noise_pct,
        "n_sims": n_sims,
        "pf_mean": round(float(np.mean(pf_arr)), 3),
        "pf_median": round(float(np.median(pf_arr)), 3),
        "pf_p5": round(float(np.percentile(pf_arr, 5)), 3),
        "pf_worst": round(float(np.min(pf_arr)), 3),
        "dd_mean": round(float(np.mean(dd_arr)), 2),
        "dd_p95": round(float(np.percentile(dd_arr, 95)), 2),
        "dd_worst": round(float(np.max(dd_arr)), 2),
    }


def load_trades(result_id: str) -> pd.DataFrame:
    """BT結果からトレードCSVを読み込む

    Args:
        result_id: 結果ID（例: "0000074"）

    Returns:
        trades DataFrame（profit_loss列を含む）

    Raises:
        FileNotFoundError: trades.csvが見つからない場合
    """
    results_dir = get_results_dir()
    trades_path = results_dir / result_id / "trades.csv"

    if not trades_path.exists():
        raise FileNotFoundError(
            f"trades.csv が見つかりません: {trades_path}",
        )

    df = pd.read_csv(trades_path)

    if "profit_loss" not in df.columns:
        raise ValueError(
            f"profit_loss列がありません: {list(df.columns)}",
        )

    return df


def run_phase5(
    result_id: str,
    n_sims: int = DEFAULT_SIMS,
) -> dict:
    """Phase 5 モンテカルロ分析を実行

    Args:
        result_id: BT結果ID
        n_sims: シミュレーション回数

    Returns:
        全分析結果のdict
    """
    df = load_trades(result_id)
    pnls = df["profit_loss"].values.astype(float)

    # 観測値
    observed_pf = _calc_pf(pnls)
    observed_dd = _calc_max_dd_pct(pnls)
    observed_wr = _calc_win_rate(pnls)
    total_trades = len(pnls)
    net_profit = round(float(np.sum(pnls)), 0)

    print(f"トレード数: {total_trades}")
    print(f"観測PF: {observed_pf}")
    print(f"観測DD: {observed_dd}%")
    print(f"観測WR: {observed_wr}%")
    print(f"純利益: {net_profit:,.0f}")
    print()

    # 3分析実行
    print("1/3 トレード順序シャッフル...")
    shuffle_result = trade_shuffle(pnls, n_sims)

    print("2/3 ブートストラップリサンプリング...")
    bootstrap_result = bootstrap_resample(pnls, n_sims)

    print("3/3 PnLノイズ (±10%, ±20%)...")
    noise_10_result = pnl_noise(pnls, 0.10, n_sims)
    noise_20_result = pnl_noise(pnls, 0.20, n_sims)

    return {
        "result_id": result_id,
        "total_trades": total_trades,
        "net_profit": net_profit,
        "observed": {
            "pf": observed_pf,
            "dd": observed_dd,
            "wr": observed_wr,
        },
        "analyses": [
            shuffle_result,
            bootstrap_result,
            noise_10_result,
            noise_20_result,
        ],
    }


def main() -> None:
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description="Phase 5 モンテカルロ分析",
    )
    parser.add_argument(
        "result_id",
        help="BT結果ID（例: 0000074）",
    )
    parser.add_argument(
        "--sims",
        type=int,
        default=DEFAULT_SIMS,
        help=f"シミュレーション回数（デフォルト: {DEFAULT_SIMS}）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="出力ファイルパス（省略時: reports/下に自動生成）",
    )
    args = parser.parse_args()

    result = run_phase5(args.result_id, args.sims)

    # 出力先決定
    if args.output:
        out_path = Path(args.output)
    else:
        reports_dir = _project_root / "reports"
        reports_dir.mkdir(exist_ok=True)
        out_path = (
            reports_dir
            / f"stress_p5_mc_{args.result_id}.json"
        )

    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n結果出力: {out_path}")

    # 合格基準判定
    print("\n--- 合格基準判定 ---")
    _pass = True
    for analysis in result["analyses"]:
        method = analysis["method"]
        pf_worst = analysis["pf_worst"]
        dd_worst = analysis["dd_worst"]
        pf_ok = "PASS" if pf_worst >= 1.3 else "FAIL"
        dd_ok = "PASS" if dd_worst <= 15.0 else "FAIL"
        if pf_worst < 1.3 or dd_worst > 15.0:
            _pass = False
        print(
            f"  {method}: "
            f"PF(worst)={pf_worst} [{pf_ok}]  "
            f"DD(worst)={dd_worst}% [{dd_ok}]",
        )

    print(f"\n総合判定: {'PASS' if _pass else 'FAIL'}")


if __name__ == "__main__":
    main()
