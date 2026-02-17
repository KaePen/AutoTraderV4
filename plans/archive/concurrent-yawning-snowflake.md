# UnifiedTradeBot包括的リファクタリング計画

## 概要

`plans/unified_trade_bot_改善案.md`の全内容を取り込み、トレーディングボットのアーキテクチャを根本的に改善する。

## 改訂版フロー（目標状態）

```
データ入力(1M〜1D; 必要TFを動的選択)
    │
    ▼
IndicatorCalculator（必要TFのみ計算）
    │
    ▼
MarketRegimeDetector（相場レジーム推定）【新規】
    │
    ▼
TimeframeRouter（entry/primary/confirm/manage TFの動的選択）【新規】
    │
    ▼
TradingModeSelector（modeとplanの決定）【新規】
    │
    ▼
RiskManager.can_trade()（日次損失・DD・クールダウン等のゲーティング）
    │
    ▼
TimeframeEvaluator.evaluate()（planに従い必要TFのみ評価）
    │
    ▼
ModeAwareScoreConsensus（単一規範によるシグナル統合）【新規】
    │
    ├── NG → HOLD（理由付与）
    │
    ▼
PositionSizer（risk_budget ÷ SL距離 → lot）【新規】
    │
    ▼
TradeExecution（約定・スプレッド反映）
    │
    ▼
PositionManager（TimeExit/Trail/部分利確/撤退）【新規】
    │
    ▼
結果ログ・集計
```

---

## フェーズ1: PositionSizer実装【最優先】

### 目的
リスク量に応じたロット数の動的算出。同一勝率でも資本曲線の形状を大きく変える。

### 作成ファイル
- `src/autotrader/decision/unified/position_sizer.py`（新規）
- `src/autotrader/core/interfaces/position_sizing.py`（新規）

### 修正ファイル
- `src/autotrader/backtest/simulator.py`
- `src/autotrader/decision/unified/trade_bot.py`

### 主要クラス

```python
@dataclass(frozen=True)
class SizingContext:
    equity: float              # 現在の有効証拠金
    sl_pips: float             # SL距離（pips）
    confidence: float          # シグナル確度（0-1）
    regime: MarketRegime       # 相場レジーム
    consecutive_losses: int    # 連敗数
    current_dd_pct: float      # 現在のドローダウン率

@dataclass(frozen=True)
class SizingResult:
    lot: float                 # 算出ロット数
    risk_budget: float         # リスク予算（通貨）
    risk_adjust: float         # リスク調整係数
    reasoning: str             # 算出理由

class PositionSizer:
    """lot = (equity × risk_pct × risk_adjust) / (sl_pips × pip_value)"""

    def calculate(self, context: SizingContext) -> SizingResult
    def _calculate_risk_adjust(self, context) -> float  # 確度/レジーム/DD/連敗で調整
```

### リスク調整ロジック
- 確度調整: 0.7以上→1.2倍、0.5以下→0.5倍
- レジーム調整: TREND=1.0, RANGE=0.7, HIGH_VOL=0.5
- DD調整: 閾値超過で減額（最大70%減）
- 連敗調整: 5連敗以上で0.5倍

---

## フェーズ2: MarketRegimeDetector実装

### 目的
相場レジームの自動判定。後段の戦術選択、フィルタ閾値、TP/SL設計を条件付きに切り替える。

### 作成ファイル
- `src/autotrader/calculator/features/regime_detector.py`（新規）

### 修正ファイル
- `src/autotrader/decision/unified/trade_bot.py`

### 主要クラス

```python
@dataclass(frozen=True)
class RegimeResult:
    regime: MarketRegime       # TREND, RANGE, HIGH_VOL, LOW_VOL
    trend_strength: float      # トレンド強度（0-1）
    volatility_level: float    # ボラティリティレベル（正規化ATR）
    adx: float                 # ADX値
    confidence: float          # 判定確度
    reasoning: str             # 判定理由

class MarketRegimeDetector:
    """ADX、正規化ATR、MA整列度からレジームを判定"""

    def detect(self, high, low, close, adx) -> RegimeResult
    def detect_from_row(self, row: pd.Series) -> RegimeResult  # 事前計算済みデータ用
```

### 判定ロジック（優先度順）
1. HIGH_VOL: 正規化ATR > 1.5 かつ ADX < 25
2. TREND: ADX >= 20 かつ MA整列
3. LOW_VOL: 正規化ATR < 0.7
4. RANGE: その他

