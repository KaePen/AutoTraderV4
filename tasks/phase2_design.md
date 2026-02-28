# Phase 2 設計: バックテストへのイベントLLMデータ統合

## 1. 概要

Phase 1 で生成されたイベント単位CSV（`llm_events_SYMBOL_YYYY.csv`）を
バックテストのトレードロジック（HardGuard, SoftGuard, PositionSizer,
PositionManager, UnifiedTradeBot）に統合する。

設計方針: **消費者から逆算する**。各消費者が「何を知りたいか」を定義し、
そこから`FundamentalContext`のスキーマとCSVの読み込み・合成ロジックを決定する。

---

## 2. 消費者要件マトリクス

### 2.1 消費者別の情報要件

| 消費者 | 知りたいこと | 判断内容 | 頻度 |
|--------|------------|---------|------|
| **BacktestRunner** | 重要指標前か？ | エントリー完全スキップ | 毎tick |
| **HardGuard** | 取引禁止条件に該当するか？ | エントリーブロック | エントリー判定時 |
| **SoftGuard** | ペナルティを課すべき状況か？ | 確度減点 | エントリー判定時 |
| **PositionSizer** | ロットを縮小すべきか？ | ロット調整係数 | エントリー判定時 |
| **PositionManager** | 保有ポジションの管理を変更すべきか？ | SL引き締め・早期決済 | ポジション管理時 |
| **UnifiedTradeBot** | シグナル方向とファンダメンタルは一致しているか？ | スコア加減算 | シグナル生成時 |

### 2.2 消費者別の必要フィールド

```
                            HG   SG   PS   PM   TB   Runner
event_caution_level          x    .    .    .    .    .
has_high_impact_within_Nmin  x    .    .    .    .    x
liquidity_factor             .    x    x    x    .    .
volatility_multiplier        .    x    x    .    .    .
direction_bias               .    .    .    .    x    .
surprise_score               .    .    .    .    x    .
active_event_count           .    x    .    .    .    .
convergence_progress         .    .    .    x    .    .
is_holiday                   x    x    .    .    .    .

HG = HardGuard, SG = SoftGuard, PS = PositionSizer
PM = PositionManager, TB = UnifiedTradeBot (consensus)
Runner = BacktestRunner (event guard skip)
```

### 2.3 消費者の判断ロジック（擬似コード）

**BacktestRunner** (現行維持 + 拡張):
```
if ctx.has_high_impact_within_30min:
    skip  # 既存動作
if ctx.event_caution_level >= 2:
    skip  # NFP等の超重要指標日 (Phase 2a)
```

**HardGuard.check_fundamental()** (新規追加):
```
if ctx.event_caution_level >= 2:
    return (False, "超重要指標日")
if ctx.is_holiday and ctx.liquidity_factor < 0.3:
    return (False, "極度の流動性低下")
```

**SoftGuard.check_fundamental()** (新規追加):
```
penalty = 0.0
if ctx.volatility_multiplier > 1.5:
    penalty += 0.15  # 高ボラ注意
elif ctx.volatility_multiplier < 0.5:
    penalty += 0.10  # 薄商い注意
if ctx.active_event_count >= 3:
    penalty += 0.10  # イベント密集
if ctx.is_holiday:
    penalty += 0.05 * (1.0 - ctx.liquidity_factor)
return min(penalty, 0.4)
```

**PositionSizer** (既存の`_calculate_risk_adjust`に追加):
```
fundamental_adjust = 1.0
if ctx.liquidity_factor < 0.5:
    fundamental_adjust *= (0.5 + ctx.liquidity_factor)  # 0.5~1.0
if ctx.volatility_multiplier > 1.5:
    fundamental_adjust *= 0.8
risk_adjust *= fundamental_adjust
```

**PositionManager** (SL引き締め + 早期決済):
```
if ctx.convergence_progress < 0.3:
    # イベント直後でまだ影響収束前
    if position.current_r > 0.5:
        # 含み益があれば利確検討
        consider_partial_close()
    trailing_sl_multiplier *= 0.8  # タイト化
```

**UnifiedTradeBot** (方向性一致ボーナス/ペナルティ):
```
alignment = signal_direction_sign * ctx.direction_bias
if alignment > 0.3:
    consensus_score += 0.5  # ファンダ一致ボーナス
elif alignment < -0.3:
    consensus_score -= 0.5  # ファンダ逆行ペナルティ
```

---

## 3. FundamentalContext 新スキーマ

### 3.1 設計原則

1. **消費者が直接使える値**を提供する（生のCSVカラムではなく計算済み値）
2. **セマンティックの一義性**: 通常イベントと休日で意味が変わるフィールドは分離
3. **後方互換**: 既存の`has_high_impact_within_30min`は維持
4. **ニュートラル安全**: デフォルト値は全消費者にとって「影響なし」

