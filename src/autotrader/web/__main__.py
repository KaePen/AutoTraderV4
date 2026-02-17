"""WebUIサーバー起動エントリーポイント

使用方法:
    python -m autotrader.web
"""
from __future__ import annotations

import logging

import uvicorn

# ログ設定（uvicorn起動前に設定）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from autotrader.web.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
