# AutoTraderV4 コード品質レビューレポート

**レビュー日**: 2026-02-07
**対象**: src/autotrader/ 全体
**レビュアー**: code-reviewer

---

## エグゼクティブサマリー

| 重要度 | 件数 |
|--------|------|
| CRITICAL | 2 |
| HIGH | 6 |
| MEDIUM | 8 |
| **合計** | **16** |

全体的なコード品質は中程度。型ヒント・docstringは概ね記載されており、モジュール分割も適切。
ただし、**確実なバグ2件**と**設計上のリスク6件**が発見された。

---

## CRITICAL問題

### C-1: PositionManager._check_trailing で SELL方向のトレーリングが highest_price を使用（バグ）

**ファイル**: `src/autotrader/decision/unified/position_manager.py:455-469`

```python
# SELL方向の場合
new_sl = position.highest_price + trail_distance
if new_sl < position.current_sl:
    position.current_sl = new_sl
    return ManagementAction.update_sl(
        new_sl,
        f"トレーリング: {new_sl:.5f}（最安{position.highest_price:.5f}）",
    )
```

**問題**: `ManagedPosition.update_price()` では SELL方向の場合 `highest_price` に最安値を格納している（`min(self.highest_price, current_price)`）。にもかかわらず、ログメッセージは「最安」と表示しているが、フィールド名は `highest_price` である。
`highest_price` フィールドの意味がBUY時は「最高価格」、SELL時は「最安価格」とオーバーロードされており、コードの可読性と保守性に重大な問題がある。さらに、`ManagedPosition.update_price()` でSELL方向の初期化は:

```python
if self.highest_price == 0.0:
    self.highest_price = current_price
self.highest_price = min(self.highest_price, current_price)
```

`highest_price` のデフォルト値は `0.0` だが、価格が常に正なので `min(0.0, current_price)` は常に `0.0` を返す。つまり **SELL方向の `highest_price` は永遠に `0.0` のまま** になるバグがある。

**影響**: SELL方向のトレーリングストップが全く機能しない。SLが `0.0 + trail_distance` という不正な値になり、初回で即座にSL更新が停止する（`0.03` < 初期SL は常にTrue になるため、SLが極端に低い値に設定される可能性がある）。

**修正案**: `highest_price` を `best_price` に改名し、初期値をエントリー価格に設定する。またはBUY/SELL で別フィールド（`highest_price` / `lowest_price`）を使用する。

---

### C-2: RiskManager.can_trade のクールダウン計算で日跨ぎ未考慮

**ファイル**: `src/autotrader/decision/unified/trade_bot.py:159-169`

```python
if self._last_trade_time is not None:
    cooldown = timedelta(minutes=self.config.cooldown_minutes)
    if timestamp - self._last_trade_time < cooldown:
        remaining = (
            self._last_trade_time + cooldown - timestamp
        ).seconds // 60
        return False, f"クールダウン中(残{remaining}分)"
```

**問題**: `timestamp - self._last_trade_time` が負の値になる可能性がある。`reset_daily()` で `_last_trade_time` はリセットされない。バックテスト時にタイムスタンプが不連続（週末スキップ等）の場合、金曜夜のトレード後、月曜朝の `timestamp` が `_last_trade_time` より大幅に離れていても問題ないが、逆にデータ順序が保証されない場合、`timedelta` の比較が不正になる。

また、`(self._last_trade_time + cooldown - timestamp).seconds` は `timedelta` の `.seconds` 属性を使用しているが、これは日数部分を無視する。例えば cooldown=5分, 経過=1日5分 の場合、`.seconds` は 300 を返し、まだクールダウン中と誤判定する可能性は低いものの、`.total_seconds()` を使うのが正確。

**影響**: 通常運用では問題になりにくいが、エッジケースで誤判定の可能性あり。

---

## HIGH問題

### H-1: Position エンティティが frozen=True だが simulator で属性変更を試みている可能性

**ファイル**: `src/autotrader/core/entities.py:130-152`, `src/autotrader/backtest/simulator.py:275-342`

**問題**: `Position` は `model_config = ConfigDict(frozen=True)` で定義されている。`TradeSimulator._close_position()` は Position 自体を変更しないが、`_open_position()` で作成した Position を `state.open_positions` リストに追加し、後で `open_positions` リストフィルタリングで削除している。現状はimmutableパターンを守っているが、Positionに `unrealized_pnl` フィールドがあり、`_update_equity` で更新する場合にfrozen制約に違反する可能性がある。