### 3.2 新スキーマ定義

```python
@dataclass(frozen=True)
class FundamentalContext:
    """ファンダメンタルコンテキスト（Phase 2）

    イベントLLMデータから計算された、トレードロジック向けの
    消費者寄りインターフェース。

    全フィールドはデフォルト値がニュートラル（影響なし）。
    """

    # --- ガード系（HardGuard / Runner 向け） ---
    has_high_impact_within_30min: bool = False
    # 現在の最大注意度（複数イベントの max）
    # 0=通常, 1=注意, 2=取引回避推奨
    event_caution_level: int = 0
    # 休日フラグ（休日由来のイベントが影響中）
    is_holiday: bool = False

    # --- 流動性・ボラティリティ系（SoftGuard / PositionSizer 向け） ---
    # 流動性係数: 1.0=通常, <1.0=低流動性, >1.0はない
    # 休日/低ボリューム時に減少
    liquidity_factor: float = 1.0
    # ボラティリティ倍率: 1.0=通常, >1.0=高ボラ, <1.0=低ボラ
    # 通常イベント: サプライズによるボラ増加（>1.0）
    # 休日: 流動性低下によるボラ減少（<1.0）だがフラッシュリスクあり
    volatility_multiplier: float = 1.0
    # 影響中のイベント数（convergence_hours未経過のもの）
    active_event_count: int = 0

    # --- 方向性系（UnifiedTradeBot 向け） ---
    # 合成方向バイアス: -1.0~+1.0, +はペア上昇方向
    # 影響中の全イベントの direction_bias を
    # 時間減衰で重み付け合算
    direction_bias: float = 0.0
    # 合成サプライズスコア: -1.0~+1.0
    surprise_score: float = 0.0

    # --- ポジション管理系（PositionManager 向け） ---
    # 影響収束進捗: 0.0=直後（最大影響）, 1.0=完全収束
    # 複数イベントの場合は最も未収束なもの
    convergence_progress: float = 1.0

    # --- 直近イベント情報（UI / ログ向け） ---
    upcoming_events: list[dict] = field(default_factory=list)

    # --- 後方互換プロパティ ---
    # 旧スキーマのフィールドをプロパティで提供
    # Phase 3（ニュースLLM統合）まではデフォルト値を返す

    @property
    def macro_bias_score(self) -> float:
        """後方互換: ニュースマクロバイアス（Phase 3で実装）"""
        return 0.0

    @property
    def macro_bias_summary(self) -> str:
        """後方互換"""
        return "Phase 3で実装予定"

    @property
    def post_event_bias_score(self) -> float:
        """後方互換: イベント方向バイアスに対応"""
        return self.direction_bias

    @property
    def post_event_summary(self) -> str:
        """後方互換"""
        return ""

    @property
    def sentiment_score(self) -> float:
        """後方互換: ニュースセンチメント（Phase 3で実装）"""
        return 0.0

    @classmethod
    def neutral(cls) -> FundamentalContext:
        """ニュートラルコンテキスト（データなし時）"""
        return cls()

    def to_prompt_section(self) -> str:
        """プロンプト用テキスト（LLM Veto用、ライブ互換）"""
        lines = [
            "## ファンダメンタルコンテキスト",
            f"- 方向バイアス: {self.direction_bias:+.2f}",
            f"- 流動性: {self.liquidity_factor:.2f}",
            f"- ボラ倍率: {self.volatility_multiplier:.2f}",
            f"- 注意度: {self.event_caution_level}",
        ]
        if self.is_holiday:
            lines.append("- 休日影響あり")
        if self.upcoming_events:
            lines.append("- 直近イベント:")
            for ev in self.upcoming_events[:3]:
                lines.append(
                    f"  - {ev.get('name', '?')} "
                    f"({ev.get('minutes_until', 0):.0f}分後)"
                )
        if self.has_high_impact_within_30min:
            lines.append("WARNING: 30分以内に高インパクト指標あり")
        return "\n".join(lines)
```

### 3.3 旧スキーマとの対応表

| 旧フィールド | 新フィールド | 移行方法 |
|------------|-----------|---------|
| `macro_bias_score` | `@property` -> `0.0` | Phase 3でニュースLLM統合時に実装 |
| `macro_bias_summary` | `@property` -> `""` | 同上 |
| `post_event_bias_score` | `direction_bias` | イベントLLMから直接マッピング |
| `post_event_summary` | `@property` -> `""` | summaryは消費者が直接使わないため省略 |
| `sentiment_score` | `@property` -> `0.0` | Phase 3で実装 |
| `upcoming_events` | `upcoming_events` | 維持 |
| `has_high_impact_within_30min` | `has_high_impact_within_30min` | 維持 |

