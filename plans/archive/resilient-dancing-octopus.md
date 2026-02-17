# AutoTraderV4 マルチタイムフレーム高性能化計画

## 目標
- 現在の勝率 **51.3%** → 目標 **60%**
- M1/M5短期トレード対応
- 3層MTF分析による高精度エントリー

---

## 現状分析

### 現在のパフォーマンス（2020-2024）
- 勝率: 51.3% (1,276トレード)
- PF: 1.45
- 総利益: ¥559,121
- 最大DD: 3.02%
- 5/5年すべて黒字

### 16年間検証（2010-2025）
- 勝率: 48.9%
- PF: 1.30
- 14/16年黒字（安定性確認済み）

### 改善余地

| 問題点 | 現状 | 改善案 |
|--------|------|--------|
| 単一時間足 | H1のみ | M1/M5/H1マルチ対応 |
| ADX閾値固定 | 20.0 | 時間足別最適化（M1:10, H1:20） |
| クールダウン固定 | 4足 | 時間足別（M1:30, H1:4） |
| MTF 2層のみ | H1+H4 | 3層（M5+H1+H4など） |
| 利確固定 | TP到達のみ | 部分利確+トレーリング |
| ノイズ対策なし | - | 短期足用フィルタ必要 |

---

## 5フェーズ実装計画

### Phase 1: TimeframePreset（時間足別パラメータ）

**新規ファイル**: `src/autotrader/config/timeframe_preset.py`

```python
@dataclass(frozen=True)
class TimeframePreset:
    """時間足別の最適化パラメータ"""
    timeframe: Timeframe
    min_signals: int
    signal_margin: int
    adx_threshold: float
    rsi_oversold: float
    rsi_overbought: float
    sl_atr_mult: float
    tp_atr_mult: float
    cooldown_bars: int
    min_atr_pips: float  # 最小ATR（ノイズ除去）
    max_spread_atr_ratio: float  # スプレッド/ATR上限

    @classmethod
    def for_m1(cls) -> TimeframePreset:
        # M1: adx=10, cooldown=30, min_atr=3pips

    @classmethod
    def for_m5(cls) -> TimeframePreset:
        # M5: adx=15, cooldown=12, min_atr=5pips

    @classmethod
    def for_h1(cls) -> TimeframePreset:
        # H1: 現行最適化済みパラメータ
```

---

### Phase 2: ShortTermSignalGenerator（短期足用シグナル）

**新規ファイル**: `src/autotrader/decision/short_term_generator.py`

短期足特有のノイズ対策:
1. ATRが最小閾値以上か確認
2. スプレッド/ATR比率チェック
3. ボラティリティ急増時は見送り
4. 上位足トレンド方向のみエントリー

---

### Phase 3: 3層MTFアライメント

**修正ファイル**: `src/autotrader/decision/signal_generator.py`

```python
class MTFAlignmentChecker:
    """3層マルチタイムフレームアライメント

    構成:
    - M1トレード: M1 + M5 + H1
    - M5トレード: M5 + H1 + H4
    - H1トレード: H1 + H4 + D1
    """
```

全層一致で追加ボーナス付与。

---

### Phase 4: 部分利確・トレーリングストップ

**新規ファイル**: `src/autotrader/decision/partial_close.py`

戦略:
1. 1R到達時: 50%利確、SLを建値へ移動
2. 2R到達時: 残り25%利確、SLを1Rへ移動
3. 3R以降: トレーリング（0.5R幅）

---

### Phase 5: Walk-Forward検証インフラ

**新規ファイル**: `src/autotrader/backtest/walk_forward.py`

過学習検出:
- IS vs OOS勝率差が10%以上で警告
- PF劣化が30%以上で警告

---

## 修正対象ファイル一覧

| ファイル | Phase | 変更内容 |
|----------|-------|----------|
| `src/autotrader/config/timeframe_preset.py` | 1 | 新規作成 |
| `src/autotrader/decision/short_term_generator.py` | 2 | 新規作成 |
| `src/autotrader/decision/signal_generator.py` | 3 | MTFAlignmentChecker追加 |
| `src/autotrader/decision/partial_close.py` | 4 | 新規作成 |
| `src/autotrader/backtest/walk_forward.py` | 5 | 新規作成 |
| `src/autotrader/backtest/simulator.py` | 4 | 部分決済対応 |
| `scripts/run_full_backtest.py` | 1-5 | 各機能統合 |

---

## 実装順序

| Phase | 内容 | 優先度 |
|-------|------|--------|
| 1 | TimeframePreset | 高 |
| 2 | ShortTermSignalGenerator | 高 |
| 3 | 3層MTFアライメント | 中 |
| 4 | 部分利確・トレーリング | 中 |
| 5 | Walk-Forward検証 | 低 |