### 既存活用
- `VolatilityFeatures.normalized_atr`, `volatility_regime`
- `TrendFeatures.trend_direction`, `ma_alignment`

---

## フェーズ3: TimeframeRouter + TradingModeSelector実装

### 目的
戦術（スキャルピング/デイトレード/スイング）と参照TFの動的選択。時間スケール不整合を根治。

### 作成ファイル
- `src/autotrader/decision/unified/mode_selector.py`（新規）
- `src/autotrader/decision/unified/timeframe_router.py`（新規）

### 修正ファイル
- `src/autotrader/decision/unified/trade_bot.py`
- `src/autotrader/decision/unified/config.py`

### 主要クラス

```python
class TradingMode(str, Enum):
    SCALPING = "scalping"      # 短期：M5-M15基準
    DAY_TRADE = "day_trade"    # 中期：M15-H1基準
    SWING = "swing"            # 長期：H4-D1基準

@dataclass(frozen=True)
class TradingPlan:
    mode: TradingMode
    primary_tf: str            # 主要時間足
    entry_tf: str              # エントリー時間足
    confirm_tfs: list[str]     # 確認用時間足リスト
    manage_tf: str             # 管理用時間足
    max_holding_bars: int      # 最大保有バー数
    tp_sl_ratio_range: tuple[float, float]  # TP/SL比率の推奨範囲

class TradingModeSelector:
    """レジームとMTF情報からモードを自動選択"""

    def select(self, regime, volatility_level, htf_alignment) -> TradingPlan

class TimeframeRouter:
    """TradingPlanに基づいて必要なTFセットを構築"""

    def route(self, plan: TradingPlan) -> TimeframeSet
    def get_required_tfs(self, plan: TradingPlan) -> list[str]
```

### モード別設定

| モード | primary_tf | entry_tf | confirm_tfs | max_holding | TP/SL比率 |
|--------|-----------|----------|-------------|-------------|----------|
| SCALPING | M5 | M1 | [M15] | 18本(90分) | 1.0-1.5 |
| DAY_TRADE | M15 | M5 | [H1, H4] | 32本(8時間) | 1.5-2.5 |
| SWING | H4 | H1 | [D1] | 12本(2日) | 2.0-4.0 |

### 選択ロジック
- HIGH_VOL → SCALPING（短期で逃げる）
- TREND + 高HTF整合 → SWING
- TREND → DAY_TRADE
- RANGE/LOW_VOL → DAY_TRADE

---

## フェーズ4: ModeAwareScoreConsensus実装

### 目的
ALL/MAJORITY/WEIGHTEDを廃止し、単一コンセンサスルールに統合。

### 作成ファイル
- `src/autotrader/decision/unified/mode_aware_consensus.py`（新規）

### 修正ファイル
- `src/autotrader/decision/unified/signal_consolidator.py`
- `src/autotrader/decision/unified/config.py`（ConsensusRule enum廃止）

### 主要クラス

```python
@dataclass(frozen=True)
class ConsensusResult:
    direction: SignalType      # BUY/SELL/HOLD
    score: float               # 統合スコア
    threshold: float           # 適用閾値
    aligned_tfs: list[str]     # 同方向TFリスト
    reasoning: str             # 判断理由

class ModeAwareScoreConsensus:
    """TradingPlanに従って重み付けスコアを計算"""

    # モード別閾値
    MODE_THRESHOLDS = {SCALPING: 3.0, DAY_TRADE: 4.0, SWING: 5.0}

    # TF役割別重み
    ROLE_WEIGHTS = {primary: 3.0, entry: 2.0, confirm: 1.5, other: 0.5}

    def consolidate(self, tf_signals, plan) -> ConsensusResult
```

### 計算手順
1. 各TFの方向を{-1, 0, +1}に正規化
2. TF役割（primary/entry/confirm）に応じた重みを付与
3. 加重合計スコアを計算
4. モード別閾値で判定

---

## フェーズ5: PositionManager統合

### 目的
ExitManager + PartialCloseManagerの統合。保有中の戦術を一元管理。

### 作成ファイル
- `src/autotrader/decision/unified/position_manager.py`（新規）

### 修正ファイル
- `src/autotrader/backtest/simulator.py`

