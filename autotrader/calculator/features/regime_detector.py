"""マーケットレジーム検出モジュール

ADX、正規化ATR、MA整列度から相場レジームを判定する。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autotrader.core.enums import MarketRegime


@dataclass(frozen=True)
class RegimeResult:
    """レジーム判定結果

    Attributes:
        regime: 相場レジーム
        trend_strength: トレンド強度（0-1）
        volatility_level: ボラティリティレベル（正規化ATR）
        adx: ADX値
        confidence: 判定確度（0-1）
        reasoning: 判定理由
    """

    regime: MarketRegime
    trend_strength: float
    volatility_level: float
    adx: float
    confidence: float
    reasoning: str


@dataclass(frozen=True)
class RegimeDetectorConfig:
    """レジーム検出設定

    Attributes:
        high_vol_atr_threshold: 高ボラティリティ判定閾値
        low_vol_atr_threshold: 低ボラティリティ判定閾値
        trend_adx_threshold: トレンド判定ADX閾値
        strong_trend_adx_threshold: 強トレンド判定ADX閾値
        ma_alignment_threshold: MA整列判定閾値
    """

    high_vol_atr_threshold: float = 1.5
    low_vol_atr_threshold: float = 0.7
    trend_adx_threshold: float = 20.0
    strong_trend_adx_threshold: float = 30.0
    ma_alignment_threshold: float = 0.3


class MarketRegimeDetector:
    """マーケットレジーム検出器

    ADX、正規化ATR、MA整列度からレジームを判定する。

    判定ロジック（優先度順）:
    1. HIGH_VOL: 正規化ATR > 1.5 かつ ADX < 25
    2. TREND: ADX >= 20 かつ MA整列
    3. LOW_VOL: 正規化ATR < 0.7
    4. RANGE: その他
    """

    def __init__(self, config: RegimeDetectorConfig | None = None) -> None:
        """初期化

        Args:
            config: 検出設定（Noneの場合はデフォルト）
        """
        self.config = config or RegimeDetectorConfig()

    def detect(
        self,
        normalized_atr: float,
        adx: float,
        ma_alignment: float,
    ) -> RegimeResult:
        """レジームを検出

        Args:
            normalized_atr: 正規化ATR
            adx: ADX値
            ma_alignment: MA整列度（-1から1）

        Returns:
            RegimeResult: レジーム判定結果
        """
        # NaN対策
        if pd.isna(normalized_atr) or pd.isna(adx) or pd.isna(ma_alignment):
            return RegimeResult(
                regime=MarketRegime.RANGE,
                trend_strength=0.0,
                volatility_level=1.0,
                adx=0.0,
                confidence=0.0,
                reasoning="データ不足のためRANGE判定",
            )

        # トレンド強度を計算（ADXベース）
        trend_strength = min(adx / 40.0, 1.0)

        # 判定ロジック（優先度順）
        regime, confidence, reasoning = self._determine_regime(
            normalized_atr, adx, ma_alignment
        )

        return RegimeResult(
            regime=regime,
            trend_strength=trend_strength,
            volatility_level=normalized_atr,
            adx=adx,
            confidence=confidence,
            reasoning=reasoning,
        )

    def detect_from_row(self, row: pd.Series) -> RegimeResult:
        """DataFrameの行からレジームを検出

        Args:
            row: 事前計算済みデータを含む行

        Returns:
            RegimeResult: レジーム判定結果
        """
        # カラム名の候補リスト
        atr_cols = ["normalized_atr", "norm_atr", "atr_norm"]
        adx_cols = ["adx", "ADX", "adx_14"]
        ma_cols = ["ma_alignment", "ma_align", "trend_alignment"]

        normalized_atr = self._get_value(row, atr_cols, default=1.0)
        adx = self._get_value(row, adx_cols, default=20.0)
        ma_alignment = self._get_value(row, ma_cols, default=0.0)

        return self.detect(normalized_atr, adx, ma_alignment)

    def detect_series(
        self,
        normalized_atr: pd.Series,
        adx: pd.Series,
        ma_alignment: pd.Series,
    ) -> pd.DataFrame:
        """シリーズデータからレジームを検出

        Args:
            normalized_atr: 正規化ATRシリーズ
            adx: ADXシリーズ
            ma_alignment: MA整列度シリーズ

        Returns:
            pd.DataFrame: レジーム判定結果
        """
        results = []
        for i in range(len(normalized_atr)):
            result = self.detect(
                normalized_atr.iloc[i],
                adx.iloc[i],
                ma_alignment.iloc[i],
            )
            results.append({
                "regime": result.regime,
                "trend_strength": result.trend_strength,
                "volatility_level": result.volatility_level,
                "adx": result.adx,
                "confidence": result.confidence,
            })

        return pd.DataFrame(results, index=normalized_atr.index)

    def _determine_regime(
        self,
        normalized_atr: float,
        adx: float,
        ma_alignment: float,
    ) -> tuple[MarketRegime, float, str]:
        """レジームを判定

        Args:
            normalized_atr: 正規化ATR
            adx: ADX値
            ma_alignment: MA整列度

        Returns:
            tuple[MarketRegime, float, str]: (レジーム, 確度, 理由)
        """
        cfg = self.config

        # 1. HIGH_VOL: 高ボラティリティ（ADXが低い＝方向性なし）
        if normalized_atr > cfg.high_vol_atr_threshold and adx < 25:
            confidence = min(
                (normalized_atr - cfg.high_vol_atr_threshold) / 0.5, 1.0
            )
            return (
                MarketRegime.HIGH_VOL,
                confidence,
                f"高ボラ(ATR={normalized_atr:.2f})で方向性なし(ADX={adx:.1f})",
            )

        # 2. TREND: ADXが高く、MA整列
        is_aligned = abs(ma_alignment) > cfg.ma_alignment_threshold
        if adx >= cfg.trend_adx_threshold and is_aligned:
            # 強トレンド判定
            if adx >= cfg.strong_trend_adx_threshold:
                confidence = min(adx / 50.0, 1.0)
                return (
                    MarketRegime.TREND,
                    confidence,
                    f"強トレンド(ADX={adx:.1f}, MA整列={ma_alignment:.2f})",
                )
            else:
                confidence = (adx - cfg.trend_adx_threshold) / 20.0
                return (
                    MarketRegime.TREND,
                    confidence,
                    f"トレンド(ADX={adx:.1f}, MA整列={ma_alignment:.2f})",
                )

        # 3. LOW_VOL: 低ボラティリティ
        if normalized_atr < cfg.low_vol_atr_threshold:
            confidence = (cfg.low_vol_atr_threshold - normalized_atr) / 0.3
            return (
                MarketRegime.LOW_VOL,
                min(confidence, 1.0),
                f"低ボラ(ATR={normalized_atr:.2f})",
            )

        # 4. RANGE: その他
        return (
            MarketRegime.RANGE,
            0.5,
            f"レンジ(ADX={adx:.1f}, ATR={normalized_atr:.2f})",
        )

    def _get_value(
        self,
        row: pd.Series,
        col_names: list[str],
        default: float,
    ) -> float:
        """行から値を取得（複数カラム名候補対応）

        Args:
            row: データ行
            col_names: カラム名候補リスト
            default: デフォルト値

        Returns:
            float: 取得した値
        """
        for col in col_names:
            if col in row.index:
                val = row[col]
                if not pd.isna(val):
                    return float(val)
        return default
