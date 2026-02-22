# バックテスト→リアルモード機能移行 & 重複ロジック統合計画

## Context

バックテストとリアルトレードは同一のシグナル生成ロジック（`UnifiedTradeBot`）を使っているが、
エントリー判定・ポジション管理の一部機能がバックテスト側にしか実装されていない。
また `pip_size` / `pip_value` の計算が `live/engine.py` 内の4箇所で重複している。

**目標**: バックテストで検証した機能が自動的にライブにも反映されるアーキテクチャへ

## 具体的な差異（バックテストのみ）

| 機能 | バックテスト | ライブ |
|------|-----------|-------|
| 品質ベース動的ポジション枠 | `BacktestConfig.bonus_max_positions/bonus_score_threshold` → `SimulatorConfig` | 未実装（単純な `max_positions` チェック） |
| pip_size計算 | `SimulatorConfig.pip_unit` フィールドとして保持 | 4箇所でインライン計算（重複） |
| pip_value計算 | `SimulatorConfig.pip_value` フィールドとして保持 | 2箇所でインライン計算（重複） |

## 変更ファイル

1. `src/autotrader/decision/unified/config.py` - `UnifiedBotConfig` に bonus フィールド追加
2. `src/autotrader/live/engine.py` - bonus チェック実装 + pip ヘルパーメソッド追加

## 実装ステップ

### Step 1: `UnifiedBotConfig` に bonus フィールドを追加

**ファイル**: `src/autotrader/decision/unified/config.py`

`UnifiedBotConfig` に以下を追加（`demo_max_positions` の直後、行160付近）:

```python
# 品質ベース動的ポジション枠（バックテストと同一ロジック）
bonus_max_positions: int = 0
bonus_score_threshold: float = 7.0
```

### Step 2: `live/engine.py` に `_get_pip_size` / `_get_pip_value` ヘルパーを追加

**ファイル**: `src/autotrader/live/engine.py`

`_build_sizer_config` の直後あたりに追加:

```python
@staticmethod
def _get_pip_size(symbol: str) -> float:
    """通貨ペアのpipサイズを返す（JPY系=0.01、その他=0.0001）"""
    return 0.01 if "JPY" in symbol.upper() else 0.0001

@staticmethod
def _get_pip_value(symbol: str) -> float:
    """通貨ペアの1lot/1pipあたりの価値を返す（JPY系=1000、その他=10）"""
    return 1000.0 if "JPY" in symbol.upper() else 10.0
```

### Step 3: `_execute_entry` の bonus ポジション枠チェック実装

**ファイル**: `src/autotrader/live/engine.py`, L900-910付近

現在:
```python
max_pos = (
    cfg.demo_max_positions
    if cfg.demo_mode
    else cfg.max_positions
)
if len(positions) >= max_pos:
    ...
```

変更後:
```python
base_max = (
    cfg.demo_max_positions if cfg.demo_mode else cfg.max_positions
)
bonus = getattr(cfg, "bonus_max_positions", 0)
threshold = getattr(cfg, "bonus_score_threshold", 7.0)
if (
    bonus > 0
    and signal.consensus_score is not None
    and signal.consensus_score >= threshold
):
    max_pos = base_max + bonus
else:
    max_pos = base_max
if len(positions) >= max_pos:
    ...
```

### Step 4: `live/engine.py` 内の重複 pip 計算をヘルパーに置き換え

以下4箇所を `self._get_pip_size(symbol)` / `self._get_pip_value(symbol)` に置き換え:

| 箇所 | メソッド | 変数 |
|------|---------|------|
| L1033 | `_register_new_position` | `pip_size` |
| L1124 | `_write_entry_to_db` | `pip_size` |
| L1252 | `_write_close_to_db` | `pip_size` + `pip_val` |
| L1369 | `_manage_positions` | `pip_factor` + `pip_value` |

## 変更しないもの

- `SimulatorConfig.pip_unit` / `pip_value`: バックテスト用シミュレーター固有（設定として保持が適切）
- `_calc_indicators` ラッパー: エラーハンドリング付きで意味がある
- セッション別スプレッド: バックテスト固有のシミュレーション機能
- MFE/MAE追跡: バックテスト分析専用

## 検証

1. 既存テストをすべて実行: `pytest tests/ -x -q`
2. `UnifiedBotConfig` の `bonus_max_positions=1, bonus_score_threshold=7.0` で、
   consensus_score >= 7.0 のシグナルで最大ポジション数が増加することを確認

## ワークフロー

```bash
BRANCH="feat/live-bonus-positions-and-pip-helper"
WORKTREE="/d/Projects/AutoTraderV4/tmp/feat_live-bonus-positions-and-pip-helper"
git -C /d/Projects/AutoTraderV4 pull origin main
git -C /d/Projects/AutoTraderV4 branch "$BRANCH"
git -C /d/Projects/AutoTraderV4 worktree add "$WORKTREE" "$BRANCH"
# 編集後
git -C "$WORKTREE" add src/autotrader/decision/unified/config.py src/autotrader/live/engine.py
git -C "$WORKTREE" commit -m "feat: bonus_max_positionsをライブモードに移行、pip計算ヘルパー統一"
git -C "$WORKTREE" push -u origin "$BRANCH"
"C:/Program Files/GitHub CLI/gh.exe" pr create --repo KaePen/AutoTraderV4 --base main ...
git -C /d/Projects/AutoTraderV4 worktree remove "$WORKTREE" --force
git -C /d/Projects/AutoTraderV4 branch -d "$BRANCH"
```
