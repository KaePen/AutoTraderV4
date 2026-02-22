"""Ollamaプロンプトテンプレート

Veto判定・信頼度調整用のプロンプト定義。
"""

from __future__ import annotations

from typing import Any


SYSTEM_PROMPT_TRADING = """あなたはFX自動売買システムのリスク評価エンジンです。
以下の原則に従って分析と判断を行ってください：

1. 感情や希望的観測を排除し、データに基づいた客観的な判断を行う
2. リスク管理を最優先し、損失を最小化する方向で判断する
3. 複数の時間足の情報を総合的に評価する
4. 判断理由は明確かつ具体的に述べる

回答は指定されたJSON形式で出力してください。"""


VETO_CHECK_PROMPT = """## Veto判定リクエスト

シンボル: {symbol}
現在時刻: {timestamp}
現在価格: {current_price}

## テクニカルシグナル
- 方向: {direction}
- 信頼度: {confidence:.1%}
- RSI: {rsi:.1f}
- MACD: {macd:.5f}
- ADX: {adx:.1f}
- トレンド: {trend}

## マルチタイムフレーム状況
{mtf_summary}

## エントリー条件
- エントリー価格: {entry_price:.5f}
- ストップロス: {stop_loss:.5f}
- テイクプロフィット: {take_profit:.5f}
- リスクリワード比: {risk_reward:.2f}

---

上記の高確度トレードシグナルに対して、**取引禁止条件**に該当するか判定してください。

取引禁止条件（いずれか1つでも該当すればveto=true）:
1. 重要経済イベント直前（30分以内）
2. 価格が急変動中（スパイク/ギャップ）
3. スプレッドが異常拡大
4. 明らかな逆行トレンド進行中
5. TF矛盾が大きい（上位足と下位足が逆方向）

判断:
- veto: true（取引禁止）/ false（取引許可）
- confidence: Veto判定の確信度（0.0-1.0）
- veto_reason: 禁止理由（vetoがtrueの場合のみ）
- veto_reason_code: 禁止理由コード
- risk_factors: リスク要因リスト"""


CONFIDENCE_ADJUSTMENT_PROMPT = """## 信頼度調整リクエスト

シンボル: {symbol}
現在時刻: {timestamp}
現在価格: {current_price}

## テクニカルシグナル
- 方向: {direction}
- 現在信頼度: {confidence:.1%}
- RSI: {rsi:.1f}
- MACD: {macd:.5f}
- ADX: {adx:.1f}

## マルチタイムフレーム状況
{mtf_summary}

## ボラティリティ
- ATR: {atr:.5f}
- ATR比率: {atr_ratio:.2%}（過去20日平均比）

## エントリー条件
- エントリー価格: {entry_price:.5f}
- ストップロス: {stop_loss:.5f}
- テイクプロフィット: {take_profit:.5f}

---

上記のシグナルに対して、市場状況を考慮した信頼度調整を行ってください。

調整基準:
- ボラティリティが高い場合: 信頼度を下方修正
- ボラティリティが低い場合: 信頼度を下方修正（機会が少ない）
- MTFが揃っている場合: 信頼度を上方修正
- MTFが矛盾している場合: 信頼度を下方修正
- ADXが高い場合（強トレンド）: 信頼度を上方修正

出力:
- adjusted_confidence: 調整後信頼度（0.0-1.0）
- adjustment_reason: 調整理由
- market_context: 市場状況の説明
- tp_adjustment: TP調整係数（0.5-2.0、強トレンド時は高めに）
- sl_adjustment: SL調整係数（0.5-2.0、高ボラ時は広めに）"""


def build_veto_check_prompt(
    symbol: str,
    timestamp: str,
    current_price: float,
    direction: str,
    confidence: float,
    rsi: float,
    macd: float,
    adx: float,
    trend: str,
    mtf_summary: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
) -> str:
    """Veto判定プロンプトを構築

    Args:
        symbol: 通貨ペア
        timestamp: タイムスタンプ
        current_price: 現在価格
        direction: シグナル方向
        confidence: シグナル信頼度
        rsi: RSI値
        macd: MACD値
        adx: ADX値
        trend: トレンド方向
        mtf_summary: MTFサマリ
        entry_price: エントリー価格
        stop_loss: ストップロス
        take_profit: テイクプロフィット

    Returns:
        str: プロンプト文字列
    """
    # リスクリワード比を計算
    if direction == "BUY":
        risk = entry_price - stop_loss
        reward = take_profit - entry_price
    else:
        risk = stop_loss - entry_price
        reward = entry_price - take_profit

    risk_reward = reward / risk if risk > 0 else 0.0

    return VETO_CHECK_PROMPT.format(
        symbol=symbol,
        timestamp=timestamp,
        current_price=current_price,
        direction=direction,
        confidence=confidence,
        rsi=rsi,
        macd=macd,
        adx=adx,
        trend=trend,
        mtf_summary=mtf_summary or "データなし",
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
    )