---

## 4. イベント影響モデル

### 4.1 時間減衰モデル

各イベントのLLMデータには`convergence_hours`（インパクト収束推定時間）が含まれる。
これを使って、ある時刻 t におけるイベント e の残存影響度を計算する。

```
elapsed_hours = (t - event_time).total_seconds() / 3600
decay_ratio = elapsed_hours / convergence_hours

if decay_ratio >= 1.0:
    influence = 0.0      # 完全収束
elif decay_ratio <= 0.0:
    influence = 1.0      # イベント前（未発表）
else:
    # 指数減衰: 収束時間の半分で影響が約60%に
    influence = exp(-2.0 * decay_ratio)
```

**指数減衰を選ぶ理由:**
- 線形減衰（`1 - ratio`）はイベント直後の急落を表現できない
- NFPの場合: 直後1時間で急速に織り込み → 残りは緩やかに収束
- `exp(-2.0 * ratio)` で convergence_hours の 35% で影響半減

**減衰曲線の視覚イメージ:**
```
influence
1.0 |*
    | *
    |  *
0.5 |   *
    |     *
    |        *
0.0 |__________*___
    0         conv_h  (時間)
```

### 4.2 複数イベント合成モデル

同時刻に複数イベントが影響中の場合、各フィールドを以下のように合成する。

**方向性フィールド（`direction_bias`, `surprise_score`）:**
```
weighted_bias = 0.0
total_weight = 0.0
for event in active_events:
    w = influence(event) * impact_weight(event)
    weighted_bias += event.direction_bias * w
    total_weight += w
if total_weight > 0:
    composite_bias = clip(weighted_bias / total_weight, -1.0, 1.0)
else:
    composite_bias = 0.0
```

`impact_weight`はイベントのインパクトレベルに基づく重み:
- HIGH: 3.0
- MEDIUM: 1.0
- LOW: 0.3

**スカラーフィールド（`volatility_multiplier`）:**
```
composite_vol = 1.0
for event in active_events:
    # 各イベントのボラ倍率を influence で重み付け
    event_vol = 1.0 + (event.expected_volatility - 1.0) * influence(event)
    composite_vol = max(composite_vol, event_vol)  # maxで合成
```

ボラティリティは加算ではなくmax取りとする。理由:
NFP（ボラ1.8倍）とISM（ボラ1.3倍）が同日にあっても、ボラが3.1倍になることはない。
最も影響の大きいイベントのボラが支配的。

**注意度（`event_caution_level`）:**
```
composite_caution = max(
    event.trade_caution_level
    for event in active_events
    if influence(event) > 0.1  # 影響が残っているもののみ
)
```

**収束進捗（`convergence_progress`）:**
```
# 最も未収束なイベントの進捗を採用
min_progress = 1.0
for event in active_events:
    progress = 1.0 - influence(event)
    min_progress = min(min_progress, progress)
convergence_progress = min_progress
```

### 4.3 休日イベントの合成

休日イベントは通常イベントと異なるセマンティクスを持つ。

**CSV上の区別:**
Phase 1のCSVは`event_name`に"Holiday"を含むかどうかで判別可能（`_HOLIDAY_RE`）。
追加カラムは不要。

**合成時の特殊処理:**
```
is_holiday = any(
    "holiday" in e.event_name.lower()
    and influence(e) > 0.1
    for e in active_events
)

if is_holiday:
    # 休日の expected_volatility は「薄商いによる低ボラ」を意味する
    # → liquidity_factor として使用（0.2 = 非常に低い流動性）
    holiday_events = [e for e in active_events if "holiday" in e.event_name.lower()]
    liquidity_factor = min(
        e.expected_volatility * influence(e) + (1.0 - influence(e))
        for e in holiday_events
    )
    # volatility_multiplier はフラッシュクラッシュリスクを反映
    # 通常の低ボラではなく、スプレッド拡大リスクとして扱う
    # 薄商いだが突発的な大変動がありうる
    # → ボラ倍率は1.0を維持しつつ流動性で制御
```

**セマンティック分離の要点:**
| カラム | 通常イベント | 休日イベント | 合成時の処理 |
|--------|-----------|-----------|------------|
| `expected_volatility` | ボラ増加倍率(>1.0) | 流動性低下度(<1.0) | 休日→`liquidity_factor`に変換 |
| `convergence_hours` | 価格インパクト収束時間 | 流動性正常化時間 | 同じ減衰モデルを適用（意味は異なるが数学的に同一） |
| `trade_caution_level` | サプライズの大きさ | 流動性低下度 | maxで合成（区別不要） |
| `direction_bias` | 常に0.0（休日に方向性なし） | - | 自動的に中立 |

