# ライブアナリティクス常時表示の修正計画

## Context

WebUIのライブアナリティクスパネルがデモモード/自動トレード有効化時しか更新されない。
原因は `signals.py` ルーターが `engine.last_analysis` 等のプロパティを参照しているが、`engine.py` にこれらのプロパティが存在しないため、APIが常に「分析待機中（データなし）」を返しているため。

## 根本原因

`src/autotrader/web/routers/signals.py` が以下のプロパティを参照:
- `engine.last_analysis` → **存在しない**
- `engine.last_tick_time` → **存在しない**
- `engine.demo_mode_enabled` → **存在しない**
- `engine.signal_history` → **存在しない**

`engine.py` の `_tick()` メソッドでは `generate_signal()` の結果を非HOLDの場合のみ `_last_signal` に保存しており、全tick結果の保存（`last_analysis`）をしていない。

## 修正ファイル

`src/autotrader/live/engine.py` のみ

## 修正内容

### Change 1: `__init__` に不足プロパティを追加

`_last_signal` 定義の付近（L80）に以下を追加:

```python
self._last_analysis: ConsolidatedSignal | None = None
self._last_tick_time: datetime | None = None
self._signal_history: list[Signal] = []
```

import追加:
```python
from autotrader.decision.unified.signal_consolidator import ConsolidatedSignal
```

### Change 2: プロパティアクセサを追加

`enable_auto_trade` プロパティ（L105-113）の後に追加:

```python
@property
def last_analysis(self) -> ConsolidatedSignal | None:
    """直近のtick分析結果"""
    return self._last_analysis

@property
def last_tick_time(self) -> datetime | None:
    """直近のtick処理時刻"""
    return self._last_tick_time

@property
def demo_mode_enabled(self) -> bool:
    """デモモード状態"""
    return getattr(self._bot.config, "demo_mode", False)

@property
def signal_history(self) -> list[Signal]:
    """シグナル履歴"""
    return self._signal_history
```

### Change 3: `_tick()` で毎tick分析結果を保存

`_tick()` L207-210 を修正。`generate_signal()` の結果を **HOLDを含む全tick** で `_last_analysis` に保存:

```python
# 3. シグナル生成
current_time = pd.Timestamp.now(tz="UTC")
signal = self._bot.generate_signal(current_time)

# 全tickの分析結果を保存（HOLD含む）
self._last_analysis = signal
self._last_tick_time = datetime.now(timezone.utc)

if signal and signal.direction != SignalType.HOLD:
    self._last_signal = signal
    self._signal_history.append(
        self._consolidated_to_signal(signal)
    )
    # 履歴上限
    if len(self._signal_history) > 200:
        self._signal_history = self._signal_history[-200:]
    ...（既存のログ・エントリー判定はそのまま）
```

### Change 4: `_consolidated_to_signal()` ヘルパー追加

`signal_history`（`/signals/current`, `/signals/history` で使用）に保存するため、`ConsolidatedSignal` → `Signal` エンティティ変換メソッドを追加。

※ `Signal` は `signal_type: SignalType` を要求。`SignalResponse` は `confidence_level: ConfidenceLevel` を要求するが `Signal` エンティティにはそのフィールドが無い。`_signal_to_response()` が `signal.confidence_level` を参照するため、ダックタイピングでカバーするか、非HOLDシグナルを `Signal` として構築する際に工夫が必要。

最小限の実装:

```python
def _consolidated_to_signal(
    self, cs: ConsolidatedSignal,
) -> Signal:
    """ConsolidatedSignalをSignalエンティティに変換"""
    return Signal(
        signal_id=str(uuid.uuid4()),
        symbol=self._config.symbol,
        timeframe=cs.primary_tf,
        signal_type=cs.direction,
        confidence=cs.confidence,
        stop_loss=cs.sl_pips,
        take_profit=cs.tp_pips,
        reasoning=cs.rationale,
        created_at=datetime.now(timezone.utc),
        indicators_snapshot={},
        regime=cs.regime,
        mode=cs.mode,
        consensus_score=cs.consensus_score,
    )
```

`SignalResponse` の `confidence_level` 問題は `/signals/current` を使う際に発生するが、主要目標は `/signals/analysis` エンドポイント（`last_analysis` ベース）なので、今回は `signal_history` を最低限動作させる範囲に留める。`confidence_level` は `Signal` に属性を追加せず、`_signal_to_response()` 側で `getattr` フォールバック対応する。

## 変更しないもの

- `signals.py` ルーター（既にプロパティを正しく参照している）
- `dashboard.js`（`mode === 'live'` チェックのみで正しい）
- `handlers.py`（WebSocket層）
- `trade_bot.py`（シグナル生成ロジック）

## 検証

1. `pytest tests/unit/live/` で既存テストがパスすること
2. WebUI起動 → デモモード/自動トレードOFFでもLive Analyticsパネルが表示・更新されること
3. `GET /api/v1/signals/analysis` が `consensus_score`, `tf_scores` 等を含むレスポンスを返すこと
