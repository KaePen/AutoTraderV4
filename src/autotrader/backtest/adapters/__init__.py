"""バックテストアダプター

CLI/WebUIそれぞれの入出力形式を統一実行エンジンに変換。
"""

from __future__ import annotations

from autotrader.backtest.adapters.cli import CLIAdapter
from autotrader.backtest.adapters.webui import WebUIAdapter

__all__ = ["CLIAdapter", "WebUIAdapter"]
