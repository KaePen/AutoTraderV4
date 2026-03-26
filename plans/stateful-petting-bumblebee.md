# スピーチ・発言系イベント時の含み損ポジション緊急決済

## Context

現在のシステムには経済指標イベント前後のエントリー禁止（`has_high_impact_within_30min`）は実装済みだが、
**スピーチ・記者会見系イベント（FRB議長、日銀総裁等）で相場が急変した場合に含み損ポジションを守る仕組みがない**。

スピーチはカレンダーに掲載されるが、発言内容は事前不明なため方向性の予測が困難。
相場が逆方向に動き始めた時点でリアルタイムに検知して含み損を緊急カットすることが目的。

**目標レイテンシ：** 外部サービス不使用（無料RSS）、ニュースヒット後 1〜2分以内
（スピーチは15〜30分続くため、最初の動きには乗れなくても十分な保護効果がある）

---

## 設計概要

```
MT5カレンダー → スピーチイベント検知（is_speech_event()）
    ↓ スピーチ開始15分前〜終了後30分
RSSポーリング間隔: 300秒 → 15秒（FundamentalDataCollector → RSSCollector に通知）

    ↓ スピーチ中のニュースヒット
KeywordSentimentScorer → 即座にスコア算出（既存）
    ↓ |score| >= 0.5 かつスピーチ中
engine._speech_emergency_active = True

    ↓ _manage_positions() ループ
FundamentalContext に speech_emergency_exit=True をブレンド

    ↓ PositionManager.evaluate()
_check_speech_emergency_exit()
  含み損（current_r < 0） + ポジション方向とセンチメントが逆 → 全決済
```

---

## 変更ファイルと実装内容

### Step 1: `autotrader/adapters/fundamental/schemas.py`

**A) スピーチ識別キーワードと関数を追加**
```python
SPEECH_EVENT_KEYWORDS: tuple[str, ...] = (
    "speech", "speaks", "testimony", "remarks",
    "press conference", "statement", "appearance",
    "interview", "forum", "hearing", "presser",
    "powell", "fed chair", "boj governor",
    "ecb president", "boe governor",
)

def is_speech_event(event: EconomicEvent) -> bool:
    """event_name からスピーチ系イベントを識別"""
    name_lower = event.event_name.lower()
    return any(kw in name_lower for kw in SPEECH_EVENT_KEYWORDS)
```

**B) `FundamentalContext` に4フィールド追加**（後方互換、デフォルト値ニュートラル）
```python
speech_active: bool = False
speech_emergency_exit: bool = False
speech_sentiment: float = 0.0
speech_event_name: str = ""
```

---

### Step 2: `autotrader/adapters/fundamental/rss_collector.py`

`set_poll_interval(interval_seconds: int)` メソッドを追加。
`asyncio.Event` を使ってスリープを割り込み可能にし、即座にポーリング間隔を変更できる。

```python
def set_poll_interval(self, interval_seconds: int) -> None:
    """ポーリング間隔を動的変更（スリープ即座中断）"""
    if interval_seconds != self._poll_interval:
        self._poll_interval = interval_seconds
        if self._interval_changed is not None:
            self._interval_changed.set()

# _poll_loop() のスリープ部分を asyncio.wait_for + Event.wait() に変更
```

---

### Step 3: `autotrader/adapters/fundamental/collector.py`

**A) `set_rss_collector(rss_collector)` メソッドを追加**

**B) `_get_seconds_to_next_speech()` メソッドを追加**
- スピーチ開始前900秒〜終了後1800秒は `0.0`（スピーチ中）を返す

**C) `_collect_loop()` の間隔決定後にRSS速度変更を通知**
```python
speech_sec = self._get_seconds_to_next_speech()
if self._rss_collector is not None:
    self._rss_collector.set_poll_interval(
        15 if speech_sec == 0.0 else 300
    )
```

---

### Step 4: `autotrader/live/engine.py`

