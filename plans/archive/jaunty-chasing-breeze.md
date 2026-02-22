# git checkout復元後の残存差分修正

## Context

前セッションで10-fix計画を実行→性能低下→`git checkout --`でリバートしたが、
pre-mod版の未コミット変更も破壊された。trade_bot.pyはセッション記録から再構築済み。
本計画は、残存するトレードロジック差分を修正する。

## 調査結果

### git checkout で戻された5ファイルの復元状態

| ファイル | pre-mod未コミット変更 | 復元状態 |
|---|---|---|
| `trade_bot.py` | あり | ✅ セッション記録から完全復元 |
| `position_manager.py` | あり | ⚠️ 1箇所未復元 |
| `engine.py` | あり | Web UI関連のみ（トレードロジック外） |
| `timeframe_evaluator.py` | なし | ✅ committed = pre-mod |
| `mode_selector.py` | なし | ✅ committed = pre-mod |

### トレードロジックに影響する唯一の差分

`src/autotrader/decision/unified/position_manager.py` line 239:

```python
# 現在（committed版）:
range_day_half_r_partial_enabled: bool = False

# pre-mod版（正しい状態）:
range_day_half_r_partial_enabled: bool = True
```

**影響**:
- RANGE×DAY_TRADEモードで0.5Rに到達時、20%部分利確+BE移動が発動しない
- バックテストスクリプト: 常にTrueを明示指定するため**影響なし**
- ライブトレード: デフォルト設定で影響あり

### 復元済み確認項目（変更不要）

- `sg_context`: 2キー（`spread_pips: 1.5`, `current_time`）✅
- `_score_htf_alignment()`: 加算ボーナス 4.0/2.0/0.0 ✅
- `MAX_POSSIBLE_SCORE = 15.0` ✅
- EMAクロスペナルティ: ±0.5 / -2.5 ✅
- `consolidate()` 直接呼び出し（check_entry_conditionsではない）✅
- demo_mode分岐（__init__, _init_new_components, _generate_signal_new）✅

---

## 修正内容

### Step 1: デフォルト値修正

**ファイル**: `src/autotrader/decision/unified/position_manager.py`

```python
# line 239: False → True
range_day_half_r_partial_enabled: bool = True
```

### Step 2: テスト修正

前回この変更で7テスト失敗した。原因はテストがデフォルト`False`を前提としていたため。
テストファイル `tests/unit/decision/unified/test_position_manager.py` で
`range_day_half_r_partial_enabled`のデフォルト値アサーションまたは
0.5R部分利確の発動を否定するアサーションを修正する。

### Step 3: 検証

```bash
pytest tests/ -x
```

全395テストPASS確認。