**確認事項**: `_update_equity` の実装を確認する必要がある。frozen=True のPydanticモデルの属性変更は RuntimeError を発生させる。

---

### H-2: TimeframeEvaluator._calculate_score で uptrend/downtrend の排他性が保証されない

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py:213-222`

```python
uptrend = close > sma_20
downtrend = close < sma_20
full_uptrend = close > sma_20 > sma_50
full_downtrend = close < sma_20 < sma_50

if not uptrend and not downtrend:
    return 0.0, 0.0, ["トレンドなし"]
```

**問題**: `close == sma_20` の場合、`uptrend=False`, `downtrend=False` でHOLDになるのは正しい。しかし後続の `if/elif` チェーンで `full_uptrend and macd_bullish` と `full_downtrend and macd_bearish` の両方が True になることはないが、`uptrend and macd_bullish` と `downtrend and macd_bearish` が同時に True になることもない（uptrendとdowntrendは排他的）。
ただし、最後の `elif full_uptrend or uptrend:` と `elif full_downtrend or downtrend:` は冗長。`full_uptrend` は `uptrend` の部分集合なので、`full_uptrend or uptrend` は `uptrend` と等価。

**影響**: 機能的なバグではないが、ロジックの意図が不明確。

---

### H-3: TP/SL比率が3箇所で設定され上書き関係が複雑

**ファイル**:
- `src/autotrader/decision/unified/mode_selector.py:87-110` (MODE_PLANS)
- `src/autotrader/decision/unified/strategies/scalp.py:46` (TIMEFRAMES)
- `src/autotrader/decision/unified/timeframe_evaluator.py:720-729` (tp_sl_ratios)

**問題**: TP/SL比率は以下の3箇所で設定されている:
1. `TradingModeSelector.MODE_PLANS` の `tp_sl_ratio_range` (Plan経由)
2. 各Strategyの `TIMEFRAMES.tp_sl_ratio_range`
3. `TimeframeEvaluator._calculate_sl_tp` のハードコード `tp_sl_ratios`

`_calculate_sl_tp` では:
- まずハードコード値を使用
- `plan` が渡された場合は `plan.get_recommended_tp_sl_ratio()` で上書き

convergentアーキテクチャでは `_generate_signal_convergent()` 内で `evaluators[tf].evaluate(row, candle, plan)` に plan=None でないが、plan はStrategyから取得されるのではなく mode_selector 経由。
newアーキテクチャでは `_generate_signal_new()` 内で plan が渡される。

MEMORY.mdの記録と一致するが、この複雑な上書き関係は今後の修正で容易に壊れるリスクがある。

**推奨**: TP/SL比率の設定を1箇所に一元化する。

---

### H-4: SimulatorConfig が存在せず、ハードコード値が散在

**ファイル**: `src/autotrader/backtest/simulator.py`

`TradeSimulator` は `SimulatorConfig` を受け取るが、pip_value, spread_pips, slippage_pips, commission_per_lot 等がconfig内で管理されている。一方、`_open_position` 内のマージンチェック:

```python
required_margin = entry_price * volume * 10000 / 25  # レバ25倍想定
```

レバレッジ `25` がハードコードされている。

**影響**: レバレッジの変更が困難。異なるブローカー条件でのテストが不可。

---

### H-5: TradeSimulator._close_position でリスト内包表記によるオープンポジション削除がO(n)

**ファイル**: `src/autotrader/backtest/simulator.py:325-336`

```python
self.state.open_positions = [
    p for p in self.state.open_positions
    if p.position_id != position.position_id
]

for strat_id, positions in self.state.positions_by_strategy.items():
    self.state.positions_by_strategy[strat_id] = [
        p for p in positions
        if p.position_id != position.position_id
    ]
```

**問題**: ポジション削除時に毎回新しいリストを作成している。`state.open_positions` はリストであるためO(n)。`positions_by_strategy` の全戦略を走査するためO(strategies * positions)。max_positions が小さいため実害は少ないが、`dict` をキーに `position_id` で管理すればO(1)に改善可能。

**影響**: パフォーマンス上の問題（大量ポジション時のみ）。

---

### H-6: ModeAwareScoreConsensus.consolidate 内で毎回 TimeframeRouter をインスタンス化

**ファイル**: `src/autotrader/decision/unified/mode_aware_consensus.py:174-176`

```python
def consolidate(self, tf_signals, plan):
    from autotrader.decision.unified.timeframe_router import TimeframeRouter
    router = TimeframeRouter()
    tf_set = router.route(plan)
