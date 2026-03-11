# トレード品質改善計画

## Context

リアルトレードでの勝率に不安があり、過剰フィッティングを避けつつトレード品質を本質的に向上させる必要がある。
現状: WR 73-77% (JPYペア), avg win +0.67R / avg loss -1.0R, DD 2-3%。
目標: WR ≥80%, DD <1%, 年利 +80%（risk_pctで調整可能なのでWR向上が本質的課題）。

### 問題分析
1. **R:R比が悪い**: 平均勝ち +0.67R vs 平均負け -1.0R → 利益を伸ばせていない
2. **未使用データ**: TICKVOL がMT5 CSVに含まれるが一切使われていない
3. **バックテスト解像度が低い**: M5基準ループ → M1の動きが見えない。ライブは1秒ごと評価なのにバックテストは5分ごと
4. **エントリータイミング不在**: コンセンサスで「何を買うか」は判断するが「いつ買うか」は判断していない。M1が反転中でもエントリーする
5. **Walk-forward未自動化**: インフラはあるが定常検証なし

## Phase 0: バックテスト基盤刷新 (P0) ★最優先

### A0: 月単位並列バックテスト
- **ファイル**: `autotrader/backtest/month_runner.py` 新規作成、`scripts/backtest_queue_runner.py` 改修
- **内容**:
  - 年単位ループ（year_runner.py）を月単位に分割
  - 各月は100万円スタート（equity独立、完全並列化）
  - 12ヶ月 × 12スレッドで並列実行
  - インジケータはPrecomputeEngineで事前計算済み → ウォームアップ不要
  - **月末処理**: オープンポジションは月末最終足で強制クローズ
  - **月末レポート**: 月末強制クローズされたトレードにフラグを付け、影響分析を出力（月末クローズ有無でのWR/PF比較）
  - 12ヶ月の結果をマージして年間サマリーを計算
- **理由**: M1解像度にすると5倍遅くなるが、12並列で相殺以上。途中保存も容易
- **参考**: 既存の `year_runner.py` のrun_unified_year()、PrecomputeEngine

### A1: M1基準ループをデフォルト化
- **ファイル**: `autotrader/backtest/year_runner.py`（またはA0のmonth_runner.py）
- **内容**: `use_m1=True` をデフォルトに変更。全シグナル生成がM1足確定ごとに発火
- **効果**:
  - エントリータイミングの解像度: 5分→1分（ライブの1秒に近づく）
  - Exit判定の解像度: M5のHigh/Low → M1のHigh/Low（SL/TP判定精度向上）
  - 同一足SL/TP問題: M1解像度で発生頻度が大幅低下（旧A1の課題が自然解決）

### A2: スプレッドストレステスト
- **状態**: 既にPR #585でspread_multiplier実装済み
- **検証**: V1 (2x spread), V2 (3x spread) のキュー投入済み、結果待ち

### A3: Walk-forward検証
- **ファイル**: `autotrader/backtest/walk_forward.py`
- **内容**: 既存インフラを活用し、キューランナーから呼び出せるWFジョブタイプを追加
- **検証パターン**:
  - **パターンA（ローリング）**: IS 3年 → OOS 1年をスライド（5回）
  - **パターンB（ストレステスト）**: IS 2018-2020 → OOS 2021-2025
  - 両パターンとも実施

## Phase 1: エントリー品質向上 (P1)

### B0: M1モメンタム確認ゲート ★重要
- **ファイル**: `autotrader/decision/unified/trade_bot.py`（既存の_pending_entry機構を活用）
- **内容**:
  - コンセンサス通過後、即エントリーせず最大N本（3-5分）M1を監視
  - M1の直近2-3本がトレード方向にモメンタムを持つことを確認してからエントリー
  - タイムアウト（N本以内に確認取れなければ見送り）
- **前回の失敗との違い**:
  - M1 Execution Gate（-82K）: スプレッド+モメンタムで判定 → 条件が複合的で有効トレードもブロック
  - M1 Retrace Entry（-15K）: 特定価格レベルを待つ → 来ないことが多い
  - 今回: **方向の勢いだけ**を確認（条件がシンプル、機会損失が少ない）
- **A1（M1基準）が前提**: M5基準では1分ごとの監視ができない
- **過剰フィットリスク**: 低（エントリータイミングの調整は普遍的手法）

### B1: ボリューム確認フィルタ（TICKVOL活用）
- **ファイル**:
  - `autotrader/calculator/features/volume_analyzer.py` 新規作成
  - `autotrader/decision/unified/scoring/timeframe_evaluator.py` にボリューム重み追加
- **内容**:
  - TICKVOL データは既に `Candle.tick_volume` として読み込み済み
  - ボリューム移動平均比率を計算（current_vol / MA(20) > 1.2 で確認）
  - エントリー時にボリューム確認が取れない場合はペナルティ（SoftGuardに追加）