---

## 5. CSVカラム設計

### 5.1 現行CSVカラム（Phase 1 出力、変更なし）

Phase 1 で生成済みの `llm_events_SYMBOL_YYYY.csv` のカラムはそのまま使用する。
Phase 2 は消費者側の読み込み・合成ロジックで対応し、CSVフォーマットは変更しない。

```
event_time       : ISO8601, イベント発表時刻（UTC）
currency         : str,     対象通貨（USD, JPY等）
event_name       : str,     イベント名称
impact           : str,     "high" / "medium" / "low"
actual           : float?,  実績値
forecast         : float?,  予測値
previous         : float?,  前回値
surprise_score   : float,   -1.0~+1.0, サプライズスコア
direction_bias   : float,   -1.0~+1.0, 短期価格方向（+はペア上昇）
convergence_hours: float,   0.0~72.0, 影響収束推定時間
expected_volatility: float, 0.0~2.0, ボラティリティ倍率
trade_caution_level: int,   0/1/2, 取引注意度
summary          : str,     分析要約（日本語、200文字以内）
```

### 5.2 各カラムの消費者マッピング

| CSVカラム | 合成先フィールド | 合成方法 |
|-----------|--------------|---------|
| `direction_bias` | `FundamentalContext.direction_bias` | 重み付き平均（影響度 x インパクト重み） |
| `surprise_score` | `FundamentalContext.surprise_score` | 重み付き平均（同上） |
| `expected_volatility` | `FundamentalContext.volatility_multiplier` | max（通常イベントのみ） |
| `expected_volatility` (休日) | `FundamentalContext.liquidity_factor` | min（休日イベントのみ） |
| `convergence_hours` | `FundamentalContext.convergence_progress` | 減衰モデルで計算、min |
| `trade_caution_level` | `FundamentalContext.event_caution_level` | max |
| `event_name` | `FundamentalContext.is_holiday` | "holiday" in name (case insensitive) |
| `impact` + 未来イベント | `FundamentalContext.has_high_impact_within_30min` | 既存ロジック維持 |
| (count) | `FundamentalContext.active_event_count` | influence > 0.1 のイベント数 |

---

## 6. BacktestFundamentalProvider.get_context() 擬似コード

### 6.1 データ構造

```python
class BacktestFundamentalProvider:
    def __init__(self, event_guard_minutes: int = 30):
        # 既存: 生の経済イベント（events_YYYY.csv）
        self._events: list[EconomicEvent] = []
        self._events_sorted_ts: list[float] = []
        self._normalizer = EconomicEventNormalizer()

        # 既存: 月次LLMコンテキスト（後方互換）
        self._llm_ts: dict[str, list[float]] = {}
        self._llm_data: dict[str, list[dict]] = {}

        # 新規: イベントLLMデータ（1行=1イベント）
        # symbol -> list[EventLLMRecord]
        self._event_llm_records: dict[str, list[EventLLMRecord]] = {}
        # bisect用: symbol -> list[float] (event_time timestamp)
        self._event_llm_ts: dict[str, list[float]] = {}

    def load_event_llm_csv(
        self, csv_path: str | Path, symbol: str
    ) -> int:
        """llm_events_SYMBOL_YYYY.csv を読み込み
        1行 = 1イベントのLLM分析結果
        """
        ...
```

### 6.2 EventLLMRecord（内部データクラス）

```python
@dataclass(frozen=True)
class EventLLMRecord:
    """イベントLLM分析結果（CSV1行に対応）"""
    event_time: datetime
    currency: str
    event_name: str
    impact: str  # "high" / "medium" / "low"
    surprise_score: float
    direction_bias: float
    convergence_hours: float
    expected_volatility: float
    trade_caution_level: int
    is_holiday: bool  # event_nameから判定
```

### 6.3 get_context() の合成アルゴリズム

