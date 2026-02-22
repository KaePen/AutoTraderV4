"""インポート診断"""
from __future__ import annotations
import sys
from pathlib import Path

# worktreeのsrcを優先
sys.path.insert(0, str(Path(__file__).parent / "tmp" / "feat_quality-based-max-positions" / "src"))
sys.path.insert(0, str(Path(__file__).parent / "src"))

OUT = Path(__file__).parent / "diag_result.txt"

def log(lines: list[str], msg: str) -> None:
    print(msg)
    lines.append(msg)
    OUT.write_text("\n".join(lines), encoding="utf-8")

def main() -> None:
    lines: list[str] = []
    log(lines, "=== インポート診断 ===")
    log(lines, f"sys.path[0]: {sys.path[0]}")
    log(lines, f"sys.path[1]: {sys.path[1]}")

    from autotrader.backtest.runner import BacktestConfig
    log(lines, f"BacktestConfig file: {BacktestConfig.__module__}")

    # bonus_max_positionsが存在するか
    cfg = BacktestConfig(symbol="USDJPY", initial_balance=1_000_000.0, max_positions=1)
    log(lines, f"bonus_max_positions exists: {hasattr(cfg, 'bonus_max_positions')}")
    if hasattr(cfg, 'bonus_max_positions'):
        log(lines, f"bonus_max_positions={cfg.bonus_max_positions}")

    # bonus付きで作成
    try:
        cfg2 = BacktestConfig(
            symbol="USDJPY", initial_balance=1_000_000.0,
            max_positions=1, bonus_max_positions=1, bonus_score_threshold=7.0
        )
        log(lines, f"bonus config OK: bonus={cfg2.bonus_max_positions} t={cfg2.bonus_score_threshold}")
    except Exception as e:
        log(lines, f"bonus config ERROR: {e}")

    log(lines, "=== DONE ===")


if __name__ == "__main__":
    main()
