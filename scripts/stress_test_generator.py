"""ストレステスト用キュージョブ自動生成

5フェーズのストレステストジョブを生成し、backtest_queue.jsonに書き出す。
Phase 5（モンテカルロ）はBT不要のため含まない。

使用方法:
    uv run python scripts/stress_test_generator.py --phases 1 2
    uv run python scripts/stress_test_generator.py --phases 3
    uv run python scripts/stress_test_generator.py  # 全フェーズ
    uv run python scripts/stress_test_generator.py --dry-run  # 確認のみ
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from autotrader.config.paths import get_queue_file

# 採用構成: 8ペア
SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "EURUSD", "GBPUSD",
]
JPY_SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY",
]
USD_SYMBOLS = ["EURUSD", "GBPUSD"]

# OOS期間（最適化バイアスのない条件で耐性を測る）
YEARS = "2020-2022"

# 基本マルチペア設定
BASE_MULTI_PAIR_CONFIG = {
    "name": "STRESS",
    "global_max_positions": 4,
    "per_pair_max_positions": 1,
    "global_max_exposure_lot": 5.0,
    "base_risk_pct": 0.005,
    "consensus_threshold": 18.0,
    "spread_multiplier": 1.0,
    "max_same_direction_jpy": 3,
    "test_name": "",
}


def _build_overrides(
    bt_overrides: dict | None = None,
    bot_overrides: dict | None = None,
    pm_overrides: dict | None = None,
) -> dict:
    """overrides辞書を構築"""
    overrides: dict = {}
    if bot_overrides:
        overrides["bot"] = bot_overrides
    if pm_overrides:
        overrides["pm"] = pm_overrides
    if bt_overrides:
        overrides["backtest"] = bt_overrides
    return overrides


def _multi_job(
    job_id: str,
    description: str,
    *,
    bt_overrides: dict | None = None,
    bot_overrides: dict | None = None,
    pm_overrides: dict | None = None,
    symbols: list[str] | None = None,
    years: str = YEARS,
    multi_pair_config: dict | None = None,
) -> dict:
    """マルチペアジョブの雛形を生成"""
    mpc = dict(BASE_MULTI_PAIR_CONFIG)
    if multi_pair_config:
        mpc.update(multi_pair_config)

    return {
        "id": job_id,
        "type": "multi_pair",
        "symbols": symbols or SYMBOLS,
        "years": years,
        "description": f"[STRESS] {description}",
        "overrides": _build_overrides(
            bt_overrides, bot_overrides, pm_overrides,
        ),
        "multi_pair_config": mpc,
    }


# シングルペア用デフォルトシンボル
SINGLE_SYMBOL = "USDJPY"


def _single_job(
    job_id: str,
    description: str,
    *,
    bt_overrides: dict | None = None,
    bot_overrides: dict | None = None,
    pm_overrides: dict | None = None,
    symbol: str = SINGLE_SYMBOL,
    years: str = YEARS,
) -> dict:
    """シングルペアジョブの雛形を生成"""
    return {
        "id": job_id,
        "type": "single",
        "symbol": symbol,
        "years": years,
        "description": f"[STRESS] {description}",
        "overrides": _build_overrides(
            bt_overrides, bot_overrides, pm_overrides,
        ),
    }


def generate_phase1_jobs() -> list[dict]:
    """Phase 1: 約定系ストレス（~10ジョブ、シングルペア）

    固定/ランダムスリッページ、約定遅延、約定失敗、部分約定。
    マルチペアではスロット解放による副作用があるためシングルで実施。
    """
    jobs = []

    # 固定スリッページ追加
    for extra in [0.5, 1.0, 2.0]:
        jobs.append(_single_job(
            f"ss_p1_slip_fixed_{extra}",
            f"P1 固定スリッページ +{extra}pips",
            bt_overrides={"slippage_extra_pips": extra},
        ))

    # ランダムスリッページ
    for rnd_max in [1.0, 2.0]:
        jobs.append(_single_job(
            f"ss_p1_slip_rnd_{rnd_max}",
            f"P1 ランダムスリッページ 0-{rnd_max}pips",
            bt_overrides={"slippage_random_max_pips": rnd_max},
        ))

    # エントリー遅延
    for delay in [1, 3]:
        jobs.append(_single_job(
            f"ss_p1_delay_{delay}",
            f"P1 エントリー遅延 +{delay}本",
            bt_overrides={"entry_delay_bars": delay},
        ))

    # 約定失敗
    for rate in [0.05, 0.10]:
        pct = int(rate * 100)
        jobs.append(_single_job(
            f"ss_p1_fail_{pct}pct",
            f"P1 約定失敗率 {pct}%",
            bt_overrides={"fill_failure_rate": rate},
        ))

    # 部分約定
    jobs.append(_single_job(
        "ss_p1_partial_50pct",
        "P1 部分約定 50%",
        bt_overrides={"partial_fill_ratio": 0.5},
    ))

    return jobs


def generate_phase2_jobs() -> list[dict]:
    """Phase 2: 入力ノイズ（~8ジョブ、シングルペア）

    エントリー遅延、価格ノイズ、シグナルスキップ。
    トレードロジック共通のためUSDJPY単体で十分。
    """
    jobs = []

    # 価格ノイズ
    for noise in [0.5, 1.0]:
        jobs.append(_single_job(
            f"ss_p2_noise_{noise}",
            f"P2 価格ノイズ ±{noise}pips",
            bt_overrides={"price_noise_pips": noise},
        ))

    # シグナルスキップ
    for skip in [0.05, 0.10]:
        pct = int(skip * 100)
        jobs.append(_single_job(
            f"ss_p2_skip_{pct}pct",
            f"P2 シグナルスキップ {pct}%",
            bt_overrides={"signal_skip_rate": skip},
        ))

    # 複合: ランダムスリッページ + 価格ノイズ
    jobs.append(_single_job(
        "ss_p2_combined_light",
        "P2 複合ノイズ(軽) slip_rnd=0.5 + noise=0.5",
        bt_overrides={
            "slippage_random_max_pips": 0.5,
            "price_noise_pips": 0.5,
        },
    ))
    jobs.append(_single_job(
        "ss_p2_combined_heavy",
        "P2 複合ノイズ(重) slip_rnd=1.0 + noise=1.0 + skip=5%",
        bt_overrides={
            "slippage_random_max_pips": 1.0,
            "price_noise_pips": 1.0,
            "signal_skip_rate": 0.05,
        },
    ))

    # スプレッド倍率（既存機能活用）
    for mult in [1.5, 2.0]:
        jobs.append(_single_job(
            f"ss_p2_spread_x{mult}",
            f"P2 スプレッド {mult}倍",
            bt_overrides={"spread_multiplier": mult},
        ))

    return jobs


def generate_phase3_jobs() -> list[dict]:
    """Phase 3: パラメータ耐性（~50ジョブ、シングルペア）

    10パラメータ × 5水準 (0.8/0.9/1.0/1.1/1.2)。
    トレードロジック共通のためUSDJPY単体で十分。
    """
    # パラメータ名、ベース値、overridesキー
    params: list[tuple[str, float, str, str]] = [
        # (表示名, ベース値, override先, フィールド名)
        ("consensus_threshold", 18.0, "bot", "consensus_threshold"),
        ("bca_min_edge", 0.60, "bot", "bca_min_edge"),
        ("trend_strength_max", 0.7, "bot", "trend_strength_max"),
        ("base_risk_pct", 0.005, "bot", "base_risk_pct"),
        ("max_lot_per_trade", 5.0, "bot", "max_lot_per_trade"),
        ("penalty_cap", 0.3, "bot", "penalty_cap"),
        ("sg_off_hours_penalty", 0.5, "bot", "sg_off_hours_penalty"),
        ("trailing_start_r", 0.5, "pm", "trailing_start_r"),
        ("trailing_atr_mult", 2.0, "pm", "trailing_atr_multiplier"),
        ("stagnation_minutes", 120.0, "pm", "stagnation_exit_minutes"),
    ]

    multipliers = [0.8, 0.9, 1.0, 1.1, 1.2]
    jobs = []

    for name, base_val, target, field in params:
        for mult in multipliers:
            val = round(base_val * mult, 6)
            mult_str = f"{mult:.1f}x"
            job_id = f"ss_p3_{name}_{mult_str}"
            desc = f"P3 {name}={val} ({mult_str})"

            overrides: dict = {}
            if target == "bot":
                overrides = {"bot": {field: val}}
            elif target == "pm":
                overrides = {"pm": {field: val}}

            jobs.append(_single_job(
                job_id,
                desc,
                bot_overrides=overrides.get("bot"),
                pm_overrides=overrides.get("pm"),
            ))

    return jobs


def generate_phase4_jobs() -> list[dict]:
    """Phase 4: レジーム破壊（~6ジョブ）

    通貨分離、期間分離、フィルタ無効化
    """
    jobs = []

    # JPY系のみ
    jobs.append(_multi_job(
        "ss_p4_jpy_only",
        "P4 JPY系のみ 6ペア",
        symbols=JPY_SYMBOLS,
        multi_pair_config={"global_max_positions": 4},
    ))

    # USD系のみ
    jobs.append(_multi_job(
        "ss_p4_usd_only",
        "P4 USD系のみ 2ペア",
        symbols=USD_SYMBOLS,
        multi_pair_config={"global_max_positions": 2},
    ))

    # 高ボラ期間（2020年コロナショック）
    jobs.append(_multi_job(
        "ss_p4_high_vol_2020",
        "P4 高ボラ期間 2020年",
        years="2020",
    ))

    # 低ボラ期間（2014年）
    jobs.append(_multi_job(
        "ss_p4_low_vol_2014",
        "P4 低ボラ期間 2014年",
        years="2014",
    ))

    # 全フィルタ無効化
    jobs.append(_multi_job(
        "ss_p4_no_filters",
        "P4 全フィルタ無効化",
        bot_overrides={
            "regime_threshold_enabled": False,
            "volume_filter_enabled": False,
            "weak_hours_enabled": False,
            "htf_score_filter_enabled": False,
            "bca_enabled": False,
        },
    ))

    # フィルタ最大強化
    jobs.append(_multi_job(
        "ss_p4_max_filters",
        "P4 フィルタ最大強化",
        bot_overrides={
            "sg_off_hours_penalty": 1.0,
            "sg_volatility_penalty": 0.15,
            "sg_spread_penalty_rate": 0.5,
            "volume_filter_threshold": 2.0,
            "volume_filter_penalty": 1.5,
        },
    ))

    return jobs


def generate_jobs(
    phases: list[int] | None = None,
) -> list[dict]:
    """指定フェーズのジョブを生成

    Args:
        phases: 生成するフェーズ番号リスト（Noneで全フェーズ）

    Returns:
        ジョブ定義のリスト
    """
    if phases is None:
        phases = [1, 2, 3, 4]

    generators = {
        1: generate_phase1_jobs,
        2: generate_phase2_jobs,
        3: generate_phase3_jobs,
        4: generate_phase4_jobs,
    }

    jobs: list[dict] = []
    for p in phases:
        gen = generators.get(p)
        if gen is None:
            print(f"警告: Phase {p} は未定義（スキップ）")
            continue
        phase_jobs = gen()
        jobs.extend(phase_jobs)
        print(f"Phase {p}: {len(phase_jobs)} ジョブ生成")

    return jobs


def write_to_queue(
    jobs: list[dict],
    *,
    append: bool = True,
) -> None:
    """ジョブをbacktest_queue.jsonに書き出す

    Args:
        jobs: ジョブ定義リスト
        append: 既存キューに追加（Falseで上書き）
    """
    queue_file = get_queue_file()
    existing_jobs: list[dict] = []

    if append and queue_file.exists():
        data = json.loads(
            queue_file.read_text(encoding="utf-8"),
        )
        existing_jobs = data.get("jobs", [])

    all_jobs = existing_jobs + jobs
    queue_file.write_text(
        json.dumps(
            {"jobs": all_jobs},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"キュー書き出し完了: {len(jobs)} ジョブ追加"
        f"（合計 {len(all_jobs)} ジョブ）",
    )
    print(f"  → {queue_file}")


def main() -> None:
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(
        description="ストレステスト用キュージョブ生成",
    )
    parser.add_argument(
        "--phases",
        type=int,
        nargs="+",
        default=None,
        help="生成するフェーズ番号（1-4、省略で全フェーズ）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ジョブ内容を表示するのみ（書き出さない）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存キューを上書き（デフォルトは追加）",
    )
    args = parser.parse_args()

    jobs = generate_jobs(args.phases)

    if not jobs:
        print("生成ジョブなし")
        return

    print(f"\n合計: {len(jobs)} ジョブ")

    if args.dry_run:
        print("\n--- ジョブ一覧 (dry-run) ---")
        for j in jobs:
            print(
                f"  {j['id']}: {j['description']}"
                f"  ({j.get('years', YEARS)})",
            )
        return

    write_to_queue(jobs, append=not args.overwrite)


if __name__ == "__main__":
    main()
