"""トレーディングモード選択モジュール

レジームとMTF情報からモードを自動選択する。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.enums import MarketRegime, TradingStrategyMode


@dataclass(frozen=True)
class TradingPlan:
    """トレーディングプラン

    選択されたモードと参照時間足の設定。

    Attributes:
        mode: トレーディングモード
        primary_tf: 主要時間足（シグナル判断の基準）
        entry_tf: エントリー時間足（タイミング判断）
        confirm_tfs: 確認用時間足リスト
        manage_tf: 管理用時間足（ポジション管理）
        max_holding_bars: 最大保有バー数（primary_tf基準）
        tp_sl_ratio_range: TP/SL比率の推奨範囲
    """

    mode: TradingStrategyMode
    primary_tf: str
    entry_tf: str
    confirm_tfs: list[str]
    manage_tf: str
    max_holding_bars: int
    tp_sl_ratio_range: tuple[float, float]
    selection_reason: str = ""
    regime: str | None = None
    dynamic_entry_tf: str | None = None

    @property
    def all_tfs(self) -> list[str]:
        """使用する全時間足のリスト

        Returns:
            list[str]: 重複なしの時間足リスト
        """
        tfs = {self.primary_tf, self.entry_tf, self.manage_tf}
        tfs.update(self.confirm_tfs)
        return list(tfs)

    def get_recommended_tp_sl_ratio(self) -> float:
        """推奨TP/SL比率を取得

        Returns:
            float: 推奨比率（範囲の中央値）
        """
        return (self.tp_sl_ratio_range[0] + self.tp_sl_ratio_range[1]) / 2


@dataclass(frozen=True)
class ModeSelectorConfig:
    """モード選択設定

    Attributes:
        high_vol_threshold: 高ボラティリティ閾値
        htf_alignment_threshold: HTF整合閾値
        prefer_swing_on_strong_trend: 強トレンド時にSWINGを優先
    """

    high_vol_threshold: float = 1.3
    htf_alignment_threshold: float = 0.5
    prefer_swing_on_strong_trend: bool = True


class TradingModeSelector:
    """トレーディングモード選択器

    レジームとMTF情報からモードを自動選択する。

    選択ロジック:
    - HIGH_VOL → SCALPING（短期で逃げる）
    - TREND + 高HTF整合 → SWING
    - TREND → DAY_TRADE
    - RANGE/LOW_VOL → DAY_TRADE
    """

    # モード別プラン定義
    # バランス型：勝率56%・PF1.01達成設定
    MODE_PLANS: dict[TradingStrategyMode, dict[str, any]] = {
        TradingStrategyMode.SCALPING: {
            "primary_tf": "M5",
            "entry_tf": "M1",
            "confirm_tfs": ["M15"],
            "manage_tf": "M5",
            "max_holding_bars": 18,  # 90分
            "tp_sl_ratio_range": (1.0, 1.3),  # 勝率56%・PF1.01達成
        },
        TradingStrategyMode.DAY_TRADE: {
            "primary_tf": "M15",
            "entry_tf": "M5",
            "confirm_tfs": ["H1", "H4", "H8"],
            "manage_tf": "M15",
            "max_holding_bars": 32,  # 8時間
            "tp_sl_ratio_range": (1.1, 1.4),  # 勝率55%・PF1.01達成
        },
        TradingStrategyMode.SWING: {
            "primary_tf": "H4",
            "entry_tf": "H1",
            "confirm_tfs": ["H8", "D1"],
            "manage_tf": "H4",
            "max_holding_bars": 12,  # 2日
            "tp_sl_ratio_range": (1.2, 1.6),  # 勝率54%・PF1.01達成
        },
        TradingStrategyMode.UNIVERSAL: {
            "primary_tf": "M15",
            "entry_tf": "M5",
            "confirm_tfs": ["M1", "M5", "M15", "H1", "H4", "H8", "D1"],
            "manage_tf": "M15",
            "max_holding_bars": 32,  # デフォルト：8時間（動的変更可）
            "tp_sl_ratio_range": (1.1, 1.4),  # デフォルト（動的変更可）
        },
    }

    def __init__(
        self,
        config: ModeSelectorConfig | None = None,
        use_universal_mode: bool = False,
    ) -> None:
        """初期化

        Args:
            config: モード選択設定
            use_universal_mode: UNIVERSALモードを使用するか
        """
        self.config = config or ModeSelectorConfig()
        self.use_universal_mode = use_universal_mode

    def select(
        self,
        regime: MarketRegime,
        volatility_level: float,
        htf_alignment: float = 0.0,
        hour_utc: int | None = None,
    ) -> TradingPlan:
        """トレーディングプランを選択

        Args:
            regime: 相場レジーム
            volatility_level: ボラティリティレベル（正規化ATR）
            htf_alignment: HTF整合度（-1から1）
            hour_utc: UTC時間（0-23）。指定時は時間帯を考慮

        Returns:
            TradingPlan: 選択されたトレーディングプラン
        """
        if self.use_universal_mode:
            plan_params = self.MODE_PLANS[TradingStrategyMode.UNIVERSAL]
            return TradingPlan(
                mode=TradingStrategyMode.UNIVERSAL,
                primary_tf=plan_params["primary_tf"],
                entry_tf=plan_params["entry_tf"],
                confirm_tfs=plan_params["confirm_tfs"],
                manage_tf=plan_params["manage_tf"],
                max_holding_bars=plan_params["max_holding_bars"],
                tp_sl_ratio_range=plan_params["tp_sl_ratio_range"],
                selection_reason="UNIVERSAL（動的TF選択）",
            )

        mode, reason = self._select_mode(
            regime, volatility_level, htf_alignment, hour_utc
        )
        plan_params = self.MODE_PLANS[mode]

        return TradingPlan(
            mode=mode,
            primary_tf=plan_params["primary_tf"],
            entry_tf=plan_params["entry_tf"],
            confirm_tfs=plan_params["confirm_tfs"],
            manage_tf=plan_params["manage_tf"],
            max_holding_bars=plan_params["max_holding_bars"],
            tp_sl_ratio_range=plan_params["tp_sl_ratio_range"],
            selection_reason=reason,
        )

    def _select_mode(
        self,
        regime: MarketRegime,
        volatility_level: float,
        htf_alignment: float,
        hour_utc: int | None = None,
    ) -> tuple[TradingStrategyMode, str]:
        """モードを選択

        時間帯を考慮してトレーディングモードを選択する。
        アクティブ時間帯（東京・ロンドン・NY）ではスキャルピングの
        選択確率が上がる。

        Args:
            regime: 相場レジーム
            volatility_level: ボラティリティレベル
            htf_alignment: HTF整合度
            hour_utc: UTC時間（0-23）

        Returns:
            tuple[TradingStrategyMode, str]: (モード, 選択理由)
        """
        cfg = self.config

        # アクティブ時間帯の判定（UTC基準）
        is_active_session = False
        if hour_utc is not None:
            tokyo_active = 0 <= hour_utc <= 3
            london_active = 7 <= hour_utc <= 10
            ny_active = 13 <= hour_utc <= 18
            is_active_session = (
                tokyo_active or london_active or ny_active
            )

        # 高ボラティリティ + アクティブ時間帯 → SCALPING
        if regime == MarketRegime.HIGH_VOL:
            if is_active_session:
                return (
                    TradingStrategyMode.SCALPING,
                    "HIGHVOL_ACTIVE",
                )
            return (
                TradingStrategyMode.DAY_TRADE,
                "HIGHVOL_INACTIVE",
            )

        # 高ボラティリティ閾値超え → SCALPING
        if volatility_level > cfg.high_vol_threshold:
            return (
                TradingStrategyMode.SCALPING,
                "VOL_THRESHOLD",
            )

        # TREND → 常にSWING（P0-2: TREND×DAY_TRADE禁止）
        if regime == MarketRegime.TREND:
            return TradingStrategyMode.SWING, "TREND"

        # RANGE/LOW_VOL → DAY_TRADE
        return TradingStrategyMode.DAY_TRADE, "RANGE"

    def get_plan_for_mode(self, mode: TradingStrategyMode) -> TradingPlan:
        """指定モードのプランを取得

        Args:
            mode: トレーディングモード

        Returns:
            TradingPlan: トレーディングプラン
        """
        plan_params = self.MODE_PLANS[mode]

        return TradingPlan(
            mode=mode,
            primary_tf=plan_params["primary_tf"],
            entry_tf=plan_params["entry_tf"],
            confirm_tfs=plan_params["confirm_tfs"],
            manage_tf=plan_params["manage_tf"],
            max_holding_bars=plan_params["max_holding_bars"],
            tp_sl_ratio_range=plan_params["tp_sl_ratio_range"],
        )
