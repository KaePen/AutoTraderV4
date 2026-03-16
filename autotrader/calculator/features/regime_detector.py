"""マーケットレジーム検出モジュール

ADX、正規化ATR、MA整列度から相場レジームを判定する。
BREAKOUT検出: 直近N足の高値/安値を突破 + ボラ拡大中。
CHOPPY検出: CI高 + ADX低 + MA非整列 → ランダムウォーク状態。
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
        is_breakout: ブレイクアウト検出フラグ
        atr_change_rate: ATR変化率
        volatility_direction: ボラ方向
            ("expanding"/"compressing"/"neutral")
    """

    regime: MarketRegime
    trend_strength: float
    volatility_level: float
    adx: float
    confidence: float
    reasoning: str
    is_breakout: bool = False
    atr_change_rate: float = 0.0
    volatility_direction: str = "neutral"


@dataclass(frozen=True)
class RegimeDetectorConfig:
    """レジーム検出設定

    Attributes:
        high_vol_atr_threshold: 高ボラティリティ判定閾値
        low_vol_atr_threshold: 低ボラティリティ判定閾値
        trend_adx_threshold: トレンド判定ADX閾値
        strong_trend_adx_threshold: 強トレンド判定ADX閾値
        ma_alignment_threshold: MA整列判定閾値
        breakout_enabled: ブレイクアウト検出有効化
        breakout_lookback: ブレイクアウト判定のルックバック期間
        vol_expanding_threshold: ボラ拡大判定閾値
        vol_compressing_threshold: ボラ縮小判定閾値
        choppy_enabled: CHOPPY検出有効化
        choppy_ci_threshold: CI閾値（61.8=フィボナッチ）
        choppy_adx_threshold: CHOPPY ADX上限
        choppy_ma_alignment_max: CHOPPY MA整列度上限
    """

    high_vol_atr_threshold: float = 1.5
    low_vol_atr_threshold: float = 0.7
    trend_adx_threshold: float = 20.0
    strong_trend_adx_threshold: float = 30.0
    ma_alignment_threshold: float = 0.3
    breakout_enabled: bool = False
    breakout_lookback: int = 20
    # ボラティリティ方向判定閾値
    vol_expanding_threshold: float = 0.3
    vol_compressing_threshold: float = -0.2
    # CHOPPY検出
    choppy_enabled: bool = False
    choppy_ci_threshold: float = 61.8
    choppy_adx_threshold: float = 20.0
    choppy_ma_alignment_max: float = 0.15


