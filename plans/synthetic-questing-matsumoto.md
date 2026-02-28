# BCA（Bidirectional Conviction Assessment）実装計画

## Context

現在のコンセンサスシステム（T=10.0）は勝率60-66%を達成しているが、ユーザーはさらなる勝率向上を要求。
負けトレードの分析から、**方向性が曖昧なエントリー（buy_scoreとsell_scoreが拮抗）** が敗因の主要因と推定。

現在の`conflict_ratio`チェックは二値的（>0.60でブロック）であり、0.55のような「やや危険」なエントリーを通してしまう。
また、`TimeframeSignal → ConsensusTimeframeSignal`変換時に`abs(net_strength)`を使用し、buy/sellの絶対値情報が失われている。

## 目的

- 両方向スコアを連続的に評価し、方向性確信度が低いエントリーをフィルタリング
- HTF（上位足）の逆方向シグナルを重視するウェイト付き反対勢力ペナルティ
- 二値判定→連続ペナルティへの移行で、エッジケースの勝率を改善

## 実装計画

### Step 1: `directional_edge.py` 新規作成

**ファイル**: `autotrader/decision/unified/directional_edge.py`

```python
@dataclass(frozen=True)
class DirectionalEdgeResult:
    directional_edge: float      # (winner - loser) / (winner + loser), 0.0-1.0
    opposition_ratio: float      # loser / winner, 0.0-1.0
    htf_opposition: float        # HTF逆方向の強さ
    ltf_opposition: float        # LTF逆方向の強さ
    passed: bool                 # min_edge以上か
    penalty: float               # SoftGuardに渡すペナルティ
    reasoning: str

class DirectionalEdgeAssessor:
    def __init__(self, min_edge: float = 0.25, penalty_scale: float = 1.0): ...
    def assess(self, consensus: ConsensusResult, tf_signals: dict, tf_set: TimeframeSet) -> DirectionalEdgeResult: ...
```

**コアロジック**:
1. `consensus.buy_score` と `consensus.sell_score` から `directional_edge = (winner - loser) / (winner + loser)` を算出
2. 各TFの`TimeframeSignal`から逆方向strengthを収集し、HTFウェイトで重み付け
   - HTFウェイト: H4=3.0, H1=2.5, M30=2.0, M15=1.5, M5=1.0, M1=0.5
3. `opposition_ratio > threshold` の場合、連続ペナルティを生成
4. `directional_edge < min_edge` の場合、エントリーをブロック（passed=False）

### Step 2: `UnifiedBotConfig` に設定追加

**ファイル**: `autotrader/decision/unified/config.py`

```python
# 追加フィールド（3つ）
bca_enabled: bool = False
bca_min_edge: float = 0.25
bca_penalty_scale: float = 1.0
```

### Step 3: `trade_bot.py` に統合

**ファイル**: `autotrader/decision/unified/trade_bot.py`

`_generate_signal_new()` 内、コンセンサス判定後・SoftGuard前に挿入:

```python
# コンセンサス判定の後（~line 592付近）
if self.config.bca_enabled:
    edge_result = self._edge_assessor.assess(
        consensus_result, tf_signals, tf_set
    )
    if not edge_result.passed:
        return None  # 方向性不十分でスキップ
    # ペナルティをSoftGuardに渡す
    if edge_result.penalty > 0:
        soft_penalties.append(edge_result.penalty)
```

### Step 4: CLI引数追加

**ファイル**: `scripts/run_backtest.py`

```
--bca                  BCA有効化フラグ
--bca-min-edge FLOAT   最小方向性エッジ（デフォルト: 0.25）
--bca-penalty-scale FLOAT  ペナルティスケール（デフォルト: 1.0）
```

BotConfig構築部分（~line 1194-1256）に3フィールドを追加。

### Step 5: ユニットテスト

**ファイル**: `tests/unit/decision/unified/test_directional_edge.py`

- `DirectionalEdgeAssessor` の基本テスト
- エッジケース: 片方向スコア=0、両方向均等、HTF逆方向のみ強い
- ペナルティ計算の正確性
- `passed` 判定の閾値テスト

### Step 6: バックテスト検証

```bash
# BCAなし（ベースライン T=10.0）
uv run python scripts/run_backtest.py --symbol USDJPY \
  --consensus-threshold 10.0 --universal-half-r --progressive-stagnation \
  --consensus-exit --profit-reversal --regime-threshold --htf-score-filter

# BCA有効（デフォルトパラメータ）
uv run python scripts/run_backtest.py --symbol USDJPY \
  --consensus-threshold 10.0 --universal-half-r --progressive-stagnation \
  --consensus-exit --profit-reversal --regime-threshold --htf-score-filter \
  --bca --bca-min-edge 0.25

# BCA有効（厳しめ）
... --bca --bca-min-edge 0.30
```

5年分（2020, 2021, 2022, 2024, 2025）で比較。期待効果: 勝率+2-4pp、取引数-10-20%。

## 修正対象ファイル一覧

| ファイル | 変更種別 |
|---------|---------|
| `autotrader/decision/unified/directional_edge.py` | **新規作成** |
| `autotrader/decision/unified/config.py` | 設定3フィールド追加 |
| `autotrader/decision/unified/trade_bot.py` | BCA統合（~10行） |
| `scripts/run_backtest.py` | CLI引数3つ + BotConfig渡し |
| `tests/unit/decision/unified/test_directional_edge.py` | **新規作成** |

## 既存コード再利用

- `ConsensusResult.buy_score / sell_score` — 既存の両方向スコア（`mode_aware_consensus.py`）
- `TimeframeSignal.buy_strength / sell_strength` — 各TFの方向別強度（`timeframe_evaluator.py`）
- `TimeframeSet` — TF役割定義（`core/timeframe_config.py`）
- `SoftGuardResult.total_penalty` — ペナルティ加算の既存仕組み（`soft_guard.py`）

## 検証基準

- 全年で勝率がベースライン以上
- PFがベースライン以上
- 取引数が50%以上維持（過度なフィルタリング回避）
- 既存テスト全パス + 新規テストパス
