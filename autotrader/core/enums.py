"""列挙型定義モジュール"""

from __future__ import annotations

from enum import Enum


class Timeframe(str, Enum):
    """時間足の列挙型

    MT5対応の全19時間足（M1〜D1）+ W1を定義。
    分数昇順で並べる。
    """

    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"
    M6 = "M6"
    M10 = "M10"
    M12 = "M12"
    M15 = "M15"
    M20 = "M20"
    M30 = "M30"
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"
    H4 = "H4"
    H6 = "H6"
    H8 = "H8"
    H12 = "H12"
    D1 = "D1"
    W1 = "W1"

    def minutes(self) -> int:
        """時間足を分単位で返す

        Returns:
            int: 分単位の値
        """
        mapping = {
            "M1": 1,
            "M2": 2,
            "M3": 3,
            "M4": 4,
            "M5": 5,
            "M6": 6,
            "M10": 10,
            "M12": 12,
            "M15": 15,
            "M20": 20,
            "M30": 30,
            "H1": 60,
            "H2": 120,
            "H3": 180,
            "H4": 240,
            "H6": 360,
            "H8": 480,
            "H12": 720,
            "D1": 1440,
            "W1": 10080,
        }
        return mapping[self.value]

    @classmethod
    def standard_mtf(cls) -> list["Timeframe"]:
        """標準MTF分析用の時間足リストを返す

        Returns:
            list[Timeframe]: MTF分析用時間足リスト
        """
        return [cls.M15, cls.H1, cls.H4, cls.H8, cls.D1, cls.W1]

    @classmethod
    def get_higher_timeframes(cls, tf: "Timeframe") -> list["Timeframe"]:
        """指定時間足より長い時間足リストを返す

        Args:
            tf: 基準時間足

        Returns:
            list[Timeframe]: より長い時間足のリスト
        """
        all_tfs = list(cls)
        idx = all_tfs.index(tf)
        return all_tfs[idx + 1 :]

    @classmethod
    def all_trading(cls) -> list["Timeframe"]:
        """W1を除く全トレーディング時間足を返す

        Returns:
            list[Timeframe]: M1〜D1の19時間足
        """
        return [tf for tf in cls if tf != cls.W1]


class SignalType(str, Enum):
    """シグナル種別"""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class ConfidenceLevel(str, Enum):
    """確度レベル

    65%以上: HIGH（自動実行）
    50-65%: MEDIUM（承認必要）
    50%未満: LOW（待機）
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @classmethod
    def from_confidence(cls, confidence: float) -> "ConfidenceLevel":
        """確度値からレベルを判定

        Args:
            confidence: 確度値（0.0-1.0）

        Returns:
            ConfidenceLevel: 対応するレベル
        """
        if confidence >= 0.65:
            return cls.HIGH
        elif confidence >= 0.50:
            return cls.MEDIUM
        return cls.LOW


class ExitReason(str, Enum):
    """決済理由"""

    STOP_LOSS = "SL_HIT"
    TAKE_PROFIT = "TP_HIT"
    TAKE_PROFIT_1R = "TP_1R"
    TAKE_PROFIT_2R = "TP_2R"
    BREAKEVEN = "BE_HIT"
    TRAILING_STOP = "TRAIL_HIT"
    TIME_EXIT = "TIME"
    MANUAL = "MANUAL"
    SIGNAL_REVERSAL = "SIGNAL_REV"
    STAGNATION = "STAGNATION"
    TAKE_PROFIT_EARLY = "TP_EARLY"
    FORCE_CLOSE = "FORCE_CLOSE"
    EXTERNAL_CLOSE = "EXTERNAL_CLOSE"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    STOP_OUT = "STOP_OUT"
    GHOST_CLEANUP = "GHOST_CLEANUP"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    PRE_EVENT_CLOSE = "PRE_EVENT"


class TrendDirection(str, Enum):
    """トレンド方向"""

    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class MarketSession(str, Enum):
    """市場セッション"""

    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEWYORK = "NEWYORK"
    OVERLAP = "OVERLAP"


class MarketRegime(str, Enum):
    """相場レジーム

    相場の「型」を4分類し、適切な戦略を選択するために使用。
    """

    TREND = "TREND"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"



class TradingMode(str, Enum):
    """トレーディングモード"""

    PRODUCTION = "PRODUCTION"
    DEMO = "DEMO"
    BACKTEST = "BACKTEST"


class OrderStatus(str, Enum):
    """注文ステータス"""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