```python
def get_context(
    self, current_time: datetime, symbol: str
) -> FundamentalContext:

    # ======================================
    # Step 1: 既存ロジック（upcoming events / high impact check）
    # ======================================
    symbol_events = self._normalizer.filter_by_symbol(
        self._events, symbol
    )
    upcoming = self._normalizer.get_upcoming_events(
        symbol_events, current_time, window_minutes=60
    )
    upcoming_dicts = [
        {"name": ev.event_name,
         "minutes_until": ev.minutes_until(current_time),
         "impact": ev.impact.value}
        for ev in upcoming
    ]
    high_impact_soon = any(
        ev.impact == ImpactLevel.HIGH
        and 0 <= ev.minutes_until(current_time) <= self._guard_minutes
        for ev in upcoming
    )

    # ======================================
    # Step 2: イベントLLMデータがなければフォールバック
    # ======================================
    records = self._event_llm_records.get(symbol, [])
    if not records:
        # 旧月次LLMデータまたはイベントベース計算にフォールバック
        return self._fallback_context(
            current_time, symbol,
            upcoming_dicts, high_impact_soon,
        )

    # ======================================
    # Step 3: 影響中のイベントを収集（bisect で検索範囲を絞る）
    # ======================================
    # 過去72時間（最大 convergence_hours）のイベントを候補に
    ts_list = self._event_llm_ts[symbol]
    cutoff_ts = (current_time - timedelta(hours=72)).timestamp()
    current_ts = current_time.timestamp()

    lo = bisect.bisect_left(ts_list, cutoff_ts)
    hi = bisect.bisect_right(ts_list, current_ts)
    candidates = records[lo:hi]

    # 各候補の影響度を計算
    active_events = []
    for rec in candidates:
        elapsed_h = (
            current_time - rec.event_time
        ).total_seconds() / 3600

        if rec.convergence_hours <= 0:
            influence = 0.0
        elif elapsed_h < 0:
            influence = 0.0  # 未来のイベント（upcoming で処理）
        elif elapsed_h >= rec.convergence_hours:
            influence = 0.0  # 完全収束
        else:
            ratio = elapsed_h / rec.convergence_hours
            influence = math.exp(-2.0 * ratio)

        if influence > 0.05:  # 5%未満は無視
            active_events.append((rec, influence))

    # ======================================
    # Step 4: フィールド合成
    # ======================================
    if not active_events:
        return FundamentalContext(
            upcoming_events=upcoming_dicts,
            has_high_impact_within_30min=high_impact_soon,
        )

    IMPACT_WEIGHT = {"high": 3.0, "medium": 1.0, "low": 0.3}

    # --- 方向性合成（重み付き平均） ---
    total_weight = 0.0
    weighted_bias = 0.0
    weighted_surprise = 0.0
    for rec, infl in active_events:
        w = infl * IMPACT_WEIGHT.get(rec.impact, 0.3)
        weighted_bias += rec.direction_bias * w
        weighted_surprise += rec.surprise_score * w
        total_weight += w

    direction_bias = 0.0
    surprise_score = 0.0
    if total_weight > 0:
        direction_bias = max(-1.0, min(1.0,
            weighted_bias / total_weight
        ))
        surprise_score = max(-1.0, min(1.0,
            weighted_surprise / total_weight
        ))

    # --- ボラティリティ合成（通常イベントの max） ---
    normal_vols = [
        1.0 + (rec.expected_volatility - 1.0) * infl
        for rec, infl in active_events
        if not rec.is_holiday
    ]
    volatility_multiplier = max(normal_vols) if normal_vols else 1.0

    # --- 流動性合成（休日イベントの min） ---
    liquidity_factor = 1.0
    is_holiday = False
    for rec, infl in active_events:
        if rec.is_holiday:
            is_holiday = True
            # expected_volatility(休日) = 流動性係数
            # 減衰を適用: 流動性は時間とともに正常化
            liq = rec.expected_volatility * infl + (1.0 - infl)
            liquidity_factor = min(liquidity_factor, liq)

    # --- 注意度合成（max） ---
    event_caution_level = max(
        rec.trade_caution_level
        for rec, infl in active_events
        if infl > 0.1
    )

    # --- 収束進捗（最も未収束なもの） ---
    convergence_progress = min(
        1.0 - infl for _, infl in active_events
    )

    # --- アクティブイベント数 ---
    active_event_count = len(active_events)

    return FundamentalContext(
        has_high_impact_within_30min=high_impact_soon,
        event_caution_level=event_caution_level,
        is_holiday=is_holiday,
        liquidity_factor=liquidity_factor,
        volatility_multiplier=volatility_multiplier,
        active_event_count=active_event_count,
        direction_bias=direction_bias,
        surprise_score=surprise_score,
        convergence_progress=convergence_progress,
        upcoming_events=upcoming_dicts,
    )
```

### 6.4 フォールバック階層

```
1. イベントLLM CSV あり → 上記の合成アルゴリズム
2. イベントLLM CSV なし + 月次LLM CSV あり → 旧ロジック（既存動作維持）
3. どちらもなし + events_YYYY.csv あり → _estimate_bias_from_events（既存）
4. 何もなし → FundamentalContext.neutral()
```

---

## 7. 段階的実装計画

