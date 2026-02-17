"""Ollama構造化出力スキーマ定義

Veto判定・信頼度調整用のPydanticスキーマ。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VetoCheckOutput(BaseModel):
    """Veto判定出力スキーマ

    高確度シグナルに対する取引禁止チェックの結果。

    Attributes:
        veto: 取引禁止フラグ（True=禁止、False=許可）
        confidence: Veto判定の確信度（0.0-1.0）
        veto_reason: 禁止理由
        veto_reason_code: 禁止理由コード
        risk_factors: リスク要因リスト
    """

    veto: bool = Field(
        description="取引禁止かどうか（true=禁止、false=許可）"
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Veto判定の確信度（0.0-1.0）"
    )
    veto_reason: str | None = Field(
        default=None,
        description="禁止理由（vetoがtrueの場合のみ）"
    )
    veto_reason_code: Literal[
        "ECONOMIC_EVENT",
        "PRICE_SPIKE",
        "SPREAD_ABNORMAL",
        "TREND_REVERSAL",
        "TF_CONFLICT",
        "LOW_LIQUIDITY",
        "SESSION_INAPPROPRIATE",
        "OTHER",
    ] | None = Field(
        default=None,
        description="禁止理由コード"
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="リスク要因リスト"
    )


class ConfidenceAdjustmentOutput(BaseModel):
    """信頼度調整出力スキーマ

    シグナルの信頼度を市場状況に基づいて調整。

    Attributes:
        adjusted_confidence: 調整後の信頼度
        adjustment_reason: 調整理由
        market_context: 市場コンテキストの説明
        tp_adjustment: TP調整係数（1.0=変更なし）
        sl_adjustment: SL調整係数（1.0=変更なし）
    """

    adjusted_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="調整後の信頼度（0.0-1.0）"
    )
    adjustment_reason: str = Field(
        description="調整理由"
    )
    market_context: str = Field(
        default="",
        description="市場コンテキストの説明"
    )
    tp_adjustment: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="TP調整係数（0.5-2.0）"
    )
    sl_adjustment: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="SL調整係数（0.5-2.0）"
    )


class MarketAnalysisOutput(BaseModel):
    """市場分析出力スキーマ

    より詳細な市場分析を生成する際のスキーマ。

    Attributes:
        trend_assessment: トレンド評価
        volatility_level: ボラティリティレベル
        key_levels: 重要価格レベル
        risk_factors: リスク要因
        opportunity_factors: 機会要因
        summary: 分析サマリー
    """

    trend_assessment: Literal[
        "STRONG_UP", "UP", "NEUTRAL", "DOWN", "STRONG_DOWN"
    ] = Field(
        description="トレンド評価"
    )
    volatility_level: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        description="ボラティリティレベル"
    )
    key_levels: list[float] = Field(
        default_factory=list,
        description="重要価格レベル"
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="リスク要因"
    )
    opportunity_factors: list[str] = Field(
        default_factory=list,
        description="機会要因"
    )
    summary: str = Field(
        description="分析サマリー（日本語）"
    )