class MarketRegimeDetector:
    """マーケットレジーム検出器

    ADX、正規化ATR、MA整列度からレジームを判定する。

    判定ロジック（優先度順）:
    0. BREAKOUT: 直近N足の高値/安値を突破 + ボラ拡大
    1. TREND: ADXが高く、MA整列
    2. HIGH_VOL: 正規化ATR > 1.5 かつ ADX < 20
    3. CHOPPY: CI > 61.8 + ADX < 20 + MA非整列
    4. LOW_VOL: 正規化ATR < 0.7
    5. RANGE: その他
    """

    def __init__(
        self, config: RegimeDetectorConfig | None = None,
    ) -> None:
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
        *,
        breakout_up: bool = False,
        breakout_down: bool = False,
        atr_change_rate: float = 0.0,
        choppiness_index: float = 0.0,
    ) -> RegimeResult:
        """レジームを検出

        Args:
            normalized_atr: 正規化ATR
            adx: ADX値
            ma_alignment: MA整列度（-1から1）
            breakout_up: 上方ブレイクアウト発生
            breakout_down: 下方ブレイクアウト発生
            atr_change_rate: ATR変化率
            choppiness_index: Choppiness Index（0-100）

        Returns:
            RegimeResult: レジーム判定結果
        """
        # NaN対策
        if (
            pd.isna(normalized_atr)
            or pd.isna(adx)
            or pd.isna(ma_alignment)
        ):
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
        regime, confidence, reasoning = (
            self._determine_regime(
                normalized_atr, adx, ma_alignment,
                breakout_up=breakout_up,
                breakout_down=breakout_down,
                atr_change_rate=atr_change_rate,
                choppiness_index=choppiness_index,
            )
        )

        _is_breakout = regime == MarketRegime.BREAKOUT

        # ボラティリティ方向判定
        cfg = self.config
        if atr_change_rate > cfg.vol_expanding_threshold:
            _vol_dir = "expanding"
        elif atr_change_rate < cfg.vol_compressing_threshold:
            _vol_dir = "compressing"
        else:
            _vol_dir = "neutral"

        return RegimeResult(
            regime=regime,
            trend_strength=trend_strength,
            volatility_level=normalized_atr,
            adx=adx,
            confidence=confidence,
            reasoning=reasoning,
            is_breakout=_is_breakout,
            atr_change_rate=atr_change_rate,
            volatility_direction=_vol_dir,
        )

    def detect_from_row(self, row: pd.Series) -> RegimeResult:
        """DataFrameの行からレジームを検出

        Args:
            row: 事前計算済みデータを含む行

        Returns:
            RegimeResult: レジーム判定結果
        """
        # カラム名の候補リスト
        atr_cols = [
            "normalized_atr", "norm_atr", "atr_norm",
        ]
        adx_cols = ["adx", "ADX", "adx_14"]
        ma_cols = [
            "ma_alignment", "ma_align",
            "trend_alignment",
        ]

        normalized_atr = self._get_value(
            row, atr_cols, default=1.0,
        )
        adx = self._get_value(
            row, adx_cols, default=20.0,
        )
        ma_alignment = self._get_value(
            row, ma_cols, default=0.0,
        )

        # ブレイクアウト関連フィールド
        breakout_up = bool(
            self._get_value(
                row, ["breakout_up"], default=0.0,
            )
        )
        breakout_down = bool(
            self._get_value(
                row, ["breakout_down"], default=0.0,
            )
        )
        atr_change_rate = self._get_value(
            row, ["atr_change_rate"], default=0.0,
        )

        # CHOPPY関連フィールド
        choppiness_index = self._get_value(
            row, ["choppiness_index", "chop_14"],
            default=0.0,
        )

        return self.detect(
            normalized_atr, adx, ma_alignment,
            breakout_up=breakout_up,
            breakout_down=breakout_down,
            atr_change_rate=atr_change_rate,
            choppiness_index=choppiness_index,
        )

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

        return pd.DataFrame(
            results, index=normalized_atr.index,
        )

    def _determine_regime(
        self,
        normalized_atr: float,
        adx: float,
        ma_alignment: float,
        *,
        breakout_up: bool = False,
        breakout_down: bool = False,
        atr_change_rate: float = 0.0,
        choppiness_index: float = 0.0,
    ) -> tuple[MarketRegime, float, str]:
        """レジームを判定

        Args:
            normalized_atr: 正規化ATR
            adx: ADX値
            ma_alignment: MA整列度
            breakout_up: 上方ブレイクアウト発生
            breakout_down: 下方ブレイクアウト発生
            atr_change_rate: ATR変化率
            choppiness_index: Choppiness Index

        Returns:
            tuple[MarketRegime, float, str]:
                (レジーム, 確度, 理由)
        """
        cfg = self.config

        # 0. BREAKOUT: 直近N足の高値/安値突破 + ボラ拡大中
        if cfg.breakout_enabled and (
            breakout_up or breakout_down
        ):
            if (
                adx < cfg.trend_adx_threshold
                and atr_change_rate > 0.0
            ):
                _dir = "上方" if breakout_up else "下方"
                _atr_conf = min(
                    atr_change_rate / 0.5, 1.0,
                )
                _adx_conf = (
                    adx / cfg.trend_adx_threshold
                )
                confidence = (
                    (_atr_conf + _adx_conf) / 2.0
                )
                return (
                    MarketRegime.BREAKOUT,
                    min(confidence, 1.0),
                    f"{_dir}ブレイクアウト"
                    f"(ADX={adx:.1f},"
                    f" ATR変化={atr_change_rate:.2f})",
                )

        # 1. TREND: ADXが高く、MA整列
        is_aligned = (
            abs(ma_alignment) > cfg.ma_alignment_threshold
        )
        if adx >= cfg.trend_adx_threshold and is_aligned:
            if adx >= cfg.strong_trend_adx_threshold:
                confidence = min(adx / 50.0, 1.0)
                return (
                    MarketRegime.TREND,
                    confidence,
                    f"強トレンド"
                    f"(ADX={adx:.1f},"
                    f" MA整列={ma_alignment:.2f})",
                )
            else:
                confidence = (
                    adx - cfg.trend_adx_threshold
                ) / 20.0
                return (
                    MarketRegime.TREND,
                    confidence,
                    f"トレンド"
                    f"(ADX={adx:.1f},"
                    f" MA整列={ma_alignment:.2f})",
                )

        # 2. HIGH_VOL: 高ボラティリティ（方向性なし）
        if (
            normalized_atr > cfg.high_vol_atr_threshold
            and adx < cfg.trend_adx_threshold
        ):
            confidence = min(
                (normalized_atr
                 - cfg.high_vol_atr_threshold) / 0.5,
                1.0,
            )
            return (
                MarketRegime.HIGH_VOL,
                confidence,
                f"高ボラ(ATR={normalized_atr:.2f})"
                f"で方向性なし(ADX={adx:.1f})",
            )

        # 3. CHOPPY: CI高 + ADX低 + MA非整列
        if (
            cfg.choppy_enabled
            and choppiness_index
            > cfg.choppy_ci_threshold
            and adx < cfg.choppy_adx_threshold
            and abs(ma_alignment)
            < cfg.choppy_ma_alignment_max
        ):
            # 確度: CIの超過度合い
            _ci_excess = (
                choppiness_index
                - cfg.choppy_ci_threshold
            )
            confidence = min(_ci_excess / 20.0, 1.0)
            return (
                MarketRegime.CHOPPY,
                confidence,
                f"チョッピー"
                f"(CI={choppiness_index:.1f},"
                f" ADX={adx:.1f},"
                f" MA={ma_alignment:.2f})",
            )

        # 4. LOW_VOL: 低ボラティリティ
        if normalized_atr < cfg.low_vol_atr_threshold:
            confidence = (
                cfg.low_vol_atr_threshold
                - normalized_atr
            ) / 0.3
            return (
                MarketRegime.LOW_VOL,
                min(confidence, 1.0),
                f"低ボラ(ATR={normalized_atr:.2f})",
            )

        # 5. RANGE: その他
        return (
            MarketRegime.RANGE,
            0.5,
            f"レンジ(ADX={adx:.1f},"
            f" ATR={normalized_atr:.2f})",
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