```

**問題**:
1. 関数内import（遅延import）は PEP8/プロジェクト規約違反
2. 毎回新しい `TimeframeRouter` インスタンスを生成しており無駄
3. `consolidate()` はシグナル生成のたびに呼ばれるため、パフォーマンスへの影響がある

**推奨**: `__init__` でインスタンスを保持し、importをファイル先頭に移動する。

---

## MEDIUM問題

### M-1: BotState の daily リセットが UnifiedTradeBot 内で呼ばれていない

**ファイル**: `src/autotrader/decision/unified/trade_bot.py`

`BotState.reset_daily()` は `daily_pnl` と `daily_trades` をリセットするが、`_generate_signal_new()` や `_generate_signal_convergent()` では `self.risk_manager.reset_daily(py_time)` のみ呼ばれ、`self.state.reset_daily()` は呼ばれていない。

**影響**: `BotState.daily_pnl` と `daily_trades` が累積し続ける。PositionSizer で `current_dd_pct` は `state.update_pnl` で正しく更新されるが、daily系のフィールドは不正確。

---

### M-2: ConsolidatedSignal と ConsensusResult で類似した構造が重複

**ファイル**: `src/autotrader/decision/unified/signal_consolidator.py`, `src/autotrader/decision/unified/mode_aware_consensus.py`

`ConsolidatedSignal` と `ConsensusResult` はどちらもシグナル統合結果を表すが、フィールドが異なり変換が必要。新旧アーキテクチャの混在による重複。

---

### M-3: HardGuard/SoftGuard のコンテキストが dict[str, Any] 型

**ファイル**: `src/autotrader/constraint/hard_guard.py:181`, `src/autotrader/constraint/soft_guard.py:196`

```python
def check(self, context: dict, is_entry: bool = True) -> HardGuardResult:
```

**問題**: `context` が型なし dict であり、必須キーが不明。TypedDict または dataclass を使用すべき。

---

### M-4: PositionManager.reset() で _partial_closed セットがクリアされない可能性

**ファイル**: `src/autotrader/decision/unified/position_manager.py`

`reset()` メソッドの有無・内容を確認する必要がある。`_partial_closed_1r` と `_partial_closed_2r` はポジションID を記録するが、ポジションが `unregister_position()` で削除されてもセットからは削除されない。長期運用でメモリリーク。

---

### M-5: TimeframeEvaluator.evaluate 内の dead code (pass文)

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py:141,153`

```python
# 時間帯フィルター無効（トレード数確保のため）
pass

# SMCスコアリング（無効化 - 性能低下のため）
pass
```

**問題**: コメントと `pass` 文が残っている。無効化されたコードはコメントアウトではなく削除すべき。

---

### M-6: StrategySelector.choose で edge_score_margin チェックが pass で無効化

**ファイル**: `src/autotrader/decision/unified/strategy_selector.py:100-106`

```python
if margin < self.config.edge_score_margin:
    # 差分が小さい場合は選択に慎重になる
    # ただしエントリーは許可（HOLDにはしない）
    pass
```

**問題**: edge_score_margin チェックが実質無効化されている。設定値が存在するのに使用されていない。

---

### M-7: UnifiedBotConfig で use_new_architecture と use_convergent_architecture の優先関係が暗黙的

**ファイル**: `src/autotrader/decision/unified/config.py:157-160`

```python
use_new_architecture: bool = True
use_convergent_architecture: bool = True  # use_new_architectureより優先
```

**問題**: 優先関係がコメントでのみ記述されている。`generate_signal()` の実装では正しく処理されているが、設定の組み合わせが不明確。Enum で1つのモードを選択する方式が望ましい。

---

### M-8: SimulatorConfig.default_volume が PositionSizer の計算結果を無視する可能性

**ファイル**: `src/autotrader/backtest/simulator.py`

`_open_position` では `volume = self.config.default_volume` を使用しているが、Signal にはlot情報が含まれていない。PositionSizer で計算された lot がシミュレーターに渡される経路が不明確。

---

## 全体的なコード品質評価

### 良い点
- PEP8準拠率が高い（型ヒント、docstring完備）
- Pydantic/dataclass によるimmutableエンティティ設計
- 責務の分離が概ね適切（evaluator, consolidator, sizer 等）
- テストファイルが主要コンポーネントに対して存在

### 改善すべき点
- 新旧アーキテクチャの混在（legacy, new, convergent）が複雑性を増大
- TP/SL比率の設定が多重になっており、一元管理が必要
- SELLポジションのトレーリング機能に重大なバグ（C-1）
- HardGuard/SoftGuard のコンテキストに型安全性がない
- dead code（pass文、無効化されたチェック）が散在