- **理由**: 本物のブレイクアウトはボリュームを伴う。フェイクブレイクアウト排除に効果的
- **過剰フィットリスク**: 低（市場構造の普遍的原則）

## Phase 2: 利益伸長 (P2)

### C1: ATR動的TP（R:R改善）
- **ファイル**: `autotrader/decision/unified/pipeline_pkg/pipeline.py` (SizingStep内SL/TP計算)
- **内容**:
  - 現在: `tp_pips = sl_pips * tp_sl_ratio` (固定比率 1.2)
  - 変更: レジームに応じてTP比率を動的調整
    - TREND: tp_sl_ratio = 1.5〜2.0
    - RANGE: tp_sl_ratio = 1.0
    - HIGH_VOL: tp_sl_ratio = 1.3
- **理由**: TRENDで利益を伸ばせない根本原因
- **過剰フィットリスク**: 低

### C2: 2段階トレーリング
- **ファイル**: `autotrader/decision/unified/risk/position_manager.py`
- **内容**:
  - Stage 1 (0.5R-1.5R): ATR × 2.0（現行、広め）
  - Stage 2 (1.5R+): ATR × 1.2（引き締め、利益確定重視）
- **理由**: avg win +0.67R → +1.0R以上を狙う
- **過剰フィットリスク**: 低

## 実装順序

```
Phase 0 (基盤):
  1. A0 (月単位並列バックテスト) — 全改善の前提基盤
  2. A1 (M1基準ループ) — エントリー/Exit解像度向上

Phase 1 (エントリー品質):
  3. B0 (M1モメンタム確認ゲート) — エントリータイミング改善
  4. B1 (ボリュームフィルタ) — フェイクブレイクアウト排除

Phase 2 (利益伸長):
  5. C1 (ATR動的TP) — R:R改善
  6. C2 (2段階トレーリング) — 利益確定改善

最終検証:
  7. A3 (Walk-forward) — 全改善のOOS検証
```

各ステップで:
1. worktree で実装
2. 1回テスト実行で動作確認
3. PR作成
4. マージ後キューランナーでA/Bテスト（変更前後比較）

## フローチャート更新

`reports/archive/autotrader_v4_logic_flowchart.drawio` を `userdoc/flow/autotrader_architecture_flowchart.drawio` にコピーし、変更箇所を赤文字・赤枠で表示:

### Page 1 (システム全体概要)
- メインループを「M1足ごと」に赤文字で更新
- Phase 2 シグナル生成に「M1モメンタム確認」「TICKVOL確認」を赤文字追加
- Phase 4 ポジション管理に「2段階トレーリング」を赤文字追加

### Page 2 (シグナル生成パイプライン)
- Step 1 リトレース待機の後に「Step 1b: M1モメンタム確認」を赤枠で追加
- Step 5 フィルタに「5d. ボリューム確認フィルタ」を赤枠で追加
- Step 5h SL/TP計算に「レジーム別動的TP比率」を赤文字追記

### Page 3 (ポジション管理)
- ⑩ トレーリングを「2段階: 0.5R→ATR×2.0 / 1.5R→ATR×1.2」に赤文字で更新

### Page 5 (制約・ガード・フィルタ)
- SoftGuardに「ボリューム不足」ペナルティを赤枠で追加

### 新規 Page 6: 月単位並列バックテスト構成
- 月分割 → 12並列 → マージのフローを赤枠で追加

## QI Round 1 結果まとめ (完了)

| テスト | 設定 | USDJPY結果 | 判定 |
|--------|------|-----------|------|
| QI-T0 | Baseline（全OFF） | baseline | — |
| QI-T1 | B0 M1モメンタムゲート ON | -3,298K (-36%), WR -2.1pp | **REJECTED** |
| QI-T2 | B1 ボリュームフィルタ ON | T0と同一（バグ） | 再検証必要 |
| QI-T3 | C1 動的TP ON | T0と同一（トレーリング先行） | **NO_EFFECT** |
| QI-T4 | C2 2段階トレーリング ON | +94K (+1.7%), 4トレード増 | MARGINAL |
| QI-T5 | B1+C2 ON | 未確認 | 再検証必要 |

### バグ修正済み (PR #598, #599)
- B0: デフォルトOFF化（有害）
- B1: `volume_ratio` 計算が `_calculate_indicators()` に欠落 → 追加
- C1: デフォルトOFF化（トレーリング0.5Rが先に発動しTPに到達しない）
- インジケーターキャッシュ: 全ペア削除済み（volume_ratio再計算のため）

---

## QI Round 2 検証計画 (次のアクション)

### 目的
PR #598/#599 のバグ修正後、B1（ボリュームフィルタ）とC2（2段階トレーリング）の効果を正しく検証する。

### テストマトリクス

USDJPY + EURJPY の2ペアで、B1/C2の2×2テスト:

