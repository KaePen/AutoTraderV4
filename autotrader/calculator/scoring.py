"""指標スコアリングの統一関数

RSI・ATR等の指標スコアリング計算を一元管理する。
各コンポーネント（TimeframeEvaluator, IndicatorStrengthCalculator等）
はこのモジュールの関数を呼び出す。
"""

from __future__ import annotations


def score_rsi_discrete(
    rsi: float,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> tuple[float, str]:
    """RSI値を離散スコア（-3.0 ~ 3.0）に変換

    TimeframeEvaluatorのコンセンサススコア計算用。
    極端値で高スコア、閾値付近で低スコアを返す。

    Args:
        rsi: RSI値（0-100）
        oversold: 売られすぎ閾値
        overbought: 買われすぎ閾値

    Returns:
        tuple[float, str]: (スコア, 理由文字列)
    """
    # 極端な売られすぎ → 強い買いシグナル
    if rsi < oversold - 10:
        return 3.0, f"RSI極低({rsi:.1f})"
    # 売られすぎ → 買いシグナル
    elif rsi < oversold - 5:
        return 2.0, f"RSI低({rsi:.1f})"
    elif rsi < oversold:
        return 1.0, ""
    # 極端な買われすぎ → 強い売りシグナル
    elif rsi > overbought + 10:
        return -3.0, f"RSI極高({rsi:.1f})"
    # 買われすぎ → 売りシグナル
    elif rsi > overbought + 5:
        return -2.0, f"RSI高({rsi:.1f})"
    elif rsi > overbought:
        return -1.0, ""
    return 0.0, ""


def score_rsi_continuous(
    rsi: float,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> float:
    """RSI値を連続強度（-1.0 ~ 1.0）に変換

    IndicatorStrengthCalculatorの強度計算用。
    中立帯でも微小な傾きを返す。

    Args:
        rsi: RSI値（0-100）
        oversold: 売られすぎ閾値
        overbought: 買われすぎ閾値

    Returns:
        float: RSI強度（負=売り、正=買い）
    """
    if rsi <= oversold:
        # 売られすぎ → 買いシグナル
        return (oversold - rsi) / oversold
    elif rsi >= overbought:
        # 買われすぎ → 売りシグナル
        return -(rsi - overbought) / (100 - overbought)
    elif rsi < 50:
        # 中立帯（やや買い寄り）
        return (50 - rsi) / (50 - oversold) * 0.3
    else:
        # 中立帯（やや売り寄り）
        return -(rsi - 50) / (overbought - 50) * 0.3


def normalize_atr_by_price(
    atr: float,
    price: float,
    scale_factor: float = 0.02,
) -> float:
    """ATRを価格比率で正規化（0.0 ~ 1.0）

    一般的なFXの日足ATR比率（0.5-2%程度）を基準に正規化。

    Args:
        atr: ATR値
        price: 現在価格（close）
        scale_factor: 正規化スケール（デフォルト0.02 = 2%）

    Returns:
        float: 正規化ATR（0.0 ~ 1.0）
    """
    if price <= 0 or scale_factor <= 0:
        return 0.0
    atr_ratio = atr / price
    return min(atr_ratio / scale_factor, 1.0)
