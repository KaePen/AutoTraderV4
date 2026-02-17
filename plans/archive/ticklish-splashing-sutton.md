# ChatGPT改善案に基づく実装計画

## Context

ChatGPTから提示された7つの改善案を、現在のベストアーキテクチャ（新アーキ: `_generate_signal_new`）に適応させる。
現在のベスト: PF 1.17, 勝率50.5%, DD 3.34%, 年間収益率17.0%。

**最大の発見**: `PositionManager`（建値移動・部分利確・トレーリング・時間決済を全実装済み）が**バックテストで未使用**。
`simulator.py`の`process_candle`はSL/TPとSIGNAL_REVERSALのみで決済しており、高度な決済ロジックが一切活用されていない。

## 提案の分類

| # | ChatGPT提案 | 判定 | 理由 |
|---|-----------|------|------|
| 1 | Exit仕様の具体化 | **実装する** | PositionManager未統合が最大のボトルネック |
| 2 | DAYの格下げ | **Phase2で検討** | ログ強化後にデータ分析して判断 |
| 3 | NoTradeStrategy | **不要** | 新アーキの多段フィルタで対応済み |
| 4 | コストモデル明文化 | **実装する** | セッション別スプレッドでリアル度向上 |
| 5 | edge_score正規化 | **適用不可** | 新アーキではedge_score不使用 |
| 6 | RegimeDetector改善 | **Phase2で検討** | 段階的に実施 |
| 7 | ログ仕様強化 | **実装する** | 全最適化の分析基盤 |

---

## Phase 1: 即時実装（3タスク）

### Task 1: ログ仕様の強化（全最適化の基盤）

**目的**: regime/mode/consensus_scoreをTrade/Signalに記録し、戦略別・レジーム別の分析を可能にする

**変更ファイル**:
- `src/autotrader/core/entities.py` — Signal/Tradeにフィールド追加
- `src/autotrader/decision/unified/trade_bot.py` — Signal生成時にメタデータ付与
- `src/autotrader/backtest/simulator.py` — Trade作成時にSignalからメタデータ引継ぎ
- `src/autotrader/backtest/metrics.py` — regime別/mode別ブレークダウン出力

**実装内容**:

1. `Signal`に追加:
```python
regime: str | None = None          # TREND/RANGE/HIGH_VOL/LOW_VOL
mode: str | None = None            # SCALPING/DAY_TRADE/SWING
consensus_score: float | None = None
```

2. `Trade`に追加:
```python
regime: str | None = None
mode: str | None = None
consensus_score: float | None = None
```

3. `_generate_signal_new`のSignal返却時にregime/mode/scoreを設定

4. `_close_position`のTrade作成時にPositionの元SignalからRegime/mode情報を引継ぎ
   - Position作成時にsignal参照を保存する必要あり

5. `metrics.py`にregime別・mode別・exit_reason別のブレークダウン関数を追加

### Task 2: PositionManager統合（最大インパクト）

**目的**: 実装済みのPositionManager（建値移動・部分利確・トレーリング・時間決済）をバックテストに統合

**変更ファイル**:
- `src/autotrader/backtest/simulator.py` — PositionManager統合
- `src/autotrader/core/entities.py` — Position拡張（mode/ATR情報の保持）

**実装内容**:

1. `SimulatorConfig`に設定追加:
```python
use_position_manager: bool = False  # デフォルトOFF（ベースライン保持）
position_manager_config: PositionManagerConfig | None = None
```

2. `TradeSimulator.__init__`でPositionManager初期化

3. `process_candle`の決済フローを拡張:
   - `use_position_manager=True`の場合:
     - 従来のSL/TPチェックの代わりにPositionManagerの`evaluate()`を呼ぶ
     - ManagementActionに基づいて決済/SL更新/部分決済を実行
     - SIGNAL_REVERSALもPositionManager経由で処理（4番目の優先度）
   - `use_position_manager=False`の場合: 既存ロジックをそのまま使用

4. `_open_position`でPositionManagerに登録
   - ATR値をSignalの`indicators_snapshot`から取得
   - mode情報もSignalから取得してTradingPlanを構築

5. 部分決済の処理追加（`_partial_close_position`メソッド新設）
   - profit_loss計算は決済比率に応じて按分
   - 残りポジションのSL更新

**PositionManagerの既存決済フロー**（統合対象）:
```
1. _check_sl()          → SL到達
2. _check_tp()          → TP到達
3. _check_time_exit()   → 時間決済（SCALP:90分, DAY:8時間, SWING:2日）
4. _check_signal_reversal() → シグナル反転（最低優先度）
5. _check_partial_close()   → 部分利確（1R:30%, 2R:30%）
6. _check_trailing()    → ATRベーストレーリング（2R以上）
```

**注意点**:
- Position は frozen=True → ManagedPosition でラップして SL 更新を管理
- ATR値は Signal の indicators_snapshot から取得（`atr_14`等のキー）
- 部分決済のロット計算にpip_valueを使用

### Task 3: セッション別スプレッドモデル

**目的**: 固定スプレッドから時間帯別可変スプレッドに対応し、バックテストのリアル度を向上

**変更ファイル**:
- `src/autotrader/config/trading_params.py` — セッション別スプレッド定義
- `src/autotrader/backtest/simulator.py` — 動的スプレッド適用

**実装内容**:

1. `TradingParams`にセッション別スプレッド追加:
```python
use_session_spread: bool = False
session_spreads: dict[str, float] = field(default_factory=lambda: {
    "tokyo": 1.2,           # 0-6 UTC
    "london": 1.0,          # 7-12 UTC
    "london_ny_overlap": 0.8, # 13-17 UTC
    "new_york": 1.2,        # 18-22 UTC
    "off_hours": 2.5,       # 23 UTC
})
```

2. `TradeSimulator`に`_get_session_spread(hour_utc)`メソッド追加

3. `_get_entry_price`/`_get_exit_price`でセッション別スプレッド使用

---

## Phase 2: データ分析後に検討

Phase 1のログ強化を実施後、バックテストでregime別・mode別の勝率/PFを分析し、以下を判断:

### Task 4: DAY_TRADE追加条件（提案2）
- 分析結果でDAY_TRADEの勝率が低い場合のみ実施
- 候補: HTF整合必須、コンセンサス閾値引き上げ（5.5→6.0）
- `trade_bot.py`の`_generate_signal_new`に条件追加

### Task 5: RegimeDetector改善（提案6）
- TREND判定にtrend_quality（0=messy, 1=clean）を追加
- MACDヒストグラムの方向一貫性、MA crossing countから算出
- `regime_detector.py`のRegimeResultに`trend_quality`フィールド追加

---

## 実装順序

```
Task 1 (ログ強化) → Task 2 (PositionManager統合) → Task 3 (セッション別スプレッド)
                                                           ↓
                                                    バックテスト実行
                                                    regime/mode別分析
                                                           ↓
                                                    Task 4/5 判断
```

## リスク管理

- 全改善は `use_xxx: bool = False` のフラグで制御（デフォルトOFF）
- ベースライン（PF 1.17, DD 3.34%）を下回る場合は即座にロールバック
- A/Bテスト: フラグON/OFFの結果を比較

## 検証方法

1. Phase 1完了後、以下のバックテストを実行:
   - ベースライン: 既存設定（全フラグOFF）
   - テスト1: ログ強化のみ（パフォーマンス影響なしを確認）
   - テスト2: PositionManager有効化
   - テスト3: セッション別スプレッド有効化
   - テスト4: 全機能有効化
2. 各テストでPF/勝率/DD/取引数を比較
3. regime別・mode別・exit_reason別のブレークダウンを分析
