# ポジション管理改善検証計画

## Context

リアルトレード監視で以下の問題が観測されている：

1. **瞬間的なSL被弾** — エントリー時のコンセンサスは良好だが、一瞬の逆方向動きでSLに到達
2. **コンセンサス反転後の利確遅延** — コンセンサスが逆方向に転換しているのに保有継続
3. **含み益の蒸発** — 2000円含み益 → 200円（大幅な利益放棄）

検証範囲：USDJPY 2023-2025（まず単一ペアで検証 → 有望案で8ペア拡大）

---

## BTの髭対応（確認済み）

✅ **実装済み** — `autotrader/backtest/simulator.py` の `_check_intrabar_sl_tp()`（L754-828）でOHLCのhigh/lowを使用。

```
SL判定: candle.low <= sl  （足の最安値で判定）
TP判定: candle.high >= tp （足の最高値で判定）
ギャップ約定: openがSL/TP超えの場合はopenで約定
```

**制限**: 同一足でSLとTPの両方に達した場合はSLが優先（高値/安値の発生順序不明）。M15以上では実用上問題なし。

---

## Phase 1: ExitReason分析（リアルトレードログ）

### ログの場所と構造

| ファイル | 用途 |
|---------|------|
| `data/autotrader.db` | トレード記録（SQLite）|
| `data/local_state.db` | ポジション状態（OPEN中のみ、CLOSE時削除）|

**TradeRecordの主要フィールド**（`autotrader/adapters/database/models.py`）:
- `exit_reason` — 決済理由（SL_HIT / TP_HIT / TRAILING_STOP / STAGNATION / TIME_EXIT / SIGNAL_REVERSAL 等）
- `profit_loss` — 確定損益（円）
- `profit_loss_pips` — 確定損益（pips）
- `stop_loss` — 最終SL（BE移動後の値）
- `closed_at` — 決済時刻

### MFEの制約

⚠️ **リアルトレード側にMFE/MAEは未記録**（TradeRecordにカラムなし）。
ポジション状態（`highest_price`, `highest_r`）はOPEN中はSQLiteに保持されるが、CLOSE時に削除される。

→ 「2000円→200円」の定量分析はExitReasonと確定損益の相関で間接的に判断する。

### 分析クエリ（実行する）

```sql
-- ExitReason別の確定損益分布
SELECT
  exit_reason,
  COUNT(*) as count,
  AVG(profit_loss) as avg_pnl,
  MIN(profit_loss) as min_pnl,
  MAX(profit_loss) as max_pnl,
  SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as wins
FROM trade_records
WHERE closed_at IS NOT NULL
GROUP BY exit_reason
ORDER BY count DESC;

-- 利益が出ていたのに大きく逆戻りしたケースの推定
-- (SL_HIT/TRAILING_STOP で profit_loss が正だが小さいもの)
SELECT
  exit_reason,
  COUNT(*) as count,
  AVG(profit_loss) as avg_pnl
FROM trade_records
WHERE exit_reason IN ('TRAILING_STOP', 'STOP_LOSS', 'BREAKEVEN')
  AND profit_loss < 100  -- 小さな利益・損失
GROUP BY exit_reason;
```

---

## Phase 2: 問題別の改善案

### 問題1: 瞬間的なSL被弾

**原因候補**:
- 初期SLがATRに対して近すぎる
- または、エントリータイミングのノイズ

**ExitReasonで確認**: `SL_HIT` の確定損益分布 → MAE（含み損の深さ）が不明なため、
「深い逆行でSL到達」か「浅い逆行でSL到達」かの区別がBT分析で必要。

**改善候補（BTで検証）**:
- `trailing_start_r = 0.5 → 0.3`（より早くトレーリング開始でSL引き上げ）
- ただし、エントリー精度の問題の場合は効果薄

### 問題2: コンセンサス反転後の利確遅延

**現在の設定**（デフォルト）:
- `consensus_exit_threshold = 6.0`（逆方向スコア）
- `consensus_exit_own_max = 3.0`（自方向スコア上限）

**問題点**: 逆方向6.0以上 AND 自方向3.0以下の厳格な条件が必要。緩やかな転換では不発。

**改善案C**:
```json
"pm": {
  "consensus_exit_threshold": 5.0,
  "consensus_exit_own_max": 4.0
}
```

### 問題3: 含み益の蒸発（最重要）

**現在の保護機構の状態**:

| 機能 | デフォルト | 問題点 |
|-----|-----------|--------|
| `profit_reversal_enabled` | **False（無効）** | MFE→反落でのガードがない |
| `early_profit_guard_max_r` | **0.30** | 0.3R以上の大きな利益で無効化 |
| Stage2トレーリング開始 | **1.5R** | 1.5R未満は2.0x ATRの広いSL |