### 主要クラス

```python
@dataclass(frozen=True)
class ManagementAction:
    action_type: str           # "hold", "update_sl", "partial_close", "full_close"
    close_ratio: float         # 決済比率（0=SL更新のみ、1.0=全決済）
    new_sl: float | None       # 新SL価格
    reason: str                # 理由

class PositionManager:
    """保有中の管理を統合"""

    def register_position(self, position) -> None
    def unregister_position(self, position_id) -> None
    def evaluate(self, position, current_price, current_time, atr, plan, indicators) -> ManagementAction
```

### 管理機能（優先度順）
1. SL/TP到達チェック
2. Time exit（mode依存の最大保有時間）
3. シグナル反転撤退
4. 部分利確（R値ベース: 1R/2R/3R以降トレーリング）
5. トレーリング更新（ATRベース、建値移動）

---

## フェーズ6: UnifiedTradeBot統合リファクタリング

### 目的
全コンポーネントの統合。既存のプリセット・単独フィルタを廃止。

### 修正ファイル
- `src/autotrader/decision/unified/trade_bot.py`（大幅改修）
- `src/autotrader/decision/unified/config.py`（プリセット削除）
- `src/autotrader/backtest/simulator.py`

### 削除対象

| 項目 | ファイル | 理由 |
|------|---------|------|
| `high_win_rate_preset()` | config.py | ModeSelector自動選択へ移行 |
| `monthly_target_preset()` | config.py | 同上 |
| `aggressive_monthly_preset()` | config.py | 同上 |
| `ultra_aggressive_preset()` | config.py | 同上 |
| `ConsensusRule` enum | config.py | ModeAwareConsensusへ統合 |
| `_check_adx_filter()` | trade_bot.py | RegimeDetectorへ統合 |
| `_check_htf_trend_alignment()` | trade_bot.py | Consensusへ集約 |

### 統合後のUnifiedTradeBot.generate_signal()フロー

```python
def generate_signal(self, current_time, candle):
    # 1. 日次リセット
    self.risk_manager.reset_daily_if_needed(current_time)

    # 2. レジーム検出
    regime_result = self.regime_detector.detect_from_row(row)

    # 3. モード・プラン選択
    plan = self.mode_selector.select(
        regime=regime_result.regime,
        volatility_level=regime_result.volatility_level,
        htf_alignment=self._get_htf_alignment(current_time),
    )

    # 4. TFセット取得
    tf_set = self.tf_router.route(plan)

    # 5. リスク管理チェック（ゲーティング）
    can_trade, reason = self.risk_manager.can_trade(current_time)
    if not can_trade:
        return ConsolidatedSignal(direction=HOLD, rationale=reason)

    # 6. 選択TFのみ評価
    tf_signals = {}
    for tf in tf_set.all_tfs:
        tf_signals[tf] = self.evaluators[tf].evaluate(row, candle)

    # 7. コンセンサス統合
    consensus = self.consensus.consolidate(tf_signals, plan)
    if consensus.direction == HOLD:
        return ConsolidatedSignal(direction=HOLD, rationale=consensus.reasoning)

    # 8. SL/TP計算（primary_tf由来）
    primary_signal = tf_signals[plan.primary_tf]
    sl_pips = primary_signal.sl_pips
    tp_pips = sl_pips * self._get_tp_sl_ratio(plan)

    # 9. ポジションサイジング
    sizing_result = self.position_sizer.calculate(SizingContext(
        equity=self.state.equity,
        sl_pips=sl_pips,
        confidence=consensus.score / consensus.threshold,
        regime=regime_result.regime,
        consecutive_losses=self.state.consecutive_losses,
        current_dd_pct=self.state.current_dd_pct,
    ))

    return ConsolidatedSignal(
        direction=consensus.direction,
        confidence=consensus.score,
        primary_tf=plan.primary_tf,
        aligned_tfs=consensus.aligned_tfs,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        lot=sizing_result.lot,
        plan=plan,
        rationale=f"{consensus.reasoning}, lot={sizing_result.lot:.2f}",
    )
```

---

## フェーズ7: テスト・後方互換性確保

### 作成ファイル
- `tests/unit/decision/unified/test_position_sizer.py`
- `tests/unit/decision/unified/test_regime_detector.py`
- `tests/unit/decision/unified/test_mode_selector.py`
- `tests/unit/decision/unified/test_mode_aware_consensus.py`
- `tests/unit/decision/unified/test_position_manager.py`
- `tests/integration/test_unified_bot_refactored.py`