### Phase 2a: 最小限の統合（ハードガード + ロット調整）

**目標: イベントLLMデータを読み込み、最低限のトレード判断に使用する。**

既存の動作に最小限の変更で、最も効果が大きい2箇所に統合する。

#### Step 2a-1: FundamentalContext スキーマ変更
- **ファイル**: `autotrader/adapters/fundamental/schemas.py`
- **内容**: 新フィールド追加 + 後方互換プロパティ + `neutral()` 更新
- **リスク**: 低（frozen dataclass、フィールド追加は既存コードに影響しない）
- **テスト**: 既存テストの通過確認、neutral() の新フィールド確認

#### Step 2a-2: EventLLMRecord + CSV読み込み
- **ファイル**: `autotrader/adapters/fundamental/backtest_provider.py`
- **内容**:
  - `EventLLMRecord` データクラス定義
  - `load_event_llm_csv()` メソッド追加
  - 内部ストレージ（`_event_llm_records`, `_event_llm_ts`）追加
- **リスク**: 低（新メソッド追加のみ、既存メソッドに変更なし）
- **テスト**: CSVパース、resume対応、ソート順の確認

#### Step 2a-3: get_context() の拡張
- **ファイル**: `autotrader/adapters/fundamental/backtest_provider.py`
- **内容**:
  - 時間減衰モデルの実装
  - 複数イベント合成ロジック
  - 休日のセマンティック分離
  - フォールバック階層
- **リスク**: 中（既存の `get_context()` を大幅変更）
- **緩和**: フォールバックで旧動作を完全維持、A/Bテスト可能
- **テスト**:
  - 単一イベント（HIGH/MEDIUM/LOW）の合成
  - 複数イベント同時（同方向/逆方向）の合成
  - 休日イベントの流動性変換
  - convergence_hours=0 のエッジケース
  - 減衰が完全収束した後のニュートラル復帰
  - フォールバック動作の確認

#### Step 2a-4: BacktestRunner の統合
- **ファイル**: `autotrader/backtest/runner.py`
- **内容**:
  - `load_event_llm_csv()` の呼び出し追加（`run()`内）
  - `event_caution_level >= 2` による追加スキップ
- **リスク**: 低（既存の `has_high_impact_within_30min` スキップの隣に追加）
- **テスト**: caution_level=2のイベント日にトレードがスキップされること

#### Step 2a-5: HardGuard の拡張
- **ファイル**: `autotrader/constraint/hard_guard.py`
- **内容**:
  - `check_fundamental()` メソッド追加
  - `check()` に `FundamentalContext` パラメータを追加（Optional）
  - 既存のハードコードされた `check_high_impact_news()` を
    `FundamentalContext` ベースに移行
- **リスク**: 中（既存の `check()` シグネチャ変更）
- **緩和**: `FundamentalContext` パラメータは Optional（None=チェックスキップ）
- **テスト**: caution_level=2 でブロック、is_holiday+低流動性でブロック

#### Step 2a-6: PositionSizer への流動性反映
- **ファイル**: `autotrader/decision/unified/position_sizer.py`
- **内容**:
  - `SizingContext` に `liquidity_factor` と `volatility_multiplier` 追加
  - `_calculate_risk_adjust()` にファンダメンタル調整を追加
- **リスク**: 中（SizingContext はインターフェース定義）
- **緩和**: デフォルト値 `liquidity_factor=1.0`, `volatility_multiplier=1.0`
- **テスト**: liquidity_factor=0.3 でロット減少、volatility_multiplier=1.8 でロット減少

### Phase 2b: 拡張統合（方向性 + 管理）

**目標: 方向バイアスをコンセンサスに反映し、ポジション管理にも統合する。**

Phase 2a の効果測定後に実施。

#### Step 2b-1: SoftGuard の拡張
- **ファイル**: `autotrader/constraint/soft_guard.py`
- **内容**:
  - `check_fundamental()` メソッド追加
  - `SoftGuardReason.FUNDAMENTAL` enum追加
  - volatility_multiplier / active_event_count / is_holiday ベースのペナルティ
- **依存**: Phase 2a 完了
- **テスト**: ボラ倍率1.6で0.15ペナルティ、イベント密集で0.10ペナルティ

#### Step 2b-2: UnifiedTradeBot のファンダメンタル加味
- **ファイル**: `autotrader/decision/unified/trade_bot.py`
- **内容**:
  - `generate_signal()` に `FundamentalContext` パラメータ追加（Optional）
  - コンセンサススコアへの方向性一致ボーナス/ペナルティ（+/-0.5）