→ 大きな含み益（0.5R以上）を持つポジションの保護が実質的に機能していない。

**改善案A: profit_reversal有効化**
```json
"pm": {
  "profit_reversal_enabled": true,
  "profit_reversal_mfe_r": 0.3,
  "profit_reversal_drop_r": 0.20
}
```
MFEが0.3R到達後、0.20R以上の反落で即撤退。

**改善案B: early_profit_guard上限引き上げ**
```json
"pm": {
  "early_profit_guard_max_r": 1.5,
  "early_profit_guard_min_mfe_r": 0.10,
  "early_profit_guard_score_diff": 1.5
}
```
0.10R〜1.5Rの利益域でスコア差によるガードを有効化。

**改善案D: Stage2トレーリングの早期化**
```json
"pm": {
  "trailing_stage2_r": 1.0,
  "trailing_stage2_atr_multiplier": 1.0
}
```
1.0R到達からATR×1.0の引き締めトレーリングに移行。

**改善案E: 組み合わせ（推奨候補）**
```json
"pm": {
  "profit_reversal_enabled": true,
  "profit_reversal_mfe_r": 0.3,
  "profit_reversal_drop_r": 0.20,
  "early_profit_guard_max_r": 1.5,
  "consensus_exit_threshold": 5.5,
  "consensus_exit_own_max": 3.5
}
```

---

## Phase 3: BTキュー検証計画

### ジョブ構成（USDJPY 2023-2025）

```json
{
  "jobs": [
    {
      "id": "baseline",
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "ベースライン（overridesなし＝リアル同等設定）"
    },
    {
      "id": "case-A",
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "案A: profit_reversal有効化",
      "overrides": {
        "pm": {
          "profit_reversal_enabled": true,
          "profit_reversal_mfe_r": 0.3,
          "profit_reversal_drop_r": 0.20
        }
      }
    },
    {
      "id": "case-B",
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "案B: early_profit_guard上限引き上げ",
      "overrides": {
        "pm": {
          "early_profit_guard_max_r": 1.5,
          "early_profit_guard_min_mfe_r": 0.10,
          "early_profit_guard_score_diff": 1.5
        }
      }
    },
    {
      "id": "case-C",
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "案C: consensus_exit閾値緩和",
      "overrides": {
        "pm": {
          "consensus_exit_threshold": 5.0,
          "consensus_exit_own_max": 4.0
        }
      }
    },
    {
      "id": "case-D",
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "案D: Stage2トレーリング早期化",
      "overrides": {
        "pm": {
          "trailing_stage2_r": 1.0,
          "trailing_stage2_atr_multiplier": 1.0
        }
      }
    },
    {
      "id": "case-E",
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "案E: 組み合わせ",
      "overrides": {
        "pm": {
          "profit_reversal_enabled": true,
          "profit_reversal_mfe_r": 0.3,
          "profit_reversal_drop_r": 0.20,
          "early_profit_guard_max_r": 1.5,
          "consensus_exit_threshold": 5.5,
          "consensus_exit_own_max": 3.5
        }
      }
    }
  ]
}
```

### 判断基準

| 指標 | 維持ライン | 改善確認 |
|------|----------|---------|
| PF | 3.0以上 | +0.2以上 |
| 最大DD | 2.0%以内 | 縮小 |
| 平均利益/平均損失 | 維持 | 向上 |
| Sharpe | 5.0以上 | — |

---

## 追加検討: リアルトレードMFE記録の改修

「2000円→200円」の蒸発を将来的に定量追跡するために：

**実装候補**:
- `TradeRecord`に`mfe_pips`/`mae_pips`/`mfe_r`カラムを追加
- `LiveTradingEngine._write_close_to_db()`で`ManagedPosition.highest_price`から計算して記録

**優先度**: 低（今回のBT検証後、改善策が確定してから実装）

---

## 作業手順

1. **ログ分析** — `data/autotrader.db`のExitReason分布を確認（SQLクエリ実行）
2. **原因特定** — どのExitReasonが問題か絞り込み → 検証する案を決定
3. **BTキュー投入** — 上記ジョブをキューランナーに投入
4. **結果比較** — `backtest/results/` の出力を比較
5. **採用案をPRで反映** — worktree + PRでPositionManagerConfigのデフォルト値を更新

---

## 関連ファイル

| ファイル | 役割 |
|---------|------|
| `autotrader/decision/unified/risk/position_manager.py` | PositionManagerConfig・エグジットロジック |
| `autotrader/backtest/simulator.py` | SL/TP判定（L680-965）|
| `autotrader/adapters/database/models.py` | TradeRecord（ExitReason記録）|
| `data/autotrader.db` | リアルトレード履歴 |
| `D:\Projects\AutoTraderV4_data\state\backtest_queue.json` | BTキュー投入先 |