### 後方互換性
- 既存の`scripts/run_backtest.py`が動作すること
- WebUI API（`src/autotrader/web/routers/backtest.py`）が動作すること
- 既存のバックテスト結果と比較可能であること

---

## 依存関係グラフ

```
フェーズ1: PositionSizer ──────────────────────┐
                                              │
フェーズ2: MarketRegimeDetector ───────────────┤
              │                               │
              ▼                               │
フェーズ3: ModeSelector + TimeframeRouter ─────┤
              │                               │
              ▼                               │
フェーズ4: ModeAwareScoreConsensus ────────────┤
              │                               │
              ▼                               │
フェーズ5: PositionManager ────────────────────┤
              │                               │
              ▼                               ▼
フェーズ6: UnifiedTradeBot統合 ◄───────────────┘
              │
              ▼
フェーズ7: テスト・後方互換性
```

---

## リスク評価

| フェーズ | リスク | 対策 |
|---------|--------|------|
| 1 | 低 | 独立実装、既存への影響なし |
| 2 | 低 | 既存特徴量クラスを活用 |
| 3 | 中 | trade_bot統合前にユニットテスト |
| 4 | 中 | 既存Consolidatorと並行稼働させて比較 |
| 5 | 中 | 既存ExitManager/PartialCloseManagerのラッパーとして実装 |
| 6 | 高 | フェーズごとに段階的統合、回帰テスト必須 |
| 7 | 低 | テストカバレッジ80%以上確保 |

---

## 重要ファイル一覧

### 新規作成
- `src/autotrader/decision/unified/position_sizer.py`
- `src/autotrader/decision/unified/mode_selector.py`
- `src/autotrader/decision/unified/timeframe_router.py`
- `src/autotrader/decision/unified/mode_aware_consensus.py`
- `src/autotrader/decision/unified/position_manager.py`
- `src/autotrader/calculator/features/regime_detector.py`
- `src/autotrader/core/interfaces/position_sizing.py`

### 大幅修正
- `src/autotrader/decision/unified/trade_bot.py`
- `src/autotrader/decision/unified/config.py`
- `src/autotrader/backtest/simulator.py`

### 既存活用
- `src/autotrader/calculator/features/volatility_features.py`
- `src/autotrader/calculator/features/trend_features.py`
- `src/autotrader/decision/exit_manager.py`
- `src/autotrader/decision/partial_close.py`

---

## 検証方法

### 各フェーズ完了時
1. ユニットテスト実行（pytest）
2. 型チェック（mypy）
3. リンター（ruff）

### フェーズ6完了後
1. 既存バックテストスクリプトで動作確認
   ```bash
   python scripts/run_backtest.py --symbol USDJPY --start 2024-01-01 --end 2024-12-31
   ```

2. WebUIで動作確認
   ```bash
   python -m autotrader.web.main
   # ブラウザでバックテスト実行
   ```

3. 結果比較
   - 勝率、プロフィットファクター、最大ドローダウン
   - エントリー頻度、トレード数
   - モード別集計（SCALPING/DAY_TRADE/SWING）

---

## 改善案との対応関係

| 改善案の要求 | 対応フェーズ | 実装コンポーネント |
|-------------|------------|------------------|
| 保有期間（スキャ/中期等）の判断 | 3 | TradingModeSelector + TimeframeRouter |
| リスクレベルに応じたロット可変 | 1 | PositionSizer |
| データ入力の柔軟化（1M〜1D） | 3 | TimeframeRouter（動的選択） |
| コンセンサスの整理 | 4 | ModeAwareScoreConsensus（単一規範） |
| プリセット削除 | 6 | 手動プリセット撤去、planの内部自動切替へ移譲 |
| ADX単独フィルタ廃止 | 2, 6 | MarketRegimeDetectorへ統合 |
| HTF整合二重判定解消 | 4, 6 | Consensusへ集約 |
| SL/TP統合則の再定義 | 3, 6 | primary_tf由来を基本に |
| TP/SL比率の動的化 | 3 | TradingPlan.tp_sl_ratio_range |
| PositionManager | 5 | ExitManager + PartialCloseManagerの統合 |
