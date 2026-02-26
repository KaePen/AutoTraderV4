"""LLM設定

Ollama接続設定とVeto設定を管理。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OllamaSettings:
    """Ollama接続設定

    Attributes:
        host: Ollamaホスト
        model: 使用モデル
        temperature: 温度パラメータ
        timeout_seconds: タイムアウト秒
        max_retries: 最大リトライ回数
        num_ctx: コンテキストウィンドウサイズ
        keep_alive: モデル保持時間
        num_gpu: GPUレイヤー数（-1=全GPU、0=CPUのみ）
        use_mmap: メモリマッピング使用
        num_thread: CPUスレッド数（None=自動）
    """

    host: str = "http://localhost:11434"
    model: str = "erwan2/DeepSeek-R1-Distill-Qwen-14B"
    temperature: float = 0.1
    timeout_seconds: float = 120.0
    max_retries: int = 3
    # R1モデルは<think>推論でトークンを消費するため余裕が必要
    num_ctx: int = 8192
    keep_alive: str = "5m"
    num_gpu: int = -1  # -1: 全GPU使用、0: CPUのみ
    use_mmap: bool = True
    num_thread: int | None = None


@dataclass(frozen=True)
class VetoSettings:
    """Veto判定設定

    Attributes:
        enabled: Veto判定有効化フラグ
        confidence_threshold: Veto発動の確信度閾値
        min_signal_confidence: LLM検証対象の最低信頼度
        fallback_on_error: エラー時フォールバック動作
    """

    enabled: bool = True
    confidence_threshold: float = 0.6
    min_signal_confidence: float = 0.7
    fallback_on_error: bool = True


@dataclass(frozen=True)
class ConfidenceAdjustmentSettings:
    """信頼度調整設定

    Attributes:
        enabled: 信頼度調整有効化フラグ
        min_adjustment: 最小調整幅
        max_adjustment: 最大調整幅
        apply_tp_sl_adjustment: TP/SL調整適用フラグ
    """

    enabled: bool = True
    min_adjustment: float = -0.2
    max_adjustment: float = 0.2
    apply_tp_sl_adjustment: bool = True


@dataclass(frozen=True)
class CacheSettings:
    """キャッシュ設定

    Attributes:
        enabled: キャッシュ有効化フラグ
        ttl_minutes: キャッシュ有効期間（分）
        max_size: 最大キャッシュサイズ
    """

    enabled: bool = True
    ttl_minutes: int = 5
    max_size: int = 1000


@dataclass(frozen=True)
class LLMSettings:
    """LLM統合設定

    Attributes:
        ollama: Ollama接続設定
        veto: Veto判定設定
        confidence: 信頼度調整設定
        cache: キャッシュ設定
        sample_rate: レスポンスサンプリング率
    """

    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    veto: VetoSettings = field(default_factory=VetoSettings)
    confidence: ConfidenceAdjustmentSettings = field(
        default_factory=ConfidenceAdjustmentSettings
    )
    cache: CacheSettings = field(default_factory=CacheSettings)
    sample_rate: float = 0.1

    @classmethod
    def default(cls) -> "LLMSettings":
        """デフォルト設定を取得

        Returns:
            LLMSettings: デフォルト設定
        """
        return cls()

    @classmethod
    def disabled(cls) -> "LLMSettings":
        """LLM無効設定を取得

        Returns:
            LLMSettings: LLM無効設定
        """
        return cls(
            veto=VetoSettings(enabled=False),
            confidence=ConfidenceAdjustmentSettings(enabled=False),
        )

    @classmethod
    def backtest(cls) -> "LLMSettings":
        """バックテスト用設定を取得

        高速化のためキャッシュを有効化。

        Returns:
            LLMSettings: バックテスト設定
        """
        return cls(
            ollama=OllamaSettings(
                timeout_seconds=60.0,
                keep_alive="30m",
            ),
            cache=CacheSettings(
                enabled=True,
                ttl_minutes=30,
                max_size=10000,
            ),
        )

    @classmethod
    def production(cls) -> "LLMSettings":
        """本番環境用設定を取得

        Returns:
            LLMSettings: 本番設定
        """
        return cls(
            ollama=OllamaSettings(
                timeout_seconds=30.0,
                max_retries=3,
            ),
            veto=VetoSettings(
                enabled=True,
                confidence_threshold=0.6,
            ),
            confidence=ConfidenceAdjustmentSettings(
                enabled=True,
                apply_tp_sl_adjustment=True,
            ),
        )
