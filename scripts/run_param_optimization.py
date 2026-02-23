"""USDJPYパラメータ最適化スクリプト

6段階のステージでパラメータを段階的に探索する。
各ステージはCSV追記方式で中断・再開に対応。

Usage:
    python scripts/run_param_optimization.py --stage 0 --max-year-workers 24
    python scripts/run_param_optimization.py --stage 1 --max-year-workers 24
    ...
    python scripts/run_param_optimization.py --stage 5 --max-year-workers 24
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import random
import sys
import time
from pathlib import Path

# プロジェクトルートをパスに追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# TF優先順（低TF→高TF）
TF_ORDER = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]
TF_RANK = {tf: i for i, tf in enumerate(TF_ORDER)}

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- 固定パラメータ（全ステージ共通） ---
FIXED_PARAMS = {
    "max_positions": 1,
    "bonus_max_positions": 0,
    "use_dynamic_lot": True,
    "base_risk_pct": 0.04,
    "max_risk_pct_absolute": 0.07,
    "max_lot_per_trade": 5.0,
}
INITIAL_BALANCE = 1_000_000.0

# IS/OOS期間
IS_YEARS = (2020, 2022)
OOS_YEARS = (2023, 2025)

# --- 結果CSV共通列 ---
RESULT_COLUMNS = [
    "run_id",
    "stage",
    "params_json",
    "trades",
    "win_rate",
    "non_loss_rate",
    "profit_factor",
    "net_profit",
    "max_drawdown",
    "sharpe_ratio",
    "annual_return",
    "composite_score",
    "elapsed_sec",
]


# ------------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------------

def composite_score(
    profit_factor: float,
    max_drawdown: float,
    win_rate: float,
) -> float:
    """複合スコアを計算する。

    Args:
        profit_factor: プロフィットファクター
        max_drawdown: 最大ドローダウン（%）
        win_rate: 勝率（%）

    Returns:
        float: 複合スコア
    """
    dd_factor = 1.0 - max_drawdown / 100.0
    if dd_factor < 0:
        dd_factor = 0.0
    wr_factor = min(1.0, win_rate / 50.0)
    return profit_factor * dd_factor * wr_factor


def resolve_regime_tf(combo: list[str]) -> str:
    """組み合わせからレジーム検出用TFを決定する。

    Args:
        combo: TF組み合わせリスト

    Returns:
        str: レジーム検出用TF
    """
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
    combo: list[str],
    regime_tf: str,
) -> list[str]:
    """組み合わせからHTF整合性チェック用TFリストを決定する。

    Args:
        combo: TF組み合わせリスト
        regime_tf: レジーム検出用TF

    Returns:
        list[str]: HTF整合性チェック用TFリスト
    """
    candidates = []
    if "H4" in combo:
        candidates.append("H4")
    if "D1" in combo:
        candidates.append("D1")
    if candidates:
        return candidates

    regime_rank = TF_RANK.get(regime_tf, 0)
    upper_tfs = sorted(
        [
            t for t in combo
            if TF_RANK.get(t, 0) >= regime_rank
        ],
        key=lambda t: TF_RANK.get(t, 0),
        reverse=True,
    )
    return upper_tfs[:2] if upper_tfs else [combo[-1]]


def make_run_id(stage: int, params: dict) -> str:
    """パラメータからユニークなrun_idを生成する。

    Args:
        stage: ステージ番号
        params: パラメータ辞書

    Returns:
        str: run_id
    """
    raw = f"s{stage}:" + str(sorted(params.items()))
    h = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"s{stage}_{h}"


def load_completed(output_path: Path) -> set[str]:
    """完了済みrun_idをCSVから読み込む。

    Args:
        output_path: 結果CSVパス

    Returns:
        set[str]: 完了済みrun_idの集合
    """
    completed = set()
    if not output_path.exists():
        return completed
    with open(output_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "run_id" in row:
                completed.add(row["run_id"])
    return completed


def append_result(
    output_path: Path,
    row: dict,
    write_header: bool = False,
) -> None:
    """結果を1行CSVに追記する。

    Args:
        output_path: 出力CSVパス
        row: 結果辞書
        write_header: ヘッダー書き込みフラグ
    """
    mode = "w" if write_header else "a"
    with open(
        output_path, mode, encoding="utf-8", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def load_top_n(csv_path: Path, n: int) -> list[dict]:
    """CSVから複合スコアTop Nのパラメータを読み込む。

    Args:
        csv_path: 結果CSVパス
        n: 取得件数

    Returns:
        list[dict]: Top Nのパラメータ辞書リスト
    """
    rows = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["composite_score"] = float(
                row.get("composite_score", 0)
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
        result.append(params)
    return result


def lhs_sample(
    param_ranges: dict[str, list],
    n_samples: int,
    seed: int = 42,
) -> list[dict]:
    """Latin Hypercube Sampling（簡易実装、scipy不要）。

    各パラメータの探索範囲から均等分割サンプリングを行う。

    Args:
        param_ranges: パラメータ名→値リストの辞書
        n_samples: サンプル数
        seed: 乱数シード

    Returns:
        list[dict]: サンプルパラメータ辞書のリスト
    """
    rng = random.Random(seed)
    param_names = list(param_ranges.keys())

    # 各パラメータの区間割り当て
    intervals: dict[str, list] = {}
    for name in param_names:
        values = param_ranges[name]
        n_vals = len(values)
        # n_samples分の区間を作成しシャッフル
        indices = [
            i % n_vals for i in range(n_samples)
        ]
        rng.shuffle(indices)
        intervals[name] = [values[idx] for idx in indices]

    samples = []
    for i in range(n_samples):
        sample = {}
        for name in param_names:
            sample[name] = intervals[name][i]
        samples.append(sample)

    return samples


# ------------------------------------------------------------------
# バックテスト実行
# ------------------------------------------------------------------

def run_backtest(
    params: dict,
    start_year: int,
    end_year: int,
    data_dir: str,
    max_year_workers: int,
) -> dict:
    """パラメータ辞書でバックテストを1回実行する。

    Args:
        params: 全パラメータ辞書
        start_year: 開始年
        end_year: 終了年
        data_dir: データディレクトリ
        max_year_workers: 年単位並列ワーカー数

    Returns:
        dict: 結果メトリクス
    """
    from autotrader.backtest.runner import (
        BacktestConfig,
        BacktestRunner,
    )
    from autotrader.decision.unified import UnifiedBotConfig
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )

    symbol = params.get("symbol", "USDJPY")

    # TF関連
    timeframes = params.get(
        "timeframes",
        ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
    )
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

    # UnifiedBotConfig構築
    bot_kwargs: dict = {
        "timeframes": timeframes,
        "regime_detection_tf": regime_tf,
        "htf_alignment_tfs": htf_tfs,
    }
    # 固定パラメータ適用
    bot_kwargs.update(FIXED_PARAMS)
    # 探索パラメータ適用（UnifiedBotConfigのフィールド）
    _bot_fields = {
        "consensus_threshold",
        "consensus_primary_weight",
        "consensus_entry_weight",
        "consensus_confirm_weight",
        "consensus_manage_weight",
        "consensus_other_weight",
        "tp_sl_ratio",
        "sl_min_pips",
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

    # PositionManagerConfig構築
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

    # BacktestConfig
    config = BacktestConfig.from_preset(symbol)
    config = BacktestConfig(
        symbol=config.symbol,
        initial_balance=INITIAL_BALANCE,
        max_positions=FIXED_PARAMS["max_positions"],
        bonus_max_positions=FIXED_PARAMS[
            "bonus_max_positions"
        ],
        spread_pips=config.spread_pips,
        slippage_pips=config.slippage_pips,
        pip_value=config.pip_value,
        commission_per_lot=config.commission_per_lot,
    )

    runner = BacktestRunner(
        data_dir=data_dir,
        config=config,
        verbose=False,
        log_to_file=False,
    )

    result = runner.run_unified(
        start_year=start_year,
        end_year=end_year,
        config=bot_config,
        pm_config=pm_config,
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
# 各ステージ定義
# ------------------------------------------------------------------

def stage0_tf_combos() -> list[dict]:
    """Stage 0: TF組み合わせ探索パラメータを生成する。

    Returns:
        list[dict]: パラメータ辞書リスト
    """
    combos = [
        ["M1", "M5", "M15", "H1", "H4"],
        ["M5", "M15", "H1", "H4"],
        ["M1", "M5", "M15", "H1", "H4", "D1"],
        ["M5", "M15", "H1", "H4", "D1"],
        ["M15", "H1", "H4"],
        ["M15", "H1", "H4", "D1"],
        ["M1", "M15", "H1", "H4"],
        ["M5", "M15", "M30", "H1", "H4"],
        ["M1", "M5", "M15", "H1", "H4", "H8"],
        ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
    ]
    params_list = []
    for combo in combos:
        regime_tf = resolve_regime_tf(combo)
        htf_tfs = resolve_htf_alignment(combo, regime_tf)
        params_list.append({
            "timeframes": combo,
            "regime_detection_tf": regime_tf,
            "htf_alignment_tfs": htf_tfs,
        })
    return params_list


def stage1_threshold(prev_csv: Path) -> list[dict]:
    """Stage 1: コンセンサス閾値スイープパラメータを生成する。

    Args:
        prev_csv: Stage 0の結果CSVパス

    Returns:
        list[dict]: パラメータ辞書リスト
    """
    top3 = load_top_n(prev_csv, 3)
    thresholds = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]
    params_list = []
    for base in top3:
        for th in thresholds:
            p = {
                "timeframes": base["timeframes"],
                "regime_detection_tf": base[
                    "regime_detection_tf"
                ],
                "htf_alignment_tfs": base[
                    "htf_alignment_tfs"
                ],
                "consensus_threshold": th,
            }
            params_list.append(p)
    return params_list


def stage2_weights(prev_csv: Path) -> list[dict]:
    """Stage 2: コンセンサス重みチューニングパラメータを生成する。

    Args:
        prev_csv: Stage 1の結果CSVパス

    Returns:
        list[dict]: パラメータ辞書リスト
    """
    top3 = load_top_n(prev_csv, 3)
    weight_ranges = {
        "consensus_primary_weight": [1.0, 2.0, 3.0, 4.0, 5.0],
        "consensus_entry_weight": [0.5, 1.0, 1.5, 2.5, 3.5],
        "consensus_confirm_weight": [1.0, 2.0, 3.0, 4.0, 5.0],
        "consensus_manage_weight": [0.5, 1.0, 1.5, 2.5],
        "consensus_other_weight": [0.5, 1.0, 2.0, 3.0],
    }
    samples = lhs_sample(weight_ranges, 25, seed=42)
    params_list = []
    for base in top3:
        for s in samples:
            p = {
                "timeframes": base["timeframes"],
                "regime_detection_tf": base[
                    "regime_detection_tf"
                ],
                "htf_alignment_tfs": base[
                    "htf_alignment_tfs"
                ],
                "consensus_threshold": base.get(
                    "consensus_threshold", 4.5
                ),
            }
            p.update(s)
            params_list.append(p)
    return params_list


def stage3_pm_params(prev_csv: Path) -> list[dict]:
    """Stage 3: ポジション管理パラメータを生成する。

    Args:
        prev_csv: Stage 2の結果CSVパス

    Returns:
        list[dict]: パラメータ辞書リスト
    """
    top5 = load_top_n(prev_csv, 5)
    pm_ranges = {
        "trailing_start_r": [1.0, 1.5, 2.0, 2.5],
        "trailing_atr_mult": [1.0, 1.5, 2.0, 2.5],
        "stag_exit_minutes": [60.0, 90.0, 120.0, 180.0],
        "partial_1r_ratio": [0.0, 0.05, 0.10, 0.15],
        "early_be_r": [0.3, 0.5, 0.7, 1.0],
        "signal_rev_ratio": [0.0, 0.25, 0.5, 0.75],
    }
    samples = lhs_sample(pm_ranges, 20, seed=123)
    params_list = []
    for base in top5:
        for s in samples:
            p = {
                "timeframes": base["timeframes"],
                "regime_detection_tf": base[
                    "regime_detection_tf"
                ],
                "htf_alignment_tfs": base[
                    "htf_alignment_tfs"
                ],
                "consensus_threshold": base.get(
                    "consensus_threshold", 4.5
                ),
            }
            # 重みパラメータ引き継ぎ
            for wk in [
                "consensus_primary_weight",
                "consensus_entry_weight",
                "consensus_confirm_weight",
                "consensus_manage_weight",
                "consensus_other_weight",
            ]:
                if wk in base:
                    p[wk] = base[wk]
            p.update(s)
            params_list.append(p)
    return params_list


def stage4_filters(prev_csv: Path) -> list[dict]:
    """Stage 4: フィルター・ペナルティ・SL/TPパラメータを生成する。

    Args:
        prev_csv: Stage 3の結果CSVパス

    Returns:
        list[dict]: パラメータ辞書リスト
    """
    top3 = load_top_n(prev_csv, 3)
    filter_ranges = {
        "tp_sl_ratio": [1.0, 1.2, 1.4, 1.6],
        "sl_min_pips": [8.0, 10.0, 12.0, 15.0],
        "range_day_bbw_threshold": [0.15, 0.20, 0.25],
        "range_day_score_premium": [0.3, 0.55, 0.8],
        "weak_hours_score_premium": [0.3, 0.5, 0.7],
        "sg_spread_penalty_rate": [0.05, 0.10, 0.20],
        "sg_off_hours_penalty": [0.10, 0.15, 0.25],
        "sg_volatility_penalty": [0.05, 0.10, 0.20],
        "sg_recent_loss_penalty": [0.10, 0.20, 0.30],
    }
    samples = lhs_sample(filter_ranges, 25, seed=456)
    params_list = []
    for base in top3:
        for s in samples:
            p = {
                "timeframes": base["timeframes"],
                "regime_detection_tf": base[
                    "regime_detection_tf"
                ],
                "htf_alignment_tfs": base[
                    "htf_alignment_tfs"
                ],
                "consensus_threshold": base.get(
                    "consensus_threshold", 4.5
                ),
            }
            # 重みパラメータ引き継ぎ
            for wk in [
                "consensus_primary_weight",
                "consensus_entry_weight",
                "consensus_confirm_weight",
                "consensus_manage_weight",
                "consensus_other_weight",
            ]:
                if wk in base:
                    p[wk] = base[wk]
            # PMパラメータ引き継ぎ
            for pk in [
                "trailing_start_r",
                "trailing_atr_mult",
                "stag_exit_minutes",
                "partial_1r_ratio",
                "early_be_r",
                "signal_rev_ratio",
            ]:
                if pk in base:
                    p[pk] = base[pk]
            p.update(s)
            params_list.append(p)
    return params_list


def stage5_oos(prev_csv: Path) -> list[dict]:
    """Stage 5: OOS最終検証パラメータを生成する。

    Top 3 + デフォルト設定のベースライン。

    Args:
        prev_csv: Stage 4の結果CSVパス

    Returns:
        list[dict]: パラメータ辞書リスト
    """
    top3 = load_top_n(prev_csv, 3)
    # デフォルトベースライン
    baseline = {
        "timeframes": [
            "M1", "M5", "M15", "M30",
            "H1", "H4", "H8", "D1",
        ],
        "regime_detection_tf": "H1",
        "htf_alignment_tfs": ["H4", "D1"],
        "_label": "baseline_default",
    }
    return top3 + [baseline]


# ------------------------------------------------------------------
# ステージ実行
# ------------------------------------------------------------------

STAGE_CONFIG = {
    0: {
        "name": "TF組み合わせ探索",
        "output": "reports/opt_stage0_tf_combos.csv",
        "generator": lambda _: stage0_tf_combos(),
        "prev_csv": None,
        "years": IS_YEARS,
    },
    1: {
        "name": "コンセンサス閾値スイープ",
        "output": "reports/opt_stage1_threshold.csv",
        "generator": stage1_threshold,
        "prev_csv": "reports/opt_stage0_tf_combos.csv",
        "years": IS_YEARS,
    },
    2: {
        "name": "コンセンサス重みチューニング",
        "output": "reports/opt_stage2_weights.csv",
        "generator": stage2_weights,
        "prev_csv": "reports/opt_stage1_threshold.csv",
        "years": IS_YEARS,
    },
    3: {
        "name": "ポジション管理パラメータ",
        "output": "reports/opt_stage3_pm_params.csv",
        "generator": stage3_pm_params,
        "prev_csv": "reports/opt_stage2_weights.csv",
        "years": IS_YEARS,
    },
    4: {
        "name": "フィルター・ペナルティ・SL/TP",
        "output": "reports/opt_stage4_filters.csv",
        "generator": stage4_filters,
        "prev_csv": "reports/opt_stage3_pm_params.csv",
        "years": IS_YEARS,
    },
    5: {
        "name": "OOS最終検証",
        "output": "reports/opt_stage5_oos_final.csv",
        "generator": stage5_oos,
        "prev_csv": "reports/opt_stage4_filters.csv",
        "years": OOS_YEARS,
    },
}


def run_stage(
    stage: int,
    data_dir: str,
    max_year_workers: int,
    dry_run: bool = False,
) -> None:
    """指定ステージを実行する。

    Args:
        stage: ステージ番号（0-5）
        data_dir: データディレクトリ
        max_year_workers: 年単位並列ワーカー数
        dry_run: 実行せずにパラメータ一覧を表示
    """
    cfg = STAGE_CONFIG[stage]
    output_path = Path(cfg["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    start_year, end_year = cfg["years"]

    logger.info(
        "=== Stage %d: %s (%d-%d) ===",
        stage, cfg["name"], start_year, end_year,
    )

    # パラメータ生成
    prev_csv = (
        Path(cfg["prev_csv"]) if cfg["prev_csv"] else None
    )
    if prev_csv and not prev_csv.exists():
        logger.error(
            "前ステージの結果が見つかりません: %s",
            prev_csv,
        )
        sys.exit(1)

    params_list = cfg["generator"](prev_csv)

    logger.info("パラメータ数: %d", len(params_list))

    if dry_run:
        for i, p in enumerate(params_list, 1):
            # 内部メタデータを除外して表示
            display = {
                k: v for k, v in p.items()
                if not k.startswith("_")
            }
            logger.info(
                "%3d. %s", i, json.dumps(display, ensure_ascii=False),
            )
        logger.info("合計: %d runs", len(params_list))
        return

    # 完了済みチェック（中断再開対応）
    completed = load_completed(output_path)
    runs = []
    for p in params_list:
        rid = make_run_id(stage, {
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

    write_header = not output_path.exists()
    total = len(runs)
    t_start = time.time()

    for idx, (run_id, params) in enumerate(runs, 1):
        # メタデータ除外
        clean_params = {
            k: v for k, v in params.items()
            if not k.startswith("_")
        }
        logger.info(
            "--- [%d/%d] %s ---", idx, total, run_id,
        )

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

            cs = composite_score(
                metrics["profit_factor"],
                metrics["max_drawdown"],
                metrics["win_rate"],
            )

            row = {
                "run_id": run_id,
                "stage": stage,
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
            append_result(output_path, row, write_header)
            write_header = False

            # 進捗表示
            elapsed_total = time.time() - t_start
            avg = elapsed_total / idx
            eta = avg * (total - idx)
            logger.info(
                "完了: %s | 取引=%d 勝率=%.1f%% "
                "PF=%.2f 利益=¥%s DD=%.2f%% "
                "Score=%.4f | %.1fs (ETA: %.0f分)",
                run_id,
                metrics["trades"],
                metrics["win_rate"],
                metrics["profit_factor"],
                f"{metrics['net_profit']:,.0f}",
                metrics["max_drawdown"],
                cs,
                elapsed,
                eta / 60,
            )

        except KeyboardInterrupt:
            logger.info(
                "\n中断（%d/%d完了）。再実行で続行可能。",
                idx - 1, total,
            )
            sys.exit(0)

        except Exception:
            logger.exception(
                "エラー: %s（スキップ）", run_id,
            )

    elapsed_total = time.time() - t_start
    logger.info(
        "\nStage %d 完了: %d runs (%.1f分)",
        stage, total, elapsed_total / 60,
    )
    logger.info("結果: %s", output_path)

    # Top 5表示
    top5 = load_top_n(output_path, 5)
    logger.info("\n--- Top 5 ---")
    for i, t in enumerate(top5, 1):
        logger.info(
            "%d. score=%.4f run=%s",
            i,
            t.get("_composite_score", 0),
            t.get("_run_id", ""),
        )


def generate_summary(data_dir: str) -> None:
    """最終サマリーレポートを生成する。

    Args:
        data_dir: データディレクトリ
    """
    summary_path = Path("reports/opt_summary.txt")
    oos_csv = Path(STAGE_CONFIG[5]["output"])
    is_csv = Path(STAGE_CONFIG[4]["output"])

    if not oos_csv.exists():
        logger.error("OOS結果が見つかりません: %s", oos_csv)
        return

    oos_rows = []
    with open(oos_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            oos_rows.append(row)

    is_top3 = load_top_n(is_csv, 3) if is_csv.exists() else []

    lines = [
        "=" * 60,
        "USDJPY パラメータ最適化 最終サマリー",
        "=" * 60,
        "",
        "--- OOS検証結果 (2023-2025) ---",
        f"{'Run ID':<20s} {'Trades':>7s} {'WinRate':>8s} "
        f"{'PF':>6s} {'Profit':>12s} {'DD%':>7s} "
        f"{'Score':>8s}",
        "-" * 70,
    ]

    for r in oos_rows:
        lines.append(
            f"{r.get('run_id', ''):<20s} "
            f"{r.get('trades', ''):>7s} "
            f"{r.get('win_rate', ''):>7s}% "
            f"{r.get('profit_factor', ''):>6s} "
            f"{r.get('net_profit', ''):>11s} "
            f"{r.get('max_drawdown', ''):>6s}% "
            f"{r.get('composite_score', ''):>8s}"
        )

    # IS vs OOS比較
    if is_top3 and len(oos_rows) >= 2:
        lines.extend([
            "",
            "--- IS vs OOS 比較 ---",
            f"{'Metric':<20s} {'IS Top1':>12s} "
            f"{'OOS Top1':>12s} {'Ratio':>8s}",
            "-" * 55,
        ])
        is_best = is_top3[0]
        oos_best = oos_rows[0]
        for metric in [
            "win_rate", "profit_factor",
            "net_profit", "max_drawdown",
        ]:
            is_val = float(is_best.get(metric, 0))
            oos_val = float(oos_best.get(metric, 0))
            ratio = (
                oos_val / is_val if is_val != 0 else 0
            )
            lines.append(
                f"{metric:<20s} {is_val:>12.2f} "
                f"{oos_val:>12.2f} {ratio:>7.2f}x"
            )

    # Top 3パラメータ詳細
    lines.extend(["", "--- Top 3 全パラメータ ---"])
    for i, r in enumerate(oos_rows[:3], 1):
        params = json.loads(r.get("params_json", "{}"))
        lines.append(f"\n#{i} ({r.get('run_id', '')})")
        for k, v in sorted(params.items()):
            if not k.startswith("_"):
                lines.append(f"  {k}: {v}")

    summary = "\n".join(lines)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary, encoding="utf-8")
    logger.info("サマリー出力: %s", summary_path)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """CLI引数をパースする。

    Returns:
        argparse.Namespace: パース結果
    """
    parser = argparse.ArgumentParser(
        description="USDJPYパラメータ最適化"
    )
    parser.add_argument(
        "--stage",
        type=int,
        required=True,
        choices=range(6),
        help="実行ステージ（0-5）",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="データディレクトリ（デフォルト: data）",
    )
    parser.add_argument(
        "--max-year-workers",
        type=int,
        default=24,
        help="年単位並列ワーカー数（デフォルト: 24）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実行せずにパラメータ一覧を表示",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Stage 5完了後にサマリーを生成",
    )
    return parser.parse_args()


def main() -> None:
    """メインエントリーポイント"""
    args = parse_args()

    if args.summary:
        generate_summary(args.data_dir)
        return

    run_stage(
        stage=args.stage,
        data_dir=args.data_dir,
        max_year_workers=args.max_year_workers,
        dry_run=args.dry_run,
    )

    # Stage 5完了後は自動でサマリー生成
    if args.stage == 5 and not args.dry_run:
        generate_summary(args.data_dir)


if __name__ == "__main__":
    main()