**A) `__init__` にスピーチ緊急状態変数を追加**
```python
self._speech_emergency_active: bool = False
self._speech_emergency_sentiment: float = 0.0
self._speech_emergency_event_name: str = ""
self._speech_emergency_since: datetime | None = None
```

**B) `_init_calendar_only()` / `_init_fundamental()` の初期化後にRSSコレクター参照を注入**
```python
if (self._fundamental_collector is not None
        and self._rss_collector is not None
        and hasattr(self._fundamental_collector, "set_rss_collector")):
    self._fundamental_collector.set_rss_collector(self._rss_collector)
```

**C) `_on_rss_news()` の KeywordSentimentScorer 呼び出し後にスピーチ緊急判定を追加**
- スピーチ中（`_get_seconds_to_next_speech() == 0`）のニュースで `|score| >= threshold`
- フラグと方向を `self._speech_emergency_*` に記録

**D) `_manage_positions()` の FundamentalContext 取得後に緊急フラグをブレンド**
```python
if _fund_ctx is not None and self._speech_emergency_active:
    # TTL（30分）チェック後に dataclasses.replace() でフィールドを注入
    _fund_ctx = replace(_fund_ctx, speech_emergency_exit=True, ...)
```

---

### Step 5: `autotrader/core/enums.py`

`ExitReason` に `SPEECH_EMERGENCY = "SPEECH_EMG"` を追加

---

### Step 6: `autotrader/decision/unified/risk/position_manager.py`

**A) `PositionManagerConfig` に設定フィールド追加**
```python
speech_emergency_exit_enabled: bool = False   # デフォルトOFF
speech_emergency_loss_only: bool = True        # 含み損のみ対象
```

**B) `_check_speech_emergency_exit()` メソッドを追加**
- `speech_emergency_exit=True` でなければスキップ
- `speech_emergency_loss_only=True` かつ `current_r >= 0` はスキップ
- ポジション方向とセンチメントが逆方向（`BUY + sentiment < -0.3` or `SELL + sentiment > 0.3`）のみ決済
- `ExitReason.SPEECH_EMERGENCY` で `ManagementAction.full_close()` を返す

**C) `evaluate()` の優先順位挿入**（SL/TP直後、利益反転ガードの前）
```python
# スピーチ緊急決済（含み損の最優先保護）
if self.config.speech_emergency_exit_enabled:
    action = self._check_speech_emergency_exit(...)
    if action: return action
```

---

## 設定の有効化方法（デフォルトOFF）

```yaml
# config/live_config.yaml の position_manager セクション
position_manager:
  speech_emergency_exit_enabled: true   # ← これをtrueにするだけ
```

---

## 検証方法

1. **MT5カレンダーにスピーチイベントがある時間帯**でサーバーログを確認
   - `[RSSCollector] ポーリング間隔変更: 15秒` が出ること
   - `[Speech] 緊急決済フラグON` が出ること

2. **ユニットテスト**
   - `is_speech_event()` に "Fed Chair Powell Speaks" 等を渡してTrue/Falseを確認
   - `_check_speech_emergency_exit()` に模擬 `FundamentalContext` を渡して決済アクションが返ること

3. **エンドツーエンド**
   - `speech_emergency_exit_enabled=True` の設定でエンジン起動
   - MT5カレンダーにスピーチイベントがある状態で、ダッシュボードのカレンダーUIにイベントが表示されること
   - RSSにスピーチ関連ヘッドラインが流れた際に `ExitReason.SPEECH_EMERGENCY` のトレードが記録されること

---

## 実装上のリスクと対策

| リスク | 対策 |
|--------|------|
| RSS遅延でスピーチ直後に間に合わない | スピーチ後30分間高速ポーリング継続 |
| 無関係なニュースで誤検知 | スピーチ中フラグとの AND 条件 + 閾値0.5 |
| 含み益ポジションの誤決済 | `speech_emergency_loss_only=True`（デフォルト）|
| 既存テストへの影響 | 全新規フィールドにデフォルト値、設定はデフォルトOFF |
