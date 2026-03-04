# EURJPY トレードロジック最適化計画

## Context

USDJPYでは6フェーズの最適化（Round2→Round3→BCA v2→Phase3 bisect→ファンダメンタル検証）を経て、
6年合計 +2,302K / WR 55.8% / PF 1.87 / DD 2.81% に到達した。

EURJPYはインフラ面で完全対応済み（データ16年分、プリセット、コード全て準備完了）だが、
USDJPYで最適化されたパラメータがEURJPYに適合するかは未検証。
本計画ではUSDJPYの最適化手法を踏襲し、段階的にEURJPYのパラメータを最適化する。

## 前提: 軽微なコード修正

`autotrader/constraint/filters/event_filter.py` の `_currency_map` にEURJPYを明示追加。
（フォールバックで動作するが整合性のため修正）

## 検証ステップ（5段階）

USDJPYの判断基準を踏襲:
- 改善幅 +100K以上 → 採用
- ±50K以内 → 効果なし（現状維持）
- 悪化 -100K以上 → 不採用

### Step 1: ベースライン確立（現デフォルト設定でEURJPY実行）

現在のデフォルト設定でEURJPY 2020-2025 6年バックテストを実行し、ベースラインを確立する。

```bash
uv run python scripts/run_backtest.py \
  --symbol EURJPY --years 2020-2025 \
  --max-year-workers 6 -v \
  2>&1 | tee reports/eurjpy_step1_baseline.txt
```

**確認項目**: Profit, WR, PF, DD, 年別内訳、月別勝率
**判断**: 利益が出るか、年別に安定しているか確認。赤字年がある場合は特に注目。

### Step 2: consensus_threshold スイープ（3条件）

USDJPYではT=9.0が最適だった。EURJPYは値動き特性が異なるため再検証。

| テスト | 設定 | コマンド追加オプション |
|--------|------|----------------------|
| T8 | T=8.0 | `--consensus-threshold 8.0` |
| T9 | T=9.0（デフォルト） | Step1と同一 |
| T10 | T=10.0 | `--consensus-threshold 10.0` |

Step1がT=9.0なので、T8とT10の2本を追加実行。

### Step 3: BCA min_edge スイープ（3条件）

USDJPYではmin_edge=0.55が最適だった。

| テスト | 設定 | コマンド追加オプション |
|--------|------|----------------------|
| BCA045 | min_edge=0.45 | `--bca-min-edge 0.45` |
| BCA055 | min_edge=0.55（デフォルト） | Step1/2の最適Tと同一 |
| BCA065 | min_edge=0.65 | `--bca-min-edge 0.65` |
| NoBCA | BCA無効 | `--no-bca` |

Step2で最適化されたTを使い、BCAパラメータを検証。

### Step 4: Phase3機能 bisect（最大4条件）

USDJPYでは very_early_exit が最大劣化要因(-935K)だった。
EURJPYでも同様の影響があるか個別検証。

| テスト | 設定 |
|--------|------|
| ALL_OFF | 全Phase3機能OFF |
| HEAD | 全ON（Step2+3の最適設定） |
| VEE | very_early_exit のみON |
| PS | progressive_stagnation のみON |

Step2+3で確定した最適Tと最適BCAを使用。

### Step 5: ファンダメンタル検証（2条件）

USDJPYではT3（events+LLM events）のみ採用された。

| テスト | 設定 |
|--------|------|
| NoFund | ファンダメンタルなし（Step4最適設定） |
| T3 | events CSV + LLM events | `--fundamental --event-llm` |

## 最終成果物

1. `reports/eurjpy_optimization_report.md` - 全テスト結果の比較表
2. `config/symbol_presets.yaml` - EURJPY最適パラメータ反映（必要に応じて）
3. MEMORY更新 - EURJPY最適設定と判断ログ

## 実行方針

- `--max-year-workers 6` で6年分を一括並列実行（12スレッド消費）
- ユーザーがゲーム中の場合は `--max-year-workers 3` に減らす
- バックテスト同時実行は最大2本まで
- 各Stepの結果を確認してから次Stepに進む（逐次判断）
- 全テスト共通: `--symbol EURJPY --years 2020-2025 --max-year-workers 6`
- 結果は `reports/eurjpy_stepN_*.txt` に保存

## 関連ファイル

- `config/symbol_presets.yaml` - 通貨ペア別プリセット
- `autotrader/constraint/filters/event_filter.py` - イベントフィルタ（EURJPY追加）
- `scripts/run_backtest.py` - バックテスト実行スクリプト
- `autotrader/backtest/runner.py` - BacktestRunner
- `autotrader/decision/unified/config.py` - UnifiedBotConfig
