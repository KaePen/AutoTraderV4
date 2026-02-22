"""ニュースデータスキーマ定義

ニュースアイテム・ニュースソースのデータクラス。
GDELT収集データおよびRSSフィードデータに共通して使用する。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class NewsSource(str, Enum):
    """ニュースデータのソース種別"""

    GDELT = "gdelt"
    RSS = "rss"


@dataclass
class NewsItem:
    """ニュースアイテムデータ

    GDELTまたはRSSから取得した個別ニュース記事。

    Args:
        news_id: 一意ID（gdelt_<hash> または rss_<hash>）
        published_at: 公開日時（UTC aware）
        title: 記事タイトル
        source_name: ニュースソース名（"Reuters", "Bloomberg" 等）
        source_url: 記事URL
        currencies: 関連通貨コードリスト（["USD", "JPY"] 等）
        source_type: データソース種別
        snippet: 本文冒頭200文字程度（任意）
    """

    news_id: str
    published_at: datetime
    title: str
    source_name: str
    source_url: str
    currencies: list[str]
    source_type: NewsSource
    snippet: str | None = None

    @classmethod
    def make_id(
        cls, source_type: NewsSource, url: str
    ) -> str:
        """URLからニュースIDを生成

        Args:
            source_type: ソース種別
            url: 記事URL

        Returns:
            str: ニュースID文字列
        """
        h = hashlib.md5(url.encode(), usedforsecurity=False)
        return f"{source_type.value}_{h.hexdigest()[:12]}"