---

## 検証計画

```bash
# H1バックテスト（既存）
python scripts/run_full_backtest.py

# M5バックテスト（Phase 2完了後）
python scripts/run_m5_backtest.py

# Walk-Forward検証（Phase 5完了後）
python scripts/run_walk_forward.py
```

### 期待される結果

| Phase | H1勝率 | M5勝率 | 備考 |
|-------|--------|--------|------|
| 現状 | 51.3% | - | - |
| Phase 1-2 | 51.3% | 48%+ | M5開始 |
| Phase 3 | 53%+ | 50%+ | MTF強化 |
| Phase 4 | 55%+ | 52%+ | 利確改善 |
| Phase 5 | 検証 | 検証 | 過学習検出 |

---

## リスクと対策

| リスク | 対策 |
|--------|------|
| 短期足はノイズが多い | min_atr_pips, spread_ratioで除外 |
| トレード数激増 | 時間足別ポジション上限 |
| 過学習 | Walk-Forward検証必須 |
| 部分決済でPF低下 | 1R/2Rの閾値を調整可能に |

---

## Phase 1-5 実装完了ステータス

| Phase | ステータス | 結果 |
|-------|----------|------|
| 1 | ✅ 完了 | TimeframePreset実装済み |
| 2 | ✅ 完了 | ShortTermNoiseFilter実装済み |
| 3 | ✅ 完了 | 3層MTFアライメント実装済み |
| 4 | ✅ 完了 | PartialCloseManager実装済み |
| 5 | ✅ 完了 | WalkForwardValidator実装済み |

### 最終バックテスト結果（2010-2025、16年間）

| 指標 | 改善前 | 改善後 | 変化 |
|------|--------|--------|------|
| 勝率 | 48.9% | 47.2% | -1.7% |
| PF | 1.30 | 1.30 | 維持 |
| 総利益 | ¥1,164,296 | ¥1,188,989 | +¥24,693 |
| 最大DD | 4.08% | 3.67% | -0.41% |
| 黒字年数 | 14/16 | **16/16** | +2年 |

**主要改善点**: 全16年黒字達成、リスク（DD）削減

---

## Phase 6: LLM統合（判定機改善）

### 目的
- シグナル判定の精度向上（Veto判定）
- 信頼度調整によるTP/SL最適化
- トレード根拠の説明可能性向上

### V3 Ollama実装の活用

AutoTraderV3に既存のLLM統合実装を移植:

| V3ファイル | 内容 | V4移植先 |
|-----------|------|----------|
| `adapters/ollama/client.py` | OllamaClient（712行） | `src/autotrader/adapters/ollama/client.py` |
| `adapters/ollama/schemas.py` | TradeSignalOutput, VetoCheckOutput | 同上ディレクトリ |
| `adapters/ollama/prompts.py` | TRADE_SIGNAL_PROMPT, VETO_CHECK_PROMPT | 同上ディレクトリ |

### 実装サブフェーズ

#### Phase 6.1: OllamaClient移植

**新規ファイル**: `src/autotrader/adapters/ollama/`

```python
# client.py
class OllamaClient:
    """Ollamaサーバーとの通信クライアント

    機能:
    - JSON mode対応（structured output）
    - リトライ機構（指数バックオフ）
    - タイムアウト管理
    - エラーハンドリング
    """

    async def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        model: str = "qwen2.5:14b"
    ) -> BaseModel:
        ...
```

```python
# schemas.py
class VetoCheckOutput(BaseModel):
    """Veto判定結果"""
    should_veto: bool
    confidence: float  # 0.0-1.0
    reason: str
    risk_factors: list[str]

class ConfidenceAdjustment(BaseModel):
    """信頼度調整結果"""
    adjusted_confidence: float
    adjustment_reason: str
    market_context: str
```

#### Phase 6.2: DecisionEngine統合

**修正ファイル**: `src/autotrader/decision/signal_generator.py`

```python
class LLMEnhancedSignalGenerator(OptimizedSignalGenerator):
    """LLM強化版シグナルジェネレーター

    処理フロー:
    1. 従来のシグナル生成
    2. 高信頼度シグナル（confidence > 0.7）のみLLM検証
    3. Veto判定でリスク評価
    4. 信頼度調整でTP/SL最適化
    """

    def __init__(self, config: StrategyConfig, ollama_client: OllamaClient):
        super().__init__(config)
        self.ollama = ollama_client
        self.veto_threshold = 0.6  # Veto確信度閾値

    async def generate_with_llm(
        self,
        row: pd.Series,
        candle: Candle,
        symbol: str,
        timeframe: Timeframe
    ) -> Signal | None:
        # 1. 従来シグナル生成
        signal = self.generate(row, candle, symbol, timeframe)
        if signal is None or signal.confidence < 0.7:
            return signal

        # 2. Veto判定
        veto_result = await self._check_veto(signal, row)
        if veto_result.should_veto and veto_result.confidence > self.veto_threshold:
            return None

        # 3. 信頼度調整
        adj = await self._adjust_confidence(signal, row)
        signal.confidence = adj.adjusted_confidence

        return signal
```

