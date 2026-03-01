"""ファンダメンタルデータルーター

ニュース一覧・経済カレンダーのREST APIを提供する。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request

from autotrader.web.dependencies import (
    get_fundamental_service,
)
from autotrader.web.middleware import limiter
from autotrader.web.schemas import ApiResponse
from autotrader.web.schemas.responses import (
    EconomicEventResponse,
    FundamentalCalendarResponse,
    FundamentalNewsResponse,
    NewsItemResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _symbol_to_currencies(symbol: str) -> list[str]:
    """シンボルから通貨コードリストを抽出

    Args:
        symbol: 通貨ペアシンボル（例: USDJPY）

    Returns:
        list[str]: 通貨コードリスト
    """
    if len(symbol) == 6:
        return [symbol[:3].upper(), symbol[3:].upper()]
    return []


@router.get(
    "/fundamental/news",
    response_model=ApiResponse[FundamentalNewsResponse],
)
@limiter.limit("60/minute")
async def get_fundamental_news(
    request: Request,
    symbol: str = Query(default="USDJPY"),
    limit: int = Query(default=30, ge=1, le=100),
    svc=Depends(get_fundamental_service),
) -> ApiResponse[FundamentalNewsResponse]:
    """ニュース一覧を取得

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
        limit: 取得件数上限
        svc: FundamentalDataService

    Returns:
        ApiResponse[FundamentalNewsResponse]: ニュース一覧
    """
    items: list[NewsItemResponse] = []
    if svc is not None:
        raw_items = svc.get_news_buffer(symbol)
        sorted_items = sorted(
            raw_items,
            key=lambda n: getattr(
                n, "published_at", datetime.min
            ),
            reverse=True,
        )[:limit]
        for n in sorted_items:
            items.append(
                NewsItemResponse(
                    news_id=getattr(n, "news_id", ""),
                    published_at=getattr(
                        n,
                        "published_at",
                        datetime.now(UTC),
                    ),
                    title=getattr(n, "title", ""),
                    source_name=getattr(
                        n, "source_name", ""
                    ),
                    source_url=getattr(
                        n, "source_url", ""
                    ),
                    currencies=getattr(
                        n, "currencies", []
                    ),
                    snippet=getattr(n, "snippet", None),
                    sentiment_score=None,
                )
            )

    return ApiResponse(
        data=FundamentalNewsResponse(
            items=items,
            total=len(items),
            symbol=symbol,
        )
    )


@router.get(
    "/fundamental/calendar",
    response_model=ApiResponse[FundamentalCalendarResponse],
)
@limiter.limit("60/minute")
async def get_fundamental_calendar(
    request: Request,
    symbol: str = Query(default="USDJPY"),
    days: int = Query(default=2, ge=1, le=7),
    svc=Depends(get_fundamental_service),
) -> ApiResponse[FundamentalCalendarResponse]:
    """経済カレンダーを取得

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペアシンボル
        days: 取得日数（今日から）
        svc: FundamentalDataService

    Returns:
        ApiResponse[FundamentalCalendarResponse]: カレンダー
    """
    events: list[EconomicEventResponse] = []
    next_high_minutes: float | None = None
    now = datetime.now(UTC)

    if svc is not None:
        cached = svc.get_cached_calendar_events()

        currencies = _symbol_to_currencies(symbol)
        filtered = [
            ev
            for ev in cached
            if getattr(ev, "currency", "")
            in currencies
        ]

        filtered.sort(
            key=lambda e: getattr(
                e, "event_time", datetime.min
            )
        )

        for ev in filtered:
            ev_time = getattr(ev, "event_time", now)
            mins = (
                ev_time - now
            ).total_seconds() / 60
            impact_val = getattr(ev, "impact", None)
            # ImpactLevel enum → 文字列変換
            if hasattr(impact_val, "value"):
                impact_str = impact_val.value
            else:
                impact_str = str(impact_val or "low")

            events.append(
                EconomicEventResponse(
                    event_id=getattr(
                        ev, "event_id", ""
                    ),
                    event_time=ev_time,
                    currency=getattr(
                        ev, "currency", ""
                    ),
                    event_name=getattr(
                        ev, "event_name", ""
                    ),
                    impact=impact_str,
                    actual=getattr(ev, "actual", None),
                    forecast=getattr(
                        ev, "forecast", None
                    ),
                    previous=getattr(
                        ev, "previous", None
                    ),
                    is_released=getattr(
                        ev, "is_released", False
                    ),
                    minutes_until=round(mins, 1),
                )
            )

            # 次のHIGHインパクトイベントまでの分数
            if (
                impact_str == "high"
                and mins > 0
                and (
                    next_high_minutes is None
                    or mins < next_high_minutes
                )
            ):
                next_high_minutes = round(mins, 1)

    return ApiResponse(
        data=FundamentalCalendarResponse(
            events=events,
            symbol=symbol,
            next_high_impact_minutes=next_high_minutes,
        )
    )
