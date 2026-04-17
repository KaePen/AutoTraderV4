# H4キャップ マイクロトレードモード実装計画

## Context

現在のシステムはD1（日足）までの4時間足階層（M5→H1→H4→D1）で動作しているが、
D1に依存することでトレード頻度が制限されている。H4を最大時間足とするモードを追加し、
よりミクロなトレードで回数を増やす方向を検証可能にする。

**目的**: D1モードとH4キャップモードを設定で切り替え可能にし、バックテストで比較検証できるようにする。

## 設計方針

- `max_timeframe` フィールドをUnifiedBotConfigに追加（デフォルト `"D1"` = 既存動作維持）
- 既存コードはD1が上限のまま一切壊さない（config-drivenアプローチ）
- バックテストキューの `overrides.bot.max_timeframe: "H4"` で切り替え可能

### H4キャップ時の時間足構成

| 役割 | D1モード（現行） | H4キャップモード |
|------|-----------------|----------------|
| Entry TF | M5 | M5 |
| Primary TF | H1 | H1 |
| Confirm TFs | [H4, D1] | [H4] |
| Manage TF | D1 | H4 |
| HTF Alignment | [H4, D1] | [H4] |

## 実装ステップ

### Phase 1: Core Config（P0 — 必須）

#### 1-1. `autotrader/decision/unified/config.py`

`UnifiedBotConfig` に `max_timeframe` フィールド追加 + 派生プロパティ:

```python
max_timeframe: str = "D1"

@property
def effective_timeframes(self) -> list[str]:
    """max_timeframe でキャップされた時間足リスト"""
    from autotrader.core.enums import Timeframe
    cap = Timeframe[self.max_timeframe].value
    return [tf for tf in self.timeframes if Timeframe[tf].value <= cap]

@property
def effective_htf_alignment_tfs(self) -> list[str]:
    from autotrader.core.enums import Timeframe
    cap = Timeframe[self.max_timeframe].value
    return [tf for tf in self.htf_alignment_tfs if Timeframe[tf].value <= cap]

@property
def effective_manage_tf(self) -> str:
    from autotrader.core.enums import Timeframe
    cap = Timeframe[self.max_timeframe].value
    if Timeframe[self.default_manage_tf].value <= cap:
        return self.default_manage_tf
    return self.max_timeframe
```

注意: `UnifiedBotConfig` は `frozen=True` なので `@property` は使える。`SignalConfig` にも同様の `max_timeframe` を追加し `to_signal_config()` で伝播させる。

#### 1-2. `autotrader/decision/unified/trade_bot.py`

3箇所の変更:

- **L509**: `self.timeframes = self.config.timeframes` → `self.config.effective_timeframes`
- **L1086**: `_htf_tfs = list(self.config.htf_alignment_tfs)` → `list(self.config.effective_htf_alignment_tfs)`
- **L2368, L2945**: `htf_tfs or list(self.config.htf_alignment_tfs)` → `htf_tfs or list(self.config.effective_htf_alignment_tfs)`

#### 1-3. `autotrader/decision/unified/mode_selector.py`

`TradingPlan.create_universal()` で `confirm_tfs` 構築時:
- `config.timeframes` → `config.effective_timeframes`
- `manage_tf` → `config.effective_manage_tf`

### Phase 2: Signal Pipeline（P1）

#### 2-1. `autotrader/decision/unified/dynamic_tf_selector.py`

`MAJOR_TF_LADDER` をインスタンス変数化し、`__init__` で `max_timeframe` によるフィルタを適用:

```python
def __init__(self, config, ...):
    cap = Timeframe[config.max_timeframe].value
    self._tf_ladder = [tf for tf in self.MAJOR_TF_LADDER if Timeframe[tf].value <= cap]
```

#### 2-2. `autotrader/decision/unified/pipeline_pkg/directional_edge.py`

変更不要。`_HTF_SET` / `_HTF_WEIGHTS` は存在するTFのみ参照するため、D1データが来なければ自然にスキップされる。

#### 2-3. `autotrader/calculator/features/mtf_features.py`

変更不要。`calculate_higher_tf_bias()` の `higher_tf` リストは渡されたデータの存在チェックを行うため、D1データがなければスキップされる。

### Phase 3: バックテストデータ読み込み（P1）

#### 3-1. `autotrader/backtest/runner.py`

D1データ読み込みを `max_timeframe` で条件化:

- `load_data()` 内のD1読み込み箇所を `if Timeframe[max_tf].value >= Timeframe.D1.value:` で囲む
- `_load_multi_tf_data()` の `timeframes_to_load` リストを `effective_timeframes` でフィルタ

#### 3-2. `autotrader/backtest/parallel_worker.py` / `month_runner.py`

変更不要。`year_market_data.get("D1")` は D1未ロード時に `None` を返し、既存のNull処理が機能する。

### Phase 4: コンセンサス閾値調整

H4キャップ時はTF数が減り最大到達可能スコアが下がるため、閾値調整が必要。

**方針**: `consensus_threshold` を直接オーバーライドで設定する（既存のYAML overrides機構を使う）。

目安計算:
- 現行D1モード: 閾値18.0（M5〜D1の8TF評価）
- H4キャップ: 約13.5（= 18.0 × 6/8、H8とD1が脱落）

初期値 **13.5** でバックテスト検証し調整。

### Phase 5: バックテスト検証

キューランナーで以下のジョブを投入して比較:

```json
{
  "jobs": [
    {
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "H4キャップ マイクロモード検証",
      "overrides": {
        "bot": {
          "max_timeframe": "H4",
          "consensus_threshold": 13.5
        }
      }
    },
    {
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "D1モード ベースライン（比較用）"
    }
  ]
}
```

## 変更対象ファイル一覧

| ファイル | 変更内容 | 優先度 |
|---------|---------|--------|
| `autotrader/decision/unified/config.py` | `max_timeframe` + 派生プロパティ追加 | P0 |
| `autotrader/decision/unified/trade_bot.py` | `effective_*` プロパティ利用（4箇所） | P0 |
| `autotrader/decision/unified/mode_selector.py` | `effective_timeframes` 利用 | P0 |
| `autotrader/decision/unified/dynamic_tf_selector.py` | TFラダーのキャップ | P1 |
| `autotrader/backtest/runner.py` | D1読み込みの条件化 | P1 |

**変更不要（自然にスキップされる）**: `directional_edge.py`, `mtf_features.py`, `parallel_worker.py`, `month_runner.py`, `tf_params_registry.py`

## 検証方法

1. **既存テスト**: `pytest` 全テスト通過（D1デフォルト動作が壊れていないこと）
2. **バックテスト比較**: USDJPY 2023-2025で D1モード vs H4キャップモードを実行
   - トレード回数の増加を確認
   - 勝率・PnLプロファイルの比較
   - SL/TP分布の健全性チェック
3. **エッジケース**: `max_timeframe="H4"` 時にD1関連のエラーが出ないことを確認