| Job ID | ペア | B1 (volume) | C2 (trailing) | 目的 |
|--------|------|-------------|---------------|------|
| QI2-T0-USDJPY | USDJPY | OFF | OFF | Baseline |
| QI2-T1-USDJPY | USDJPY | ON | OFF | B1単独効果 |
| QI2-T2-USDJPY | USDJPY | OFF | ON | C2単独効果 |
| QI2-T3-USDJPY | USDJPY | ON | ON | B1+C2複合 |
| QI2-T0-EURJPY | EURJPY | OFF | OFF | Baseline |
| QI2-T1-EURJPY | EURJPY | ON | OFF | B1単独効果 |
| QI2-T2-EURJPY | EURJPY | OFF | ON | C2単独効果 |
| QI2-T3-EURJPY | EURJPY | ON | ON | B1+C2複合 |

### 実行設定
- 年: 2020-2025（6年）
- モード: 月並列 (`max_month_workers: 6`)
- TF: M1,M5,M15,M30,H1,H4,H8,D1
- B0 (M1モメンタムゲート): 全テストOFF
- C1 (動的TP): 全テストOFF

### パラメータ
```
B1 ON:  volume_filter_enabled=true, volume_filter_threshold=0.8, volume_filter_penalty=0.3
B1 OFF: volume_filter_enabled=false
C2 ON:  trailing_stage2_enabled=true, trailing_stage2_r=1.5, trailing_stage2_atr_multiplier=1.2
C2 OFF: trailing_stage2_enabled=false
```

### 判定基準
- **採用**: WR +1pp以上 or PF +0.1以上、DD悪化なし
- **棄却**: WR -1pp以上 or DD +1pp以上
- **中立**: 上記いずれにも該当しない → OFF維持

### 判定後フロー
| B1結果 | C2結果 | 次のアクション |
|--------|--------|---------------|
| 採用 | 採用 | 両方ON → 全6JPYペアで検証 |
| 採用 | 棄却/中立 | B1のみON → 全6JPYペアで検証 |
| 棄却/中立 | 採用 | C2のみON → 全6JPYペアで検証 |
| 棄却/中立 | 棄却/中立 | Phase 1-2終了、Walk-forward検証へ |

### キューJSON（実行時に投入）
ファイル: `D:\Projects\AutoTraderV4_data\backtest_queue.json`

```json
{
  "jobs": [
    {
      "id": "QI2-T0-USDJPY",
      "symbol": "USDJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": false,
        "trailing_stage2_enabled": false
      }
    },
    {
      "id": "QI2-T1-USDJPY",
      "symbol": "USDJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": true,
        "volume_filter_threshold": 0.8,
        "volume_filter_penalty": 0.3,
        "trailing_stage2_enabled": false
      }
    },
    {
      "id": "QI2-T2-USDJPY",
      "symbol": "USDJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": false,
        "trailing_stage2_enabled": true,
        "trailing_stage2_r": 1.5,
        "trailing_stage2_atr_multiplier": 1.2
      }
    },
    {
      "id": "QI2-T3-USDJPY",
      "symbol": "USDJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": true,
        "volume_filter_threshold": 0.8,
        "volume_filter_penalty": 0.3,
        "trailing_stage2_enabled": true,
        "trailing_stage2_r": 1.5,
        "trailing_stage2_atr_multiplier": 1.2
      }
    },
    {
      "id": "QI2-T0-EURJPY",
      "symbol": "EURJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": false,
        "trailing_stage2_enabled": false
      }
    },
    {
      "id": "QI2-T1-EURJPY",
      "symbol": "EURJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": true,
        "volume_filter_threshold": 0.8,
        "volume_filter_penalty": 0.3,
        "trailing_stage2_enabled": false
      }
    },
    {
      "id": "QI2-T2-EURJPY",
      "symbol": "EURJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": false,
        "trailing_stage2_enabled": true,
        "trailing_stage2_r": 1.5,
        "trailing_stage2_atr_multiplier": 1.2
      }
    },
    {
      "id": "QI2-T3-EURJPY",
      "symbol": "EURJPY",
      "years": [2020, 2021, 2022, 2023, 2024, 2025],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_month_workers": 6,
      "overrides": {
        "volume_filter_enabled": true,
        "volume_filter_threshold": 0.8,
        "volume_filter_penalty": 0.3,
        "trailing_stage2_enabled": true,
        "trailing_stage2_r": 1.5,
        "trailing_stage2_atr_multiplier": 1.2
      }
    }
  ]
}
```

### 前提条件（実装済み）
- [x] B1 volume_ratio計算を `_calculate_indicators()` に追加 (PR #599)
- [x] B0 デフォルトOFF (PR #598)
- [x] C1 デフォルトOFF (PR #599)
- [x] 全ペアインジケーターキャッシュ削除済み
- [ ] コード変更不要（全て設定値の切り替えのみ）