- **依存**: Phase 2a 完了
- **設計判断**:
  - ファンダメンタルはコンセンサススコアに直接加算（テクニカルスコアの一部として扱う）
  - 加算量は設定可能（`UnifiedBotConfig.fundamental_alignment_bonus`）
  - デフォルト 0.5（コンセンサス閾値8.0に対して約6%の影響）
- **テスト**: BUYシグナル + direction_bias>0.3 でスコア+0.5

#### Step 2b-3: BacktestRunner からのパイプライン
- **ファイル**: `autotrader/backtest/runner.py`
- **内容**:
  - tick ループ内で `get_context()` の結果を各消費者に渡す
  - `bot.generate_signal(current_time, candle, fundamental_ctx=_fctx)`
  - SoftGuard, PositionSizer へのコンテキスト注入
- **依存**: Step 2b-1, 2b-2 完了
- **テスト**: エンドツーエンドバックテストの実行、結果比較

#### Step 2b-4: PositionManager への統合
- **ファイル**: `autotrader/decision/unified/position_manager.py`
- **内容**:
  - `manage()` に `FundamentalContext` パラメータ追加（Optional）
  - convergence_progress < 0.3 時のSL引き締め（trailing_atr_multiplier * 0.8）
  - 含み益ポジション + 収束前 → 部分利確検討
- **依存**: Phase 2a 完了
- **リスク**: 中（PositionManager は複雑な状態機械）
- **緩和**: ファンダメンタル影響はデフォルト無効（設定で有効化）
- **テスト**: convergence_progress=0.2 + 含み益0.5R でSL引き締め動作

---

## 8. テスト戦略

### 8.1 ユニットテスト

| テスト対象 | テスト内容 | ファイル |
|-----------|----------|---------|
| FundamentalContext | 新フィールドのデフォルト値、neutral()、後方互換プロパティ | `tests/unit/test_fundamental_schemas.py` |
| EventLLMRecord | CSVパース、休日判定 | `tests/unit/test_event_llm_record.py` |
| 時間減衰モデル | 減衰曲線、境界値（0時間、convergence_hours、負の時間） | `tests/unit/test_decay_model.py` |
| 複数イベント合成 | 同方向/逆方向、通常+休日混在、空リスト | `tests/unit/test_event_synthesis.py` |
| load_event_llm_csv | ファイル読み込み、ソート、resume | `tests/unit/test_backtest_provider.py` |
| get_context() | フォールバック階層、各フィールドの計算 | `tests/unit/test_backtest_provider.py` |
| HardGuard | fundamental チェック、既存チェックとの併存 | `tests/unit/test_hard_guard.py` |
| SoftGuard | fundamental ペナルティ計算 | `tests/unit/test_soft_guard.py` |
| PositionSizer | liquidity/volatility による調整 | `tests/unit/test_position_sizer.py` |

### 8.2 統合テスト

| テスト内容 | 方法 |
|-----------|------|
| Phase 2a 有無の結果比較 | 同一パラメータで fundamental_provider あり/なし のバックテスト結果を比較 |
| 回帰テスト | Phase 2a でイベントLLM CSV なしの場合、旧動作と完全一致 |
| 休日の効果確認 | USD休日（Thanksgiving等）での取引数・損益を確認 |
| NFP日の効果確認 | NFP日の caution_level=2 によるスキップ動作 |

### 8.3 効果測定メトリクス

Phase 2a/2b の効果を測定するための指標:

| メトリクス | 期待する変化 | 測定方法 |
|-----------|------------|---------|
| 休日の取引数 | 大幅減少 | 休日フラグ日の取引カウント |
| NFP日近辺の損益 | 損失減少 | caution_level=2 日の前後24時間の損益 |
| 全体の勝率 | 微増（0.5-1%） | バックテスト全期間 |
| シャープレシオ | 微増 | ボラ調整後リターン |
| 最大ドローダウン | 微減 | ロット縮小効果 |
| ファンダ一致トレードの勝率 | 通常より高い | direction_bias とシグナル方向の一致分析 |

---

## 9. リスクと緩和策

### 9.1 技術リスク

| リスク | 影響度 | 緩和策 |
|-------|--------|-------|
| get_context() の計算コスト増（毎tick） | 中 | bisectによるO(log n)検索、候補数は通常1-3件で軽量 |
| FundamentalContext のスキーマ変更による回帰 | 高 | 全フィールドにデフォルト値、後方互換プロパティ、フォールバック階層 |
| 減衰モデルのパラメータ不適切 | 中 | A/Bテスト可能な設計、減衰係数を設定パラメータ化 |
| PositionSizer の意図しないロット変動 | 高 | liquidity_factor/volatility_multiplier のデフォルト=1.0（影響なし）、段階的導入 |
| イベントLLM CSV の品質問題（LLM出力のばらつき） | 中 | clip済み値のみ使用、極端な値は減衰で吸収 |

