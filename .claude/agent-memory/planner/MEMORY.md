# Planner Agent Memory

## Project: AutoTraderV4

### Architecture: Fundamental Data Flow
- **Event CSV**: `data/fundamental/events_YYYY.csv` (~4600 rows/year, all currencies)
- **News CSV**: `data/fundamental/news/news_YYYY.csv` (GDELT, ~8700/day for busy days)
- **RSS News CSV**: `data/fundamental/news_rss_YYYY.csv` (filtered FX sources, with content)
- **LLM Context CSV**: `data/fundamental/llm_context_SYMBOL_YYYY.csv` (monthly, 12 rows/year) -- being replaced by daily
- **LLM Generator**: `autotrader/adapters/fundamental/llm_context_generator.py` (monthly batch)
- **Backtest Provider**: `autotrader/adapters/fundamental/backtest_provider.py` (serves FundamentalContext)
- **Live Provider**: `autotrader/adapters/fundamental/memory.py` (FundamentalMemoryService, DB-backed)
- **Schemas**: `autotrader/adapters/fundamental/schemas.py` (FundamentalContext, EconomicEvent)
- **News Schemas**: `autotrader/adapters/fundamental/news_schemas.py` (NewsItem, CURRENCY_KEYWORDS)
- **LLM Settings**: `autotrader/config/llm_settings.py` (OllamaSettings, qwen3:14b)
- **Generation Script**: `scripts/generate_fundamental_llm.py`

### Key Usage Points
- FundamentalContext consumed in `backtest/runner.py` line ~1817 (high impact skip only)
- Live engine: `live/engine.py` uses FundamentalMemoryService.get_context_for_llm()
- trade_bot.py and position_manager.py do NOT directly reference fundamental data
- Fundamental data currently only used for event guard (skip before high impact events)
- HardGuard.check_high_impact_news() exists but receives data via dict context, NOT FundamentalContext
- SoftGuard has no fundamental checks at all
- PositionSizer uses SizingContext (core/interfaces/position_sizing.py) - no fundamental fields
- UnifiedTradeBot.generate_signal() takes (current_time, candle) - no fundamental param

### Phase 2 Design (tasks/phase2_design.md)
- Consumer-driven design: FundamentalContext fields derived from what consumers need
- Time decay model: exp(-2.0 * elapsed/convergence_hours) for event influence
- Holiday semantic separation: expected_volatility -> liquidity_factor for holidays
- Phase 2a: HardGuard + PositionSizer (minimal, low risk)
- Phase 2b: SoftGuard + TradeBot consensus + PositionManager (extended)
- Fallback chain: event_llm -> monthly_llm -> rule-based -> neutral

### Symbol-Currency Mapping
- Defined in multiple places (llm_context_generator.py, backtest_provider.py, normalizer.py)
- Should be consolidated into a single source

### Event LLM Data Location
- Actual files: `data/fundamental/llm_events/llm_events_USDJPY_YYYY.csv`
- run_backtest.py searches: `data/fundamental/llm_events_USDJPY_YYYY.csv` (BUG)
- Available years: 2010-2022, 2024-2025 (15 files, 2023 missing)
- CSV columns: event_time, currency, event_name, impact, actual, forecast, previous,
  surprise_score, direction_bias, convergence_hours, expected_volatility,
  trade_caution_level, is_holiday, summary

### Phase 2b Feature Flags (config.py lines 224-230)
- `fundamental_assessor_enabled`: FundamentalRiskAssessor + direction filter + threshold adjustment
- `fundamental_softguard_enabled`: SoftGuard penalty from assessment risk level
- `fundamental_pm_enabled`: PositionManager trailing SL multiplier adjustment
- All default False, fully wired in trade_bot.py, soft_guard.py, position_manager.py, runner.py
- Runner enables FundamentalMemory when assessor_enabled=True (line 1059)