#### Phase 6.3: 設定・プロンプト

**新規ファイル**: `src/autotrader/config/llm_settings.py`

```python
@dataclass
class OllamaSettings:
    """Ollama接続設定"""
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:14b"
    timeout_seconds: float = 30.0
    max_retries: int = 3

    # Veto設定
    veto_enabled: bool = True
    veto_confidence_threshold: float = 0.6
    min_signal_confidence_for_llm: float = 0.7

    # パフォーマンス
    cache_responses: bool = True
    cache_ttl_minutes: int = 5
```

**新規ファイル**: `src/autotrader/adapters/ollama/prompts.py`

```python
VETO_CHECK_PROMPT = """
あなたはFXトレードのリスクアナリストです。

## トレードシグナル
- 方向: {direction}
- 通貨ペア: {symbol}
- 時間足: {timeframe}
- エントリー価格: {entry_price}
- ストップロス: {stop_loss}
- テイクプロフィット: {take_profit}

## テクニカル指標
- RSI: {rsi}
- MACD: {macd}
- ADX: {adx}
- トレンド: {trend}

## 質問
このトレードを拒否（Veto）すべきですか？
高リスク要因があれば指摘してください。
"""
```

### 修正対象ファイル一覧（Phase 6）

| ファイル | サブフェーズ | 変更内容 |
|----------|-------------|----------|
| `src/autotrader/adapters/ollama/__init__.py` | 6.1 | 新規作成 |
| `src/autotrader/adapters/ollama/client.py` | 6.1 | V3から移植・改良 |
| `src/autotrader/adapters/ollama/schemas.py` | 6.1 | V3から移植・V4用調整 |
| `src/autotrader/adapters/ollama/prompts.py` | 6.3 | V3から移植・最適化 |
| `src/autotrader/config/llm_settings.py` | 6.3 | 新規作成 |
| `src/autotrader/decision/signal_generator.py` | 6.2 | LLMEnhancedSignalGenerator追加 |
| `scripts/run_llm_backtest.py` | 6.2 | LLM統合バックテスト |

### 検証計画（Phase 6）

```bash
# Ollamaサーバー起動確認
curl http://localhost:11434/api/tags

# 単体テスト（LLMモック使用）
pytest tests/adapters/ollama/ -v

# LLM統合バックテスト（2023-2024年のみ、高速検証）
python scripts/run_llm_backtest.py --years 2023-2024

# 本番バックテスト（全期間）
python scripts/run_llm_backtest.py --years 2010-2025
```

### 期待される効果

| 指標 | Phase 5完了時 | Phase 6目標 | 改善理由 |
|------|--------------|-------------|----------|
| 勝率 | 47.2% | 50%+ | Veto判定で低品質シグナル除外 |
| PF | 1.30 | 1.40+ | 信頼度調整でTP最適化 |
| トレード数 | 4,110 | 3,500前後 | Vetoで10-15%削減 |

### リスクと対策（Phase 6）

| リスク | 対策 |
|--------|------|
| LLMレイテンシ | 高信頼度シグナルのみ検証（全体の20%程度） |
| Ollamaサーバー障害 | フォールバック（LLMなしで継続） |
| 過度なVeto | veto_confidence_threshold調整可能 |
| モデル変更による一貫性低下 | キャッシュ + モデルバージョン固定 |

---

## 全体実装順序（更新版）

| Phase | 内容 | ステータス | 優先度 |
|-------|------|----------|--------|
| 1 | TimeframePreset | ✅ 完了 | - |
| 2 | ShortTermSignalGenerator | ✅ 完了 | - |
| 3 | 3層MTFアライメント | ✅ 完了 | - |
| 4 | 部分利確・トレーリング | ✅ 完了 | - |
| 5 | Walk-Forward検証 | ✅ 完了 | - |
| 6.1 | OllamaClient移植 | 🔲 未着手 | 高 |
| 6.2 | DecisionEngine統合 | 🔲 未着手 | 高 |
| 6.3 | 設定・プロンプト | 🔲 未着手 | 中 |