### 9.2 設計リスク

| リスク | 影響度 | 緩和策 |
|-------|--------|-------|
| 過剰フィルタリング（トレード機会の大幅減少） | 高 | Phase 2a は HardGuard のみ（既存スキップの延長）、Phase 2b はボーナス/ペナルティ方式 |
| ファンダメンタルと テクニカルの矛盾による混乱 | 中 | ファンダ影響は小さく設定（コンセンサスの6%程度）、設定で無効化可能 |
| リアルトレードとの乖離拡大 | 中 | Phase 4（リアル側改修）まで、新フィールドのライブ側はニュートラル値 |

---

## 10. 設定パラメータ一覧

Phase 2 で追加する設定パラメータ。全て `UnifiedBotConfig` に集約。

```python
# Phase 2a: ハードガード
fundamental_caution_block_level: int = 2
# caution_level >= この値でエントリーブロック

fundamental_holiday_liquidity_block: float = 0.3
# 休日の流動性がこの値未満でエントリーブロック

# Phase 2a: PositionSizer
fundamental_liquidity_adjust_enabled: bool = True
# 流動性によるロット調整を有効化

fundamental_volatility_adjust_enabled: bool = True
# ボラティリティによるロット調整を有効化

# Phase 2b: SoftGuard
fundamental_softguard_enabled: bool = False
# ファンダメンタルソフトガードを有効化（Phase 2b で True に）

# Phase 2b: コンセンサス
fundamental_alignment_bonus: float = 0.5
# ファンダ方向一致時のコンセンサスボーナス

fundamental_alignment_penalty: float = -0.5
# ファンダ方向逆行時のコンセンサスペナルティ

fundamental_alignment_threshold: float = 0.3
# 方向一致/逆行の閾値（|alignment| > threshold で発動）

# Phase 2b: PositionManager
fundamental_convergence_sl_tighten: bool = False
# 収束前のSL引き締めを有効化

# 共通: 減衰モデル
fundamental_decay_coefficient: float = 2.0
# 指数減衰係数（大きいほど急速に減衰）
```

---

## 11. ファイル変更一覧

### Phase 2a

| ファイルパス | 変更種別 | 内容 |
|------------|---------|------|
| `autotrader/adapters/fundamental/schemas.py` | 変更 | FundamentalContext 新スキーマ + 後方互換 |
| `autotrader/adapters/fundamental/backtest_provider.py` | 変更 | EventLLMRecord, load_event_llm_csv(), get_context() 合成ロジック |
| `autotrader/constraint/hard_guard.py` | 変更 | check_fundamental() 追加 |
| `autotrader/decision/unified/position_sizer.py` | 変更 | SizingContext に liquidity/volatility 追加 |
| `autotrader/core/interfaces/position_sizing.py` | 変更 | SizingContext にフィールド追加 |
| `autotrader/backtest/runner.py` | 変更 | load_event_llm_csv() 呼び出し、caution_level スキップ |
| `autotrader/decision/unified/config.py` | 変更 | UnifiedBotConfig にファンダメンタル設定追加 |

### Phase 2b

| ファイルパス | 変更種別 | 内容 |
|------------|---------|------|
| `autotrader/constraint/soft_guard.py` | 変更 | check_fundamental() 追加 |
| `autotrader/decision/unified/trade_bot.py` | 変更 | generate_signal() にファンダメンタル加味 |
| `autotrader/decision/unified/position_manager.py` | 変更 | manage() にファンダメンタル加味 |
| `autotrader/backtest/runner.py` | 変更 | 各消費者へのコンテキスト注入パイプライン |

---

## 12. 成功基準

### Phase 2a

- [ ] イベントLLM CSV がない場合、旧動作と完全一致（回帰なし）
- [ ] イベントLLM CSV がある場合、`get_context()` が新フィールドを正しく返す
- [ ] caution_level=2 のイベント日にエントリーがブロックされる
- [ ] 休日（USD Holiday）で liquidity_factor < 1.0 が正しく計算される
- [ ] PositionSizer が liquidity_factor < 0.5 でロット縮小する
- [ ] 全既存テストが通過する
- [ ] バックテスト結果（fundamental なし）が変化しない

### Phase 2b

- [ ] 方向性一致トレードの勝率が非一致より高い（統計的有意性は不問）
- [ ] SoftGuard のファンダメンタルペナルティが正しく計算される
- [ ] PositionManager の収束前 SL 引き締めが動作する
- [ ] 全体の勝率またはシャープレシオが微改善（改悪でない）
