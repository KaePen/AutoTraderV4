# What-If分析 → ペア別パラメータ最適化

## Context

バックテストのブロックシグナルを「もしトレードしていたら」分析し、
各フィルタの機会損失を定量化 → 当たりのあるパラメータを特定してから調整する。

USDJPYは既にVQ検証（BBW20, PCAP50等）で調整済みだが、What-If分析で
追加の改善余地があるか先に確認し、検証アプローチを固めてから他ペアに展開。

## Phase 1: USDJPY What-If分析（既存データ活用）

### 1A. 分析スクリプト作成

**新規**: `scripts/analyze_whatif.py`（~300行）

入力: `backtest_results/093_AN-USDJPY-WI/whatif_trades.csv`（1,766,822行）

**分析内容**:

1. **ブロック理由別統計**: カテゴリ別の件数、WR、平均pips、合計pips
2. **スコアバケット分析**: consensus_score [8-9), [9-10), [10-11), [11-12), [12+) 別WR
3. **レジーム別分析**: TREND vs RANGE でフィルタ効果が異なるか
4. **IS/OOS比較**: 2020-2023 vs 2024-2025 でWRが安定しているか（差>5ppなら不安定）

**ブロック理由カテゴリマッピング**:

| パターン | カテゴリ | 対応パラメータ |
|----------|----------|----------------|
| スコア不足 | Score_below | consensus_threshold |
| ペナルティ | Penalty | penalty_cap |
| SoftGuard | SoftGuard | sg_* penalties |
| HTF | HTF | htf_score_filter_* |
| BCA | BCA | bca_min_edge |
| MACD | MACD | macd_slope_filter_threshold |
| トレンド強度 | Trend_strength | trend_strength_max |
| レンジ/RANGE | RANGE | range_day_bbw_threshold |
| ボリューム | Volume | volume_filter_* |

**What-If SL/TP制約の注記**:
- ブロックシグナルは動的SL/TP未計算のため、固定 SL=20/TP=24 pips で仮想トレード
- WR損益分岐点 = 20/(20+24) = 45.5%
- **WR > 45.5% なら「フィルタなしでも利益」**
- ただし実際のシステムWR（77%）より大幅に低いとシステム品質を下げる
- この分析は**優先順位付け**に使い、最終判定はPhase 2の実バックテストで行う

**出力**: `reports/whatif_analysis_USDJPY.md`

### 1B. 分析実行・レビュー

```bash
uv run python scripts/analyze_whatif.py --symbol USDJPY
```

**判断基準**:
- VQ検証（BBW20/PCAP50）で既に見つけたもの以外に、新しい改善候補があるか？
- あれば → Phase 2でUSDJPY追加検証 + 他ペアにも同じ分析パイプライン適用
- なければ → VQ検証結果を採用し、他ペアのWhat-If収集・分析に移る

## Phase 2: USDJPY追加検証（Phase 1の結果次第）

Phase 1で新しい改善候補が見つかった場合のみ実施。

**キュージョブ**: 1候補につき1ジョブ、6年バックテスト
```json
{
  "id": "VQ2-T{N}-USDJPY-{PARAM}",
  "symbol": "USDJPY",
  "years": "2020-2025",
  "overrides": { "bot": { "param": value } }
}
```

**採用基準**:
- 利益増加 > 0
- WR低下 < 2pp
- DD増加 < 1pp
- OOS（2024-2025）でIS（2020-2023）と同方向

## Phase 3: 残り5ペア What-If収集 + 分析

### 3A. What-If バックテスト実行

EURJPY（094_AN-EURJPY-WI）は既にデータあり。残り4ペアを収集:

```json
{
  "jobs": [
    { "id": "AN-GBPJPY-WI", "symbol": "GBPJPY", "years": "2020-2025",
      "overrides": { "backtest": { "whatif_enabled": true } } },
    { "id": "AN-AUDJPY-WI", "symbol": "AUDJPY", "years": "2020-2025",
      "overrides": { "backtest": { "whatif_enabled": true } } },
    { "id": "AN-CADJPY-WI", "symbol": "CADJPY", "years": "2020-2025",
      "overrides": { "backtest": { "whatif_enabled": true } } },
    { "id": "AN-CHFJPY-WI", "symbol": "CHFJPY", "years": "2020-2025",
      "overrides": { "backtest": { "whatif_enabled": true } } }
  ]
}
```

### 3B. 全6ペア分析

```bash
uv run python scripts/analyze_whatif.py --all
```

**出力**: ペア別レポート + クロスペアサマリー `reports/whatif_analysis_all_pairs.md`

クロスペアサマリーは「どのフィルタがどのペアで利益をブロックしているか」マトリクス表示:

```
| フィルタ    | USDJPY | EURJPY | GBPJPY | AUDJPY | CADJPY | CHFJPY |
|------------|--------|--------|--------|--------|--------|--------|
| Penalty    | +445K  | +200K  | ?      | ?      | ?      | ?      |
| BCA        | -200K  | -150K  | ?      | ?      | ?      | ?      |
```

## Phase 4: ペア別パラメータ最適化（1ペアずつ）

Phase 3の分析結果に基づき、各ペアで「当たりのあるパラメータ」のみテスト。

**テスト順序**: EURJPY → GBPJPY → AUDJPY → CADJPY → CHFJPY
（EURJPYは既にWhat-Ifデータあり + 最も利益の大きいペアなので最優先）

**各ペアの作業フロー**:
1. What-If分析レポートから改善候補を特定（Phase 3B結果）
2. 候補パラメータのバックテストジョブを投入（3-5件/ペア）
3. 結果比較 → 採用判定
4. `config/symbol_presets.yaml` 更新
5. 次のペアへ

**ベースライン比較元**: `087-092_AN-{SYMBOL}-6Y/result.json`

## Phase 5: ポートフォリオ検証 + リスク調整

全ペアの個別最適化完了後:

1. **マルチペア統合テスト**: 6JPYポートフォリオで2020-2025検証
2. **リスク調整**: 利益改善分をリスク引き下げに充てる
   - `risk_pct` を段階的に下げてDD vs 利益カーブを測定
   - 目標: 同等利益水準でDD改善

---

## 対象ファイル

| ファイル | 変更内容 |
|----------|----------|
| `scripts/analyze_whatif.py` | 新規: What-If分析スクリプト |
| `config/symbol_presets.yaml` | Phase 4/5で更新 |
| `reports/whatif_analysis_*.md` | 出力: 分析レポート |

## 検証方法

- Phase 1: スクリプト出力のレポートが妥当な統計を含むこと
- Phase 2-4: 各バックテスト結果を採用基準で判定
- Phase 5: マルチペアDD < 7%、月間プラス率 > 95%

## 現在の進捗

- [x] USDJPY What-Ifデータ取得済み（093_AN-USDJPY-WI）
- [x] EURJPY What-Ifデータ取得済み（094_AN-EURJPY-WI）
- [ ] **Phase 1A**: 分析スクリプト作成 ← 次のステップ
- [ ] Phase 1B: USDJPY分析実行
- [ ] Phase 2: USDJPY追加検証（必要な場合）
- [ ] Phase 3A: 残り4ペアWhat-If収集
- [ ] Phase 3B: 全ペア分析
- [ ] Phase 4: ペア別最適化
- [ ] Phase 5: ポートフォリオ検証 + リスク調整
