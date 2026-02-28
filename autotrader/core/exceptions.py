"""カスタム例外定義"""

from __future__ import annotations


class AutoTraderError(Exception):
    """AutoTrader基底例外"""

    pass


class DataError(AutoTraderError):
    """データ関連エラー"""

    pass


class ExecutionError(AutoTraderError):
    """実行関連エラー"""

    pass


class ValidationError(AutoTraderError):
    """バリデーションエラー"""

    pass


class ConfigurationError(AutoTraderError):
    """設定エラー"""

    pass


class ConstraintViolationError(AutoTraderError):
    """制約違反エラー"""

    pass


class InsufficientDataError(DataError):
    """データ不足エラー"""

    pass


class ConnectionError(AutoTraderError):
    """接続エラー"""

    pass


class LLMError(AutoTraderError):
    """LLM関連エラー基底クラス"""

    pass


class LLMConnectionError(LLMError):
    """LLM接続エラー"""

    pass


class LLMTimeoutError(LLMError):
    """LLMタイムアウトエラー"""

    pass


class LLMResponseError(LLMError):
    """LLMレスポンス解析エラー"""

    pass


class TradingError(AutoTraderError):
    """トレード実行関連エラー"""

    pass


class BacktestError(AutoTraderError):
    """バックテスト実行関連エラー"""

    pass


class CalculationError(AutoTraderError):
    """計算処理関連エラー"""

    pass


class StrategyError(AutoTraderError):
    """戦略実行関連エラー"""

    pass
