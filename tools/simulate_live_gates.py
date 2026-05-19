"""BT の trades CSV を Live engine のエントリーゲートで再評価

multi_pair_runner は EntryGateChecker を通さないため、BT で 33件 entry した
うち、Live engine 側で何件が JPY SL Circuit Breaker / STAGNATION block /
spread gate / position limit で deny されるかを机上シミュレートする。

Usage:
    python tools/simulate_live_gates.py \\
        --trades-csv reports/live_replay_20260511_20260518_trades.csv \\
        --out reports/live_gate_simulation.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autotrader.config.config_loader import ConfigLoader
from autotrader.config.trading_params import get_preset
from autotrader.constraint.entry_gate import (
    EntryGateChecker,
    EntryGateContext,
)
from autotrader.core.entities import SignalType

logger = logging.getLogger("simulate_live_gates")

JPY_SL_CB_WINDOW_MIN = 60


def _to_sigtype(s: str) -> SignalType:
    s = s.upper()
    if s == "BUY":
        return SignalType.BUY
    if s == "SELL":
        return SignalType.SELL
    return SignalType.HOLD


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades-csv", required=True)
    parser.add_argument("--out", default="reports/live_gate_simulation.json")
    parser.add_argument(
        "--global-max-positions", type=int, default=4,
        help="Live engine の global_max_positions",
    )
    parser.add_argument(
        "--global-max-exposure-lot", type=float, default=10.0,
    )
    parser.add_argument(
        "--max-same-direction-jpy", type=int, default=3,
    )
    args = parser.parse_args()

    df = pd.read_csv(args.trades_csv)
    df["opened_at"] = pd.to_datetime(df["opened_at"], errors="coerce")
    df["closed_at"] = pd.to_datetime(df["closed_at"], errors="coerce")
    df = df.sort_values("opened_at").reset_index(drop=True)

    logger.info("trades %d 件 を時系列で再評価", len(df))

    gate = EntryGateChecker()
    loader = ConfigLoader()

    # 各シンボルの bot_config (max_positions, bonus*, spread_threshold)
    sym_cfg: dict[str, dict] = {}
    for sym in df["symbol"].unique():
        bot_config, _ = loader.load_preset_config(sym)
        preset = get_preset(sym)
        sym_cfg[sym] = {
            "max_positions": preset.max_positions,
            "bonus_max_positions": getattr(
                preset, "bonus_max_positions", 0,
            ),
            "bonus_score_threshold": getattr(
                preset, "bonus_score_threshold", 7.0,
            ),
            "spread_pips": preset.spread_pips,
            "sg_spread_threshold_pips": getattr(
                bot_config, "sg_spread_threshold_pips", None,
            ),
        }

    # オープン中ポジション (sym -> list of (direction, closed_at))
    open_by_sym: dict[str, list[tuple[str, pd.Timestamp]]] = (
        defaultdict(list)
    )
    closed_history: list[dict] = []

    def _clean_open(now: pd.Timestamp) -> None:
        """now までに closed_at が過ぎたポジションを除去"""
        for sym in list(open_by_sym.keys()):
            open_by_sym[sym] = [
                (d, ct) for d, ct in open_by_sym[sym]
                if pd.notna(ct) and ct > now
            ]

    results: list[dict] = []
    deny_counts: dict[str, int] = defaultdict(int)
    allow_count = 0

    for _, r in df.iterrows():
        opened = r["opened_at"]
        sym = r["symbol"]
        sigtype = _to_sigtype(r["signal_type"])
        score = float(r.get("consensus_score") or 0.0)

        _clean_open(opened)

        # ポジション集計
        sym_count = len(open_by_sym[sym])
        global_count = sum(len(v) for v in open_by_sym.values())
        global_exp = float(global_count)  # 1 lot/pos と仮定

        jpy_same = 0
        if sym.endswith("JPY"):
            for s, plist in open_by_sym.items():
                if not s.endswith("JPY"):
                    continue
                for d, _ in plist:
                    if d == sigtype.value:
                        jpy_same += 1

        # JPY SL CB: 直近 60 分以内の同方向 JPY SL
        jpy_sl_cb = False
        if sym.endswith("JPY"):
            cutoff = opened - timedelta(minutes=JPY_SL_CB_WINDOW_MIN)
            for h in reversed(closed_history):
                if not h["symbol"].endswith("JPY"):
                    continue
                if h["direction"] != sigtype.value:
                    continue
                if h["closed_at"] < cutoff:
                    break
                if h["exit_reason"] in ("SL_HIT", "STOP_LOSS"):
                    jpy_sl_cb = True
                    break

        # STAGNATION block: 直近の同ペア同方向 trade が STAGNATION か (1件のみ)
        stag_block = False
        for h in reversed(closed_history):
            if h["symbol"] != sym:
                continue
            if h["direction"] != sigtype.value:
                continue
            if h["exit_reason"] in ("STAGNATION", "STAG"):
                stag_block = True
            break

        sc = sym_cfg[sym]
        ctx = EntryGateContext(
            signal_direction=sigtype,
            consensus_score=score,
            symbol_position_count=sym_count,
            global_position_count=global_count,
            global_exposure_lot=global_exp,
            jpy_same_direction_count=jpy_same,
            max_positions=sc["max_positions"],
            bonus_max_positions=sc["bonus_max_positions"],
            bonus_score_threshold=sc["bonus_score_threshold"],
            global_max_positions=args.global_max_positions,
            global_max_exposure_lot=args.global_max_exposure_lot,
            max_same_direction_jpy=args.max_same_direction_jpy,
            is_jpy_pair=sym.endswith("JPY"),
            current_spread_pips=sc["spread_pips"],
            spread_threshold_pips=sc["sg_spread_threshold_pips"],
            dd_emergency_active=False,
            margin_usage_pct=0.0,
            margin_limit_pct=0.0,
            jpy_sl_circuit_breaker_active=jpy_sl_cb,
            prev_same_dir_exit_was_stag=stag_block,
        )
        gres = gate.evaluate(ctx)
        row = {
            "opened_at": str(opened),
            "symbol": sym,
            "direction": sigtype.value,
            "consensus_score": score,
            "regime": r.get("regime"),
            "sym_position_count": sym_count,
            "global_position_count": global_count,
            "jpy_same_direction_count": jpy_same,
            "jpy_sl_cb": jpy_sl_cb,
            "stag_block": stag_block,
            "allowed": gres.allowed,
            "deny_code": gres.deny_code,
            "deny_reason": gres.deny_reason,
            "actual_exit_reason": r.get("exit_reason"),
        }
        results.append(row)
        if gres.allowed:
            allow_count += 1
        else:
            deny_counts[gres.deny_code or "unknown"] += 1

        # この trade を「実際に発生したもの」として履歴に追加
        open_by_sym[sym].append((sigtype.value, r["closed_at"]))
        closed_history.append({
            "symbol": sym,
            "direction": sigtype.value,
            "closed_at": r["closed_at"],
            "exit_reason": r.get("exit_reason"),
        })

    out = {
        "total_trades": len(df),
        "allowed": allow_count,
        "denied": len(df) - allow_count,
        "deny_breakdown": dict(deny_counts),
        "rows": results,
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(out, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n=== Saved: {args.out} ===")
    print(f"total: {out['total_trades']}, allowed: {out['allowed']}, "
          f"denied: {out['denied']}")
    print(f"deny breakdown: {dict(deny_counts)}")
    print()
    print("=== denied rows ===")
    for r in results:
        if not r["allowed"]:
            print(
                f"  {r['opened_at']} {r['symbol']:7s} {r['direction']:4s} "
                f"score={r['consensus_score']:5.2f} "
                f"deny={r['deny_code']:25s} reason={r['deny_reason']}"
            )


if __name__ == "__main__":
    main()
