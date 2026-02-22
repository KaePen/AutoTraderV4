"""設定クラス

Pydantic Settingsを使用した設定管理。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from autotrader.core.enums import TradingMode


@dataclass(frozen=True)
class StrategyConfig:
    """戦略パラメータ設定

    Walk-forward最適化で得られた最適パラメータ。

    Attributes:
        min_signals: シグナル発生に必要な最小シグナル数
        signal_margin: 買い/売りの差分マージン
        adx_threshold: ADX閾値（トレンド強度フィルター）
        sl_atr_mult: ストップロスのATR倍率
        tp_atr_mult: テイクプロフィットのATR倍率
        use_mtf: MTF（マルチタイムフレーム）確認を使用
        mtf_bonus: MTFトレンド一致時のボーナススコア
        mtf_required: MTFトレンド一致を必須とするか
        rsi_oversold: RSI売られすぎ閾値
        rsi_overbought: RSI買われすぎ閾値
        cooldown_bars: シグナル発生後のクールダウン足数
        default_volume: デフォルトロット数
    """

    min_signals: int = 4
    signal_margin: int = 2
    adx_threshold: float = 20.0
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 3.0
    use_mtf: bool = True
    mtf_bonus: int = 2
    mtf_required: bool = False
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    cooldown_bars: int = 4
    default_volume: float = 0.1

    @classmethod
    def optimized(cls) -> "StrategyConfig":
        """最適化済みパラメータを取得

        Walk-forward検証（2020-2024）で最適化されたパラメータ。
        5年間すべて黒字、総利益¥559,121（55.9%リターン）達成。

        Returns:
            StrategyConfig: 最適化済み設定
        """
        return cls(
            min_signals=4,
            signal_margin=2,
            adx_threshold=20.0,
            sl_atr_mult=2.0,
            tp_atr_mult=3.0,
            use_mtf=True,
            mtf_bonus=2,
            mtf_required=False,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            cooldown_bars=4,
            default_volume=0.5,
        )

    @classmethod
    def conservative(cls) -> "StrategyConfig":
        """保守的パラメータを取得

        低リスク設定。最大DD 2.33%。

        Returns:
            StrategyConfig: 保守的設定
        """
        return cls(
            min_signals=4,
            signal_margin=2,
            adx_threshold=20.0,
            sl_atr_mult=2.0,
            tp_atr_mult=3.0,
            use_mtf=True,
            mtf_bonus=2,
            mtf_required=False,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            cooldown_bars=0,
            default_volume=0.3,
        )

    @classmethod
    def aggressive(cls) -> "StrategyConfig":
        """積極的パラメータを取得

        より大きなロットサイズとMTF必須条件。

        Returns:
            StrategyConfig: 積極的設定
        """
        return cls(
            min_signals=4,
            signal_margin=2,
            adx_threshold=20.0,
            sl_atr_mult=2.0,
            tp_atr_mult=4.0,
            use_mtf=True,
            mtf_bonus=2,
            mtf_required=True,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            cooldown_bars=4,
            default_volume=1.0,
        )


class Settings(BaseSettings):
    """アプリケーション設定

    環境変数または.envファイルから読み込む。

    Attributes:
        trading_mode: トレーディングモード
        symbol: 対象通貨ペア
        database_url: データベースURL
        data_dir: データディレクトリ
        ollama_host: Ollamaホスト
        ollama_model: Ollamaモデル
        max_daily_loss_pct: 日次最大損失率（%）
        max_position_count: 最大ポジション数
        min_margin_ratio: 最低証拠金維持率（%）
        log_level: ログレベル
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 基本設定
    trading_mode: TradingMode = TradingMode.BACKTEST
    symbol: str = "USDJPY"

    # データベース
    database_url: str = "sqlite:///data/autotrader.db"

    # ディレクトリ
    data_dir: Path = Path("data")

    # Ollama
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"

    # リスク管理
    max_daily_loss_pct: float = Field(default=5.0, ge=0.0, le=100.0)
    max_position_count: int = Field(default=2, ge=1, le=10)
    min_margin_ratio: float = Field(default=150.0, ge=100.0)

    # ログ
    log_level: str = "INFO"

    # 戦略
    strategy: StrategyConfig = Field(
        default_factory=StrategyConfig.optimized
    )


@lru_cache
def get_settings() -> Settings:
    """設定シングルトンを取得

    Returns:
        Settings: 設定インスタンス
    """
    return Settings()
