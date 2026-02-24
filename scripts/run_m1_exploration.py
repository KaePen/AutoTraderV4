"""M1必須 × 同時ポジション数 × 閾値 網羅探索

前回最適化のベスト設定をベースに、M1エントリー必須・
同時ポジション数1-3・閾値2.0-5.0を網羅的に検証する。

Phase 1: 広域スイープ (M1 TF × thresholds × max_positions)
Phase 2: トップ結果の重み・PMリファイン
Phase 3: OOS検証

Usage:
    python scripts/run_m1_exploration.py --phase 1
    python scripts/run_m1_exploration.py --phase 2
    python scripts/run_m1_exploration.py --phase 3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import sys
import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# TF優先順
TF_ORDER = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]
TF_RANK = {tf: i for i, tf in enumerate(TF_ORDER)}

INITIAL_BALANCE = 1_000_000.0
IS_YEARS = (2020, 2022)
OOS_YEARS = (2023, 2025)

RESULT_COLUMNS = [
    "run_id", "phase", "params_json",
    "trades", "win_rate", "non_loss_rate",
    "profit_factor", "net_profit", "max_drawdown",
    "sharpe_ratio", "annual_return",
    "composite_score", "elapsed_sec",
]

# 前回最適化のベスト設定（Phase 1のベースライン）
BEST_WEIGHTS = {
    "consensus_primary_weight": 2.0,
    "consensus_entry_weight": 1.5,
    "consensus_confirm_weight": 3.0,
    "consensus_manage_weight": 0.5,
    "consensus_other_weight": 1.0,
}

BEST_PM = {
    "trailing_start_r": 2.5,
    "trailing_atr_mult": 2.0,
    "stag_exit_minutes": 180.0,
    "partial_1r_ratio": 0.05,
    "early_be_r": 0.5,
    "signal_rev_ratio": 0.5,
}

BEST_FILTERS = {
    "tp_sl_ratio": 1.2,
    "sl_min_pips": 8.0,
    "range_day_bbw_threshold": 0.25,
    "range_day_score_premium": 0.3,
    "weak_hours_score_premium": 0.5,
    "sg_spread_penalty_rate": 0.2,
    "sg_off_hours_penalty": 0.25,
    "sg_volatility_penalty": 0.05,
    "sg_recent_loss_penalty": 0.1,
}


def composite_score(
    profit_factor: float,
    max_drawdown: float,
    win_rate: float,
    trades: int = 0,
    years: int = 3,
    annual_return: float = 0.0,
) -> float:
    """複合スコア（収益重視版）。

    Args:
        profit_factor: PF
        max_drawdown: DD%
        win_rate: WR%
        trades: 取引数
        years: 期間年数
        annual_return: 年間リターン%

    Returns:
        float: 複合スコア
    """
    pf_capped = min(profit_factor, 10.0)
    dd_factor = max(0.0, 1.0 - max_drawdown / 100.0)
    wr_factor = min(1.0, win_rate / 50.0)
    # 年間100取引を基準（前回は50だったが今回は多頻度重視）
    min_trades = 100 * years
    trade_factor = min(1.0, trades / min_trades)
    # 年間リターンボーナス（30%以上で1.0、それ以下は比例）
    ret_bonus = min(1.0, max(0.0, annual_return) / 30.0)
    return (
        pf_capped * dd_factor * wr_factor
        * trade_factor * (0.7 + 0.3 * ret_bonus)
    )


def resolve_regime_tf(combo: list[str]) -> str:
    """レジーム検出TFを決定する。"""
    if "H1" in combo:
        return "H1"
    for tf in ["H4", "H8", "D1"]:
        if tf in combo:
            return tf
    sorted_combo = sorted(
        combo, key=lambda t: TF_RANK.get(t, 0)
    )
    return sorted_combo[-1]


def resolve_htf_alignment(
    combo: list[str], regime_tf: str,
) -> list[str]:
    """HTF整合性TFを決定する。"""
    candidates = []
    if "H4" in combo:
        candidates.append("H4")
    if "D1" in combo:
        candidates.append("D1")
    if candidates:
        return candidates
    regime_rank = TF_RANK.get(regime_tf, 0)
    upper = sorted(
        [t for t in combo if TF_RANK.get(t, 0) >= regime_rank],
        key=lambda t: TF_RANK.get(t, 0), reverse=True,
    )
    return upper[:2] if upper else [combo[-1]]


def make_run_id(phase: int, params: dict) -> str:
    """ユニークrun_id生成。"""
    raw = f"p{phase}:" + str(sorted(params.items()))
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"p{phase}_{h}"


def load_completed(output_path: Path) -> set[str]:
    """完了済みrun_id読み込み。"""
    completed = set()
    if not output_path.exists():
        return completed
    with open(output_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if "run_id" in row:
                completed.add(row["run_id"])
    return completed


def append_result(
    output_path: Path, row: dict,
    write_header: bool = False,
) -> None:
    """結果を1行追記。"""
    mode = "w" if write_header else "a"
    with open(
        output_path, mode, encoding="utf-8", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=RESULT_COLUMNS
        )
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def load_top_n(
    csv_path: Path, n: int, years: int = 3,
) -> list[dict]:
    """CSVからスコアTop Nを読み込む（再計算）。"""
    rows = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            pf = float(row.get("profit_factor", 0))
            dd = float(row.get("max_drawdown", 0))
            wr = float(row.get("win_rate", 0))
            tr = int(float(row.get("trades", 0)))
            ar = float(row.get("annual_return", 0))
            row["composite_score"] = composite_score(
                pf, dd, wr, tr, years, ar,
            )
            rows.append(row)
    rows.sort(
        key=lambda r: r["composite_score"], reverse=True
    )
    result = []
    for r in rows[:n]:
        params = json.loads(r["params_json"])
        params["_composite_score"] = r["composite_score"]
        params["_run_id"] = r.get("run_id", "")
        params["_trades"] = int(float(r.get("trades", 0)))
        params["_annual_return"] = float(
            r.get("annual_return", 0)
        )
        result.append(params)
    return result


def lhs_sample(
    param_ranges: dict[str, list],
    n_samples: int, seed: int = 42,
) -> list[dict]:
    """LHSサンプリング。"""
    rng = random.Random(seed)
    names = list(param_ranges.keys())
    intervals: dict[str, list] = {}
    for name in names:
        values = param_ranges[name]
        n_vals = len(values)
        indices = [i % n_vals for i in range(n_samples)]
        rng.shuffle(indices)
        intervals[name] = [values[idx] for idx in indices]
    return [
        {name: intervals[name][i] for name in names}
        for i in range(n_samples)
    ]


def run_backtest(
    params: dict, start_year: int, end_year: int,
    data_dir: str, max_year_workers: int,
) -> dict:
    """バックテスト1回実行。"""
    from autotrader.backtest.runner import (
        BacktestConfig, BacktestRunner,
    )
    from autotrader.decision.unified import (
        UnifiedBotConfig,
    )
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )

    symbol = params.get("symbol", "USDJPY")
    timeframes = params.get("timeframes", [])
    if isinstance(timeframes, str):
        timeframes = timeframes.split(",")
    regime_tf = params.get(
        "regime_detection_tf",
        resolve_regime_tf(timeframes),
    )
    htf_tfs = params.get(
        "htf_alignment_tfs",
        resolve_htf_alignment(timeframes, regime_tf),
    )
    if isinstance(htf_tfs, str):
        htf_tfs = htf_tfs.split(",")

    # 固定パラメータ
    max_pos = params.get("max_positions", 1)
    bonus_pos = params.get("bonus_max_positions", 0)

    bot_kwargs: dict = {
        "timeframes": timeframes,
        "regime_detection_tf": regime_tf,
        "htf_alignment_tfs": htf_tfs,
        "max_positions": max_pos,
        "bonus_max_positions": bonus_pos,
        "use_dynamic_lot": True,
        "base_risk_pct": 0.04,
        "max_risk_pct_absolute": 0.07,
        "max_lot_per_trade": 5.0,
    }

    _bot_fields = {
        "consensus_threshold",
        "consensus_primary_weight",
        "consensus_entry_weight",
        "consensus_confirm_weight",
        "consensus_manage_weight",
        "consensus_other_weight",
        "tp_sl_ratio", "sl_min_pips",
        "range_day_bbw_threshold",
        "range_day_score_premium",
        "weak_hours_score_premium",
        "sg_spread_penalty_rate",
        "sg_off_hours_penalty",
        "sg_volatility_penalty",
        "sg_recent_loss_penalty",
    }
    for k in _bot_fields:
        if k in params:
            bot_kwargs[k] = params[k]

    bot_config = UnifiedBotConfig(**bot_kwargs)

    pm_kwargs: dict = {}
    _pm_fields = {
        "trailing_start_r": "trailing_start_r",
        "trailing_atr_mult": "trailing_atr_multiplier",
        "stag_exit_minutes": "stagnation_exit_minutes",
        "partial_1r_ratio": "partial_close_1r_ratio",
        "early_be_r": "early_breakeven_r",
        "signal_rev_ratio": "signal_rev_close_ratio",
    }
    for param_key, config_key in _pm_fields.items():
        if param_key in params:
            pm_kwargs[config_key] = params[param_key]

    pm_config = (
        PositionManagerConfig(**pm_kwargs) if pm_kwargs
        else None
    )

    config = BacktestConfig.from_preset(symbol)
    config = BacktestConfig(
        symbol=config.symbol,
        initial_balance=INITIAL_BALANCE,
        max_positions=max_pos,
        bonus_max_positions=bonus_pos,
        spread_pips=config.spread_pips,
        slippage_pips=config.slippage_pips,
        pip_value=config.pip_value,
        commission_per_lot=config.commission_per_lot,
    )

    runner = BacktestRunner(
        data_dir=data_dir, config=config,
        verbose=False, log_to_file=False,
    )

    result = runner.run_unified(
        start_year=start_year, end_year=end_year,
        config=bot_config, pm_config=pm_config,
        max_year_workers=max_year_workers,
    )

    return {
        "trades": result.trades,
        "win_rate": round(result.win_rate, 2),
        "non_loss_rate": round(result.non_loss_rate, 2),
        "profit_factor": round(result.profit_factor, 2),
        "net_profit": round(result.net_profit, 0),
        "max_drawdown": round(result.max_drawdown, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 2),
        "annual_return": round(result.annual_return, 2),
    }


# ------------------------------------------------------------------
# Phase 1: 広域スイープ
# ------------------------------------------------------------------

def phase1_params() -> list[dict]:
    """M1必須TF × 閾値 × max_positions 全組み合わせ。"""
    # M1を必ず含むTF組み合わせ
    tf_combos = [
        ["M1", "M5", "M15", "H1", "H4"],
        ["M1", "M15", "H1", "H4"],
        ["M1", "M5", "M15", "H1", "H4", "D1"],
        ["M1", "M15", "H1", "H4", "D1"],
        ["M1", "M5", "M15", "M30", "H1", "H4"],
        ["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
    ]
    thresholds = [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    max_positions_list = [1, 2, 3]

    params_list = []
    for combo in tf_combos:
        regime_tf = resolve_regime_tf(combo)
        htf_tfs = resolve_htf_alignment(combo, regime_tf)
        for th in thresholds:
            for mp in max_positions_list:
                p = {
                    "timeframes": combo,
                    "regime_detection_tf": regime_tf,
                    "htf_alignment_tfs": htf_tfs,
                    "consensus_threshold": th,
                    "max_positions": mp,
                    "bonus_max_positions": 0,
                }
                # ベスト設定をベースに適用
                p.update(BEST_WEIGHTS)
                p.update(BEST_PM)
                p.update(BEST_FILTERS)
                params_list.append(p)
    return params_list


# ------------------------------------------------------------------
# Phase 2: リファインメント
# ------------------------------------------------------------------

def load_top_n_by_return(
    csv_path: Path, n: int, years: int = 3,
) -> list[dict]:
    """CSVから年間リターンTop Nを読み込む。"""
    rows = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    rows.sort(
        key=lambda r: float(r.get("annual_return", 0)),
        reverse=True,
    )
    result = []
    for r in rows[:n]:
        params = json.loads(r["params_json"])
        params["_composite_score"] = float(
            r.get("composite_score", 0)
        )
        params["_run_id"] = r.get("run_id", "")
        params["_trades"] = int(float(r.get("trades", 0)))
        params["_annual_return"] = float(
            r.get("annual_return", 0)
        )
        result.append(params)
    return result


def select_diverse_top(
    csv_path: Path, n: int = 10, years: int = 3,
) -> list[dict]:
    """スコアTop + リターンTopから重複排除で多様なTop N選択。"""
    by_score = load_top_n(csv_path, n, years)
    by_return = load_top_n_by_return(csv_path, n, years)

    selected: list[dict] = []
    seen_ids: set[str] = set()

    # スコアとリターンを交互に取得（多様性確保）
    score_idx = 0
    ret_idx = 0
    while len(selected) < n:
        # スコア側から1つ
        while score_idx < len(by_score):
            rid = by_score[score_idx].get("_run_id", "")
            if rid not in seen_ids:
                selected.append(by_score[score_idx])
                seen_ids.add(rid)
                score_idx += 1
                break
            score_idx += 1
        if len(selected) >= n:
            break
        # リターン側から1つ
        while ret_idx < len(by_return):
            rid = by_return[ret_idx].get("_run_id", "")
            if rid not in seen_ids:
                selected.append(by_return[ret_idx])
                seen_ids.add(rid)
                ret_idx += 1
                break
            ret_idx += 1
        # 両方枯渇したら終了
        if (
            score_idx >= len(by_score)
            and ret_idx >= len(by_return)
        ):
            break

    return selected


def phase2_params(prev_csv: Path) -> list[dict]:
    """多様なTop 10に重み・PM・閾値・フィルタを微調整。"""
    top10 = select_diverse_top(prev_csv, 10)

    logger.info("Phase 2 ベース構成:")
    for i, b in enumerate(top10, 1):
        logger.info(
            "  %d. %s score=%.3f tr=%d ret=%.1f%%",
            i, b.get("_run_id", ""),
            b.get("_composite_score", 0),
            b.get("_trades", 0),
            b.get("_annual_return", 0),
        )

    # 重み微調整（10パターン）
    weight_tweaks = lhs_sample({
        "consensus_primary_weight": [
            1.5, 2.0, 2.5, 3.0,
        ],
        "consensus_entry_weight": [
            1.0, 1.5, 2.0, 2.5, 3.0,
        ],
        "consensus_confirm_weight": [
            2.0, 3.0, 4.0, 5.0,
        ],
        "consensus_manage_weight": [0.5, 1.0, 1.5],
        "consensus_other_weight": [0.5, 1.0, 2.0],
    }, 10, seed=777)

    # PM微調整（10パターン）
    pm_tweaks = lhs_sample({
        "trailing_start_r": [0.5, 1.0, 1.5, 2.0, 2.5],
        "trailing_atr_mult": [1.0, 1.5, 2.0, 2.5],
        "stag_exit_minutes": [
            30.0, 60.0, 120.0, 180.0, 240.0,
        ],
        "partial_1r_ratio": [0.0, 0.05, 0.10, 0.15],
        "early_be_r": [0.3, 0.5, 0.7, 1.0],
        "signal_rev_ratio": [0.0, 0.25, 0.5, 0.75],
    }, 10, seed=888)

    # 閾値微調整（6パターン: 各ベースの±0.5）
    threshold_offsets = [-1.0, -0.5, -0.25, 0.25, 0.5, 1.0]

    # フィルタ微調整（4パターン）
    filter_tweaks = lhs_sample({
        "tp_sl_ratio": [0.8, 1.0, 1.2, 1.5, 2.0],
        "sl_min_pips": [5.0, 8.0, 10.0, 12.0],
        "weak_hours_score_premium": [
            0.0, 0.3, 0.5, 0.7,
        ],
        "sg_spread_penalty_rate": [
            0.1, 0.2, 0.3, 0.5,
        ],
        "sg_off_hours_penalty": [
            0.1, 0.15, 0.25, 0.3,
        ],
    }, 4, seed=999)

    params_list = []
    for base in top10:
        base_clean = {
            k: v for k, v in base.items()
            if not k.startswith("_")
        }
        # 重みバリエーション
        for wt in weight_tweaks:
            p = dict(base_clean)
            p.update(wt)
            params_list.append(p)
        # PMバリエーション
        for pt in pm_tweaks:
            p = dict(base_clean)
            p.update(pt)
            params_list.append(p)
        # 閾値バリエーション
        cur_th = base_clean.get("consensus_threshold", 3.0)
        for offset in threshold_offsets:
            new_th = round(cur_th + offset, 2)
            if new_th < 1.0 or new_th > 6.0:
                continue
            p = dict(base_clean)
            p["consensus_threshold"] = new_th
            params_list.append(p)
        # フィルタバリエーション
        for ft in filter_tweaks:
            p = dict(base_clean)
            p.update(ft)
            params_list.append(p)

    return params_list


# ------------------------------------------------------------------
# Phase 3: OOS検証
# ------------------------------------------------------------------

def phase3_params(prev_csv: Path) -> list[dict]:
    """Top 10（多様選択） + ベースライン。"""
    top_configs = select_diverse_top(prev_csv, 10)

    # 前回最適化ベスト（M15,H1,H4 max_pos=1）
    prev_best = {
        "timeframes": ["M15", "H1", "H4"],
        "regime_detection_tf": "H1",
        "htf_alignment_tfs": ["H4"],
        "consensus_threshold": 4.5,
        "max_positions": 1,
        "bonus_max_positions": 0,
        "_label": "prev_best_m15h1h4",
    }
    prev_best.update(BEST_WEIGHTS)
    prev_best.update(BEST_PM)
    prev_best.update(BEST_FILTERS)

    # デフォルトベースライン
    baseline = {
        "timeframes": [
            "M1", "M5", "M15", "M30",
            "H1", "H4", "H8", "D1",
        ],
        "regime_detection_tf": "H1",
        "htf_alignment_tfs": ["H4", "D1"],
        "max_positions": 3,
        "bonus_max_positions": 0,
        "_label": "baseline_default_mp3",
    }

    return top_configs + [prev_best, baseline]


# ------------------------------------------------------------------
# 実行エンジン
# ------------------------------------------------------------------

PHASE_CONFIG = {
    1: {
        "name": "M1必須 広域スイープ",
        "output": "reports/m1_phase1_sweep.csv",
        "generator": lambda _: phase1_params(),
        "prev_csv": None,
        "years": IS_YEARS,
    },
    2: {
        "name": "重み・PMリファイン",
        "output": "reports/m1_phase2_refine.csv",
        "generator": phase2_params,
        "prev_csv": "reports/m1_phase1_sweep.csv",
        "years": IS_YEARS,
    },
    3: {
        "name": "OOS最終検証",
        "output": "reports/m1_phase3_oos.csv",
        "generator": phase3_params,
        "prev_csv": "reports/m1_phase2_refine.csv",
        "years": OOS_YEARS,
    },
}


# CSV書き込み用ロック
_csv_lock = threading.Lock()


def _run_single(args: tuple) -> dict | None:
    """1つのバックテストを実行する（並列実行用）。"""
    (
        run_id, clean_params, phase,
        start_year, end_year,
        data_dir, max_year_workers,
    ) = args
    try:
        t0 = time.time()
        metrics = run_backtest(
            params=clean_params,
            start_year=start_year,
            end_year=end_year,
            data_dir=data_dir,
            max_year_workers=max_year_workers,
        )
        elapsed = time.time() - t0
        n_years = end_year - start_year + 1
        cs = composite_score(
            metrics["profit_factor"],
            metrics["max_drawdown"],
            metrics["win_rate"],
            metrics["trades"],
            n_years,
            metrics["annual_return"],
        )
        return {
            "run_id": run_id,
            "phase": phase,
            "params_json": json.dumps(
                clean_params, ensure_ascii=False
            ),
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "non_loss_rate": metrics["non_loss_rate"],
            "profit_factor": metrics["profit_factor"],
            "net_profit": metrics["net_profit"],
            "max_drawdown": metrics["max_drawdown"],
            "sharpe_ratio": metrics["sharpe_ratio"],
            "annual_return": metrics["annual_return"],
            "composite_score": round(cs, 4),
            "elapsed_sec": round(elapsed, 1),
        }
    except Exception:
        logger.exception("エラー: %s", run_id)
        return None


def run_phase(
    phase: int, data_dir: str,
    max_year_workers: int, dry_run: bool = False,
    concurrent_runs: int = 1,
    worker_id: int = 0,
    num_workers: int = 1,
) -> None:
    """指定フェーズを実行する。"""
    cfg = PHASE_CONFIG[phase]
    output_path = Path(cfg["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    start_year, end_year = cfg["years"]

    logger.info(
        "=== Phase %d: %s (%d-%d) ===",
        phase, cfg["name"], start_year, end_year,
    )

    prev_csv = (
        Path(cfg["prev_csv"]) if cfg["prev_csv"] else None
    )
    if prev_csv and not prev_csv.exists():
        logger.error(
            "前フェーズ結果なし: %s", prev_csv
        )
        sys.exit(1)

    params_list = cfg["generator"](prev_csv)
    logger.info("パラメータ数: %d", len(params_list))

    if dry_run:
        for i, p in enumerate(params_list, 1):
            display = {
                k: v for k, v in p.items()
                if not k.startswith("_")
            }
            logger.info(
                "%3d. %s", i,
                json.dumps(display, ensure_ascii=False),
            )
        logger.info("合計: %d runs", len(params_list))
        return

    completed = load_completed(output_path)
    runs = []
    for p in params_list:
        rid = make_run_id(phase, {
            k: v for k, v in p.items()
            if not k.startswith("_")
        })
        if rid not in completed:
            runs.append((rid, p))

    if completed:
        logger.info(
            "既完了: %d件, 残り: %d件",
            len(completed), len(runs),
        )

    if not runs:
        logger.info("全runs完了済み")
        return

    # ワーカー分割: 各ワーカーは自分の担当runだけ処理
    if num_workers > 1:
        all_runs = runs
        runs = [
            r for i, r in enumerate(all_runs)
            if i % num_workers == worker_id
        ]
        logger.info(
            "Worker %d/%d: 担当 %d/%d runs",
            worker_id, num_workers,
            len(runs), len(all_runs),
        )
        # 各ワーカーは別ファイルに書き出す
        stem = output_path.stem
        output_path = output_path.with_name(
            f"{stem}_w{worker_id}.csv"
        )

    write_header = not output_path.exists()
    total = len(runs)
    t_start = time.time()

    if concurrent_runs > 1:
        logger.info(
            "並列実行: %d concurrent runs, "
            "%d year workers each",
            concurrent_runs, max_year_workers,
        )
        _run_parallel(
            runs, phase, start_year, end_year,
            data_dir, max_year_workers,
            output_path, write_header,
            total, t_start, concurrent_runs,
        )
    else:
        _run_sequential(
            runs, phase, start_year, end_year,
            data_dir, max_year_workers,
            output_path, write_header,
            total, t_start,
        )

    elapsed_total = time.time() - t_start
    logger.info(
        "\nPhase %d 完了: %d runs (%.1f分)",
        phase, total, elapsed_total / 60,
    )

    top5 = load_top_n(output_path, 5)
    logger.info("\n--- Top 5 ---")
    for i, t in enumerate(top5, 1):
        logger.info(
            "%d. score=%.4f trades=%d ann_ret=%.1f%% "
            "run=%s",
            i,
            t.get("_composite_score", 0),
            t.get("_trades", 0),
            t.get("_annual_return", 0),
            t.get("_run_id", ""),
        )


def _run_sequential(
    runs, phase, start_year, end_year,
    data_dir, max_year_workers,
    output_path, write_header, total, t_start,
) -> None:
    """逐次実行。"""
    for idx, (run_id, params) in enumerate(runs, 1):
        clean_params = {
            k: v for k, v in params.items()
            if not k.startswith("_")
        }
        logger.info(
            "--- [%d/%d] %s ---", idx, total, run_id
        )
        result = _run_single((
            run_id, clean_params, phase,
            start_year, end_year,
            data_dir, max_year_workers,
        ))
        if result and result["trades"] > 0:
            append_result(
                output_path, result, write_header
            )
            write_header = False
            elapsed_total = time.time() - t_start
            avg = elapsed_total / idx
            eta = avg * (total - idx)
            logger.info(
                "OK: %s | tr=%d wr=%.1f%% "
                "PF=%.2f DD=%.2f%% "
                "AnnRet=%.1f%% Score=%.4f "
                "| %.1fs (ETA: %.0fm)",
                run_id, result["trades"],
                result["win_rate"],
                result["profit_factor"],
                result["max_drawdown"],
                result["annual_return"],
                result["composite_score"],
                result["elapsed_sec"],
                eta / 60,
            )


def _run_parallel(
    runs, phase, start_year, end_year,
    data_dir, max_year_workers,
    output_path, write_header, total, t_start,
    concurrent_runs,
) -> None:
    """並列実行（ProcessPoolExecutor）。"""
    tasks = []
    for run_id, params in runs:
        clean_params = {
            k: v for k, v in params.items()
            if not k.startswith("_")
        }
        tasks.append((
            run_id, clean_params, phase,
            start_year, end_year,
            data_dir, max_year_workers,
        ))

    done_count = 0
    with ThreadPoolExecutor(
        max_workers=concurrent_runs
    ) as executor:
        future_map = {
            executor.submit(_run_single, t): t[0]
            for t in tasks
        }
        for future in as_completed(future_map):
            run_id = future_map[future]
            done_count += 1
            try:
                result = future.result()
            except Exception:
                logger.exception(
                    "Future失敗: %s", run_id
                )
                continue

            if result and result["trades"] > 0:
                with _csv_lock:
                    append_result(
                        output_path, result,
                        write_header,
                    )
                    write_header = False

                elapsed_total = time.time() - t_start
                avg = elapsed_total / done_count
                eta = avg * (total - done_count)
                logger.info(
                    "OK [%d/%d]: %s | tr=%d "
                    "PF=%.2f DD=%.2f%% "
                    "AnnRet=%.1f%% Score=%.4f "
                    "| %.1fs (ETA: %.0fm)",
                    done_count, total, run_id,
                    result["trades"],
                    result["profit_factor"],
                    result["max_drawdown"],
                    result["annual_return"],
                    result["composite_score"],
                    result["elapsed_sec"],
                    eta / 60,
                )


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(
        description="M1必須パラメータ探索"
    )
    parser.add_argument(
        "--phase", type=int, required=True,
        choices=[1, 2, 3],
    )
    parser.add_argument(
        "--data-dir", default="data",
    )
    parser.add_argument(
        "--max-year-workers", type=int, default=24,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
    )
    parser.add_argument(
        "--concurrent-runs", type=int, default=1,
        help="同時実行バックテスト数",
    )
    parser.add_argument(
        "--worker-id", type=int, default=0,
        help="ワーカーID (0-based)",
    )
    parser.add_argument(
        "--num-workers", type=int, default=1,
        help="総ワーカー数",
    )
    args = parser.parse_args()

    run_phase(
        phase=args.phase,
        data_dir=args.data_dir,
        max_year_workers=args.max_year_workers,
        dry_run=args.dry_run,
        concurrent_runs=args.concurrent_runs,
        worker_id=args.worker_id,
        num_workers=args.num_workers,
    )


if __name__ == "__main__":
    main()
