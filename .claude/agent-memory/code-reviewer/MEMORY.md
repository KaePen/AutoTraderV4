# Code Reviewer Memory

## Project Patterns

### LLM Generator Architecture
- `llm_generator_base.py`: 3-stage JSON fallback: direct parse -> code block -> brace regex
- Brace regex (stage 3) handles 1-level nesting only; primary paths (stages 1-2) handle unlimited nesting
- `_clip(val, lo, hi, default)`: returns `default` when val is None/non-numeric, otherwise clamps to [lo, hi]
- Default values for no-event days come from `_default_event_result()` which bypasses `_clip` entirely

### Test Coverage Gaps (as of PR #283)
- `test_llm_generator_base.py`: no test for nested JSON regex (the fix in PR #283)
- `test_llm_event_generator.py`: no test for convergence_hours lower-bound clip (0.3 -> 0.5)
- `scripts/generate_fundamental_llm.py`: no unit tests at all

### Common Review Patterns
- Nested function inside loop is a Python anti-pattern; moving to function scope (not just out of loop) is ideal
- `argparse subparsers required=True` supported since Python 3.7; project requires >=3.12, so safe
- When regex matches "first occurrence", check whether it matches outer or inner structure on 2-deep nesting
- Lazy imports (`from x import y` inside a method) are forbidden by project rules (coding-style2.md); move to top-level
- `_news_llm_by_date` dict is symbol-agnostic (flat date key): acceptable when provider is single-symbol, latent bug if multi-symbol reuse ever added
- run_backtest.py: `--fundamental-phase2b` sets `args.fundamental = True` AFTER the `_fundamental_csvs` block runs — pre-existing ordering bug (exists in main)
