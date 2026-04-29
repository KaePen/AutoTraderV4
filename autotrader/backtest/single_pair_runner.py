"""単独ペア統合バックテスト（薄いラッパ）

handover.md 推奨パターンを autotrader/backtest/ 内で API 化した。

Usage:
    from autotrader.backtest.single_pair_runner import (
        SinglePairConfig, run_single_pair
    )

    cfg = SinglePairConfig(symbol="USDJPY", start_year=2025, end_year=2026)
    summary = run_single_pair(cfg)
    print(summary)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autotrader.backtest.runner import BacktestConfig, BacktestRunner
from autotrader.backtest.tick_simulator import TickSimConfig
from autotrader.config.config_loader import ConfigLoader
from autotrader.decision.unified import UnifiedBotConfig
from autotrader.decision.unified.risk.position_manager import (
    PositionManagerConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class SinglePairConfig:
    """単独ペアBT設定"""
    symbol: str
    start_year: int = 2025
    end_year: int = 2026
    use_tick_sim: bool = True
    sequential: bool = True
    period_start: datetime | None = None
    period_end: datetime | None = None


def run_single_pair(
    config: SinglePairConfig,
    bot_config: UnifiedBotConfig | None = None,
    pm_config: PositionManagerConfig | None = None,
) -> dict[str, Any]:
    """単独ペアで BT を実行し、サマリ dict を返す"""
    if bot_config is None or pm_config is None:
        loader = ConfigLoader()
        bot_config, pm_config = loader.load_preset_config(config.symbol)

    tick_cfg = TickSimConfig(enabled=config.use_tick_sim)
    bt_cfg = BacktestConfig.from_preset(config.symbol, tick_sim_config=tick_cfg)
    runner = BacktestRunner(config=bt_cfg, verbose=False)

    t0 = time.time()
    result = runner.run_unified(
        start_year=config.start_year,
        end_year=config.end_year,
        config=bot_config,
        pm_config=pm_config,
        sequential=config.sequential,
        period_start=config.period_start,
        period_end=config.period_end,
    )
    elapsed = time.time() - t0

    return {
        "symbol": config.symbol,
        "period": f"{config.start_year}-{config.end_year}",
        "elapsed_sec": round(elapsed, 1),
        "trades": getattr(result, "trades", 0),
        "win_rate": getattr(result, "win_rate", 0.0),
        "non_loss_rate": getattr(result, "non_loss_rate", 0.0),
        "profit_factor": getattr(result, "profit_factor", 0.0),
        "sharpe_ratio": getattr(result, "sharpe_ratio", 0.0),
        "max_drawdown_pct": getattr(result, "max_drawdown", 0.0),
        "net_profit": getattr(result, "net_profit", 0.0),
        "annual_return": getattr(result, "annual_return", 0.0),
        "monthly_results_n": len(getattr(result, "monthly_results", []) or []),
    }