def build_confidence_adjustment_prompt(
    symbol: str,
    timestamp: str,
    current_price: float,
    direction: str,
    confidence: float,
    rsi: float,
    macd: float,
    adx: float,
    mtf_summary: str,
    atr: float,
    atr_ratio: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
) -> str:
    """信頼度調整プロンプトを構築

    Args:
        symbol: 通貨ペア
        timestamp: タイムスタンプ
        current_price: 現在価格
        direction: シグナル方向
        confidence: 現在の信頼度
        rsi: RSI値
        macd: MACD値
        adx: ADX値
        mtf_summary: MTFサマリ
        atr: ATR値
        atr_ratio: ATR比率（過去平均比）
        entry_price: エントリー価格
        stop_loss: ストップロス
        take_profit: テイクプロフィット

    Returns:
        str: プロンプト文字列
    """
    return CONFIDENCE_ADJUSTMENT_PROMPT.format(
        symbol=symbol,
        timestamp=timestamp,
        current_price=current_price,
        direction=direction,
        confidence=confidence,
        rsi=rsi,
        macd=macd,
        adx=adx,
        mtf_summary=mtf_summary or "データなし",
        atr=atr,
        atr_ratio=atr_ratio,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


def format_mtf_summary(mtf_data: dict[str, Any]) -> str:
    """MTFデータをサマリ文字列に変換

    Args:
        mtf_data: MTF分析データ

    Returns:
        str: フォーマット済み文字列
    """
    if not mtf_data:
        return "データなし"

    lines: list[str] = []

    for tf, data in mtf_data.items():
        direction = data.get("direction", "neutral")
        strength = data.get("strength", 0.0)
        ma_alignment = data.get("ma_alignment", 0.0)

        direction_jp = {
            "up": "上昇",
            "down": "下降",
            "neutral": "中立",
        }.get(direction, direction)

        lines.append(
            f"- {tf}: {direction_jp}（強度: {strength:.2f}, "
            f"MA整列: {ma_alignment:+.2f}）"
        )

    return "\n".join(lines) if lines else "データなし"


# ==== ファンダメンタルコンテキスト関連プロンプト ====

MARKET_OUTLOOK_PROMPT = """## 市場観分析リクエスト

シンボル: {symbol}
分析日時: {timestamp}
現在価格: {current_price}

## 直近の経済指標
{upcoming_events_section}

## テクニカルサマリー
{technical_summary}

---

上記の情報を基に、今後{valid_days}日間の市場観を分析してください。

出力フォーマット:
- direction_score: 方向性スコア（-1.0=強い売り、0=中立、+1.0=強い買い）
- confidence: 確信度（0.0-1.0）
- macro_summary: マクロ要約（日本語50文字以内）
- key_factors: 主要要因リスト（3-5個）
- valid_days: この見通しの有効日数（1-30）
- risk_events: 今週の注意すべきイベントリスト"""


POST_EVENT_ANALYSIS_PROMPT = """## 指標後バイアス分析リクエスト

シンボル: {symbol}
分析日時: {timestamp}
イベント名: {event_name}
通貨: {currency}

## 発表結果
- 実績: {actual}
- 予測: {forecast}
- 前回: {previous}
- 予測比サプライズ: {surprise_pct:+.1%}

## 現在の市場状況
- 現在価格: {current_price}
- 指標発表後の価格変動: {price_change:+.1%}

---

上記の経済指標発表結果を基に、今後の市場バイアスを分析してください。

出力フォーマット:
- surprise_direction: サプライズ方向（BULLISH/BEARISH/NEUTRAL）
- expected_duration_hours: バイアスの持続時間（1-72時間）
- bias_score: バイアススコア（-1.0=強い売り、+1.0=強い買い）
- analysis: 分析内容（日本語、具体的に）"""


VETO_WITH_FUNDAMENTAL_SECTION = """\n\n## ファンダメンタルコンテキスト
{fundamental_section}"""


def build_market_outlook_prompt(
    symbol: str,
    timestamp: str,
    current_price: float,
    upcoming_events: list[dict],
    technical_summary: str = "",
    valid_days: int = 7,
) -> str:
    """市場観分析プロンプトを構築

    Args:
        symbol: 通貨ペア
        timestamp: タイムスタンプ
        current_price: 現在価格
        upcoming_events: 直近イベントリスト
        technical_summary: テクニカルサマリー
        valid_days: 有効日数

    Returns:
        str: プロンプト文字列
    """
    if upcoming_events:
        event_lines = []
        for ev in upcoming_events[:5]:
            event_lines.append(
                f"  - {ev.get('name', '不明')} "
                f"({ev.get('minutes_until', 0):.0f}分後, "
                f"インパクト: {ev.get('impact', '不明')})"
            )
        events_section = "\n".join(event_lines)
    else:
        events_section = "  直近に重要指標なし"

    return MARKET_OUTLOOK_PROMPT.format(
        symbol=symbol,
        timestamp=timestamp,
        current_price=current_price,
        upcoming_events_section=events_section,
        technical_summary=technical_summary or "データなし",
        valid_days=valid_days,
    )


def build_post_event_analysis_prompt(
    symbol: str,
    timestamp: str,
    event_name: str,
    currency: str,
    actual: float | None,
    forecast: float | None,
    previous: float | None,
    current_price: float,
    price_change: float = 0.0,
) -> str:
    """指標後バイアス分析プロンプトを構築

    Args:
        symbol: 通貨ペア
        timestamp: タイムスタンプ
        event_name: イベント名
        currency: 通貨コード
        actual: 実績値
        forecast: 予測値
        previous: 前回値
        current_price: 現在価格
        price_change: 指標発表後の価格変化率

    Returns:
        str: プロンプト文字列
    """
    # サプライズ率計算
    surprise_pct = 0.0
    if actual is not None and forecast is not None and forecast != 0:
        surprise_pct = (actual - forecast) / abs(forecast)

    return POST_EVENT_ANALYSIS_PROMPT.format(
        symbol=symbol,
        timestamp=timestamp,
        event_name=event_name,
        currency=currency,
        actual=f"{actual:.4f}" if actual is not None else "未発表",
        forecast=f"{forecast:.4f}" if forecast is not None else "なし",
        previous=f"{previous:.4f}" if previous is not None else "なし",
        surprise_pct=surprise_pct,
        current_price=current_price,
        price_change=price_change,
    )
