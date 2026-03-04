# ファンダメンタル機能 検証バックテスト計画

## Context

PR #376 で有害な Phase 3 機能を無効化し、+3,317K まで回復（推定）。
しかし現在、ファンダメンタルデータ（MT5経済カレンダー + LLM分析結果）が
バックテストで全く活用されていない。

**問い**: ファンダメンタル機能を有効化すれば、さらに収益改善できるか？

### 現状の仕組み

| 機能 | デフォルト | 効果 |
|------|----------|------|
| `has_high_impact_within_30min` | OFF（`--fundamental`で有効） | HIGH指標30分前にエントリースキップ |
| `event_caution_level >= 2` | OFF（同上） | 超重要指標日スキップ（実質不発: HIGH=1, 閾値=2） |
| `fundamental_assessor_enabled` | OFF | Phase 2b: 方向性フィルター・リスク評価 |
| `fundamental_softguard_enabled` | OFF | Phase 2b: SoftGuardペナルティ |
| `fundamental_pm_enabled` | OFF | Phase 2b: PM管理統合 |

### 利用可能データ

- `data/fundamental/events/events_20XX.csv` (2020-2025)
- `data/USDJPY/llm_events_USDJPY_20XX.csv` (2020-2025)
- `data/USDJPY/llm_news_USDJPY_20XX.csv` (2020-2024)

## 検証テスト一覧

worktree `tmp/fundamental_verify` で検証スクリプト `run_fundamental_verify.py` を作成し実行。

### Test 1: Baseline（ファンダメンタルなし）
- 現在のHEAD（PR #376マージ後）でファンダメンタル無効
- `run_unified(2020, 2025)` そのまま
- 期待値: ~+3,317K（bisect Test B相当）

### Test 2: HIGH指標30分前スキップのみ
- `--fundamental` 相当: events CSV読み込み + `has_high_impact_within_30min` でスキップ
- `run_unified(2020, 2025, fundamental_csv_list=[events CSVs])`
- 検証ポイント: HIGHイベント前のエントリー回避で勝率改善するか

### Test 3: Event LLM データ追加
- Test 2 + LLM分析済みイベントCSV読み込み
- `run_unified(..., event_llm_csv_list=[llm_events CSVs])`
- 検証ポイント: LLM分析結果でcaution_level/direction_biasが改善するか

### Test 4: Phase 2b フル有効化
- Test 3 + `fundamental_assessor_enabled=True, fundamental_softguard_enabled=True, fundamental_pm_enabled=True`
- 検証ポイント: アセッサーの方向フィルター・リスク評価で収益向上するか

### Test 5: caution_level閾値引き下げ（HIGH→ブロック）
- Test 2 + `fundamental_caution_block_level=1`（HIGH impact=caution_level 1 でもブロック）
- 検証ポイント: 中インパクト以上のイベント前もブロックする効果

## 実装手順

### Step 1: worktree作成・テストスクリプト準備
```bash
# worktree 作成
git worktree add tmp/fundamental_verify -b verify/fundamental-impact origin/main
# data symlink
ln -s ../../data tmp/fundamental_verify/data
```

### Step 2: 検証スクリプト `run_fundamental_verify.py` 作成

```python
# runner.run_unified() を各テスト条件で呼び出し
# 結果を比較テーブルとして出力
```

主要パラメータ:
- `fundamental_csv_list`: events CSV パスリスト
- `event_llm_csv_list`: LLM events CSV パスリスト
- `config.fundamental_assessor_enabled`: Phase 2b アセッサー
- `config.fundamental_softguard_enabled`: Phase 2b SoftGuard
- `config.fundamental_pm_enabled`: Phase 2b PM
- `config.fundamental_caution_block_level`: ブロック閾値

### Step 3: テスト実行（順次、各30-40分）

Test 1-5 を順次実行。CPU負荷を考慮し並列は最大2本。

### Step 4: 結果分析・レポート

`reports/fundamental_verification.md` に結果を出力。

## 修正対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `run_fundamental_verify.py`（新規） | 検証スクリプト |

既存コードの変更は不要。`runner.run_unified()` のパラメータ制御のみで全テスト実行可能。

## 判断基準

| 結果 | アクション |
|------|----------|
| Test 2-5 いずれかで +100K 以上改善 | 該当機能をデフォルト有効化するPR作成 |
| Test 2-5 全て ±50K 以内 | 現状維持（ファンダメンタルは効果なし） |
| Test 2-5 いずれかで -100K 以上悪化 | 該当機能は明確にOFF維持 |

## 検証方法

各テストの出力を年別テーブルで比較:
- Trades, WR, PF, Profit, DD を比較
- 特に 2020-2021 年（低ボラ期間）での効果に注目
- WR 変動が 1pp 以上あれば有意と判断
