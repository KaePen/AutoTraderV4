# ポジション管理 総合検討レポート（2026-03-25）

対象: USDJPY BT（2023-2025）×5案 + リアルトレード（GBPJPY 2026-03-24）

---

## 1. BT結果サマリー

| ケース | PF | Sharpe | DD% | WR% | 取引数 | 純損益 |
|-------|-----|--------|-----|-----|-------|-------|
| **baseline** | **5.91** | **4.16** | 1.40 | 90.4 | 291 | 510,887 |
| case-A profit_reversal | 5.82 | 3.64 | **1.27** | 80.5 | 277 | 397,262 |
| case-B early_guard | 5.91 | 4.16 | 1.40 | 90.4 | 291 | 510,887 |
| case-C consensus緩和 | 5.91 | 4.16 | 1.40 | 90.4 | 291 | 510,887 |
| case-D Stage2早期化 | 5.89 | **4.23** | 1.54 | 90.4 | 293 | 506,654 |
| case-E 組み合わせ | 5.82 | 3.64 | **1.27** | 80.5 | 277 | 397,262 |

**結論: USDJPYにおいてはベースラインが最良。改善案はいずれも純損益を悪化させる。**

---

## 2. ExitReason別分析（baseline）

| ExitReason | 件数 | 平均PnL | avg MFE | avg MAE | 保有中央値 |
|-----------|------|--------|---------|---------|---------|
| SL_HIT | 187件 | +1,416円 | 0.80R | -0.40R | 47分 |
| TP_HIT | 36件 | +6,482円 | 1.59R | -0.30R | 48分 |
| TP_1R（部分利確） | 66件 | +230円 | 1.12R | -0.30R | 44分 |
| STAGNATION | 2件 | -1,200円 | 0.12R | -0.49R | 120分 |

### 重要な発見: SL_HIT の87%はプラス決済

```
SL_HITの構成:
  プラス決済: 160件 (86%) ← BEに移動したSLで決済
  マイナス決済:  27件 (14%) ← 初期SL（BE未到達）で被弾
  avg PnL: +1,416円
  うち MFE>0.5R: 168件 (90%) → avg_mfe=0.85R → avg_pnl=+2,084円
```

**BTの「SL_HIT」≠ リアルの「SL_HIT」**

| | BT (USDJPY) | リアル (GBPJPY 3/24) |
|---|---|---|
| SL_HIT件数 | 187件 | 3件 |
| avg PnL | **+1,416円** | **-2,675円** |
| avg MFE | 0.80R（BE到達後に逆行） | ≈0R（エントリー直後に逆行）|
| パターン | エントリー→0.8R到達→BE移動→反転→SL | エントリー→即逆行→初期SL |

---

## 3. 各改善案が効かなかった理由

### case-B（early_profit_guard上限引き上げ）/ case-C（consensus_exit緩和）→ 完全に無効

USDJPYのBTでは `early_profit_guard` も `consensus_exit` の条件（スコア差）をほぼ満たさないため発動回数が変わらない。パラメータ変更の影響がゼロ。

### case-A（profit_reversal有効化）→ 純損益 -22%

```
変化の内訳:
  SL_HIT: 187件 → 134件 (-53件)  ← 早期撤退で逃れた
  STAGNATION: 2件 → 58件 (+56件) ← profit_reversalで早期決済
  純損益: 510,887 → 397,262 (-113,625円)
  WR: 90.4% → 80.5% (-9.9%)
```

profit_reversalは0.3R到達後の0.2R反落で即撤退させるため、USDJPYでは「0.5R付近の一時的押し目で撤退」するケースが多く発生。結果として利益トレードを早期に切ってしまう。

STAGNATIONのavg PnLは -1,200円 → -179円に改善しているが、件数が56件増えて全体の利益を圧迫。

### case-D（Stage2早期化）→ TP_HIT が激減

```
TP_HIT: 36件 → 22件 (-14件)
SL_HIT: 187件 → 203件 (+16件)
```

Stage2（ATR×1.0）への早期移行で、TP（2-3R先）まで到達する前にSLがあまりにも引き締まりすぎて決済されてしまう。

---

## 4. 「2000円→200円」の正体（BT解明）

BTのデータから、「大きな含み益がほぼ消える」パターンの正体は：

```
1. エントリー
2. 1R到達（例: GBPJPY 20pips → 約2,000円の含み益）
   → 部分利確 5%（100円確定）
   → SLをBEに移動（entry+5pipsのクッション付き）
   → TP無効化（disable_tp_after_partial=True）
3. 価格が反転・BE付近に戻る
   → SL（BEの5pips上）で決済 → 確定 約100〜300円
4. 「2,000円あった含み益が200円になった」
```

これは**バグではなく設計通りの動作**。ただし、ユーザー体験としては「損をした感覚」になる。

### 改善の方向性

**現在の設定:**
```
partial_close_1r_ratio = 0.05 (5%のみ確定)
be_cushion_pips = 3.0
disable_tp_after_partial = True
```

**改善候補:**
1. `partial_close_1r_ratio = 0.30` に引き上げ → 1R到達時に30%確定（「2000円→600円確定+残り継続」）
2. `be_cushion_pips = 5.0` → 保護バッファを拡大
3. `disable_tp_after_partial = False` → TP継続有効（ただしTPまで到達しやすい設定との組み合わせが必要）

---

## 5. リアルトレード特有の問題: GBPJPY 欧州オープン

BTの分析はUSDJPYが対象。リアルで発生した問題はGBPJPYのBUYに特化しており、構造が異なる。

### 問題の特性
- 3/24 08:06〜08:50（欧州オープン直後）に2回連続SL被弾
- 12:42〜13:08（ロンドンランチ後）に1回SL被弾
- 全て初期SL（20pips）で被弾（BEに到達する前）
- SL被弾後すぐに同方向で再エントリー

### EdgeValidatorの動作確認

lot変化から推測されるEdgeValidatorの動作：
```
SL_HIT [1]: 0.14lot
SL_HIT [2]: 0.13lot (-7%)   ← WARNING段階（lot削減）の可能性
EXTERNAL:   0.11lot (-21%)  ← WARNING段階（lot削減）
SL_HIT [3]: 0.13lot         ← 別時間帯のため回復？
EXTERNAL:   0.12lot
```

→ EdgeValidatorのWARNINGが一部機能してlot削減はしているが、エントリー自体は止まらなかった。

### 仮説：欧州オープン時間帯のボラティリティ問題

GBPJPYのM15での欧州オープン前後は1本のキャンドルが20pips以上動くことがある。
初期SL幅20pipsでは「ノイズ」と「本物の方向性」を区別できない時間帯がある。

---

## 6. 総合的な対応方針

### 短期（すぐ実施）

| 優先 | 内容 | 方法 |
|------|------|------|
| ★★★ | **GBPJPYのBTを実施**（2023-2025、baseline vs 各案） | BTキュー投入 |
| ★★★ | **partial_close_1r_ratioの引き上げ検証**（5%→20-30%） | BTキュー投入 |
| ★★ | **欧州オープン時間帯フィルター検討**（08:00-09:00 GMT） | BTで効果確認 |

### 中期（BTで効果確認後）

| 内容 | 期待効果 |
|------|---------|
| partial_close_1r_ratio引き上げ | 「1R到達後の含み益保護」を確定利益で実現 |
| 欧州オープンフィルター | GBPJPY朝のSL連続被弾を削減 |
| GBPJPYのSL幅調整（20→25pips） | 初期SL被弾率の削減 |

### 採用しない案

| 案 | 理由 |
|----|------|
| profit_reversal有効化 | USDJPYで純損益-22%、WR-10% |
| early_profit_guard上限引き上げ | USDJPY BTで効果ゼロ |
| consensus_exit閾値緩和 | USDJPY BTで効果ゼロ |
| Stage2トレーリング早期化 | TP_HIT激減、全体PF低下 |

---

## 7. 次に投入するBTキュー案

### GBPJPY 基礎検証

```json
{
  "jobs": [
    {
      "id": "gbpjpy-baseline",
      "symbol": "GBPJPY",
      "years": "2023-2025",
      "description": "GBPJPYベースライン"
    },
    {
      "id": "gbpjpy-partial30",
      "symbol": "GBPJPY",
      "years": "2023-2025",
      "description": "GBPJPY 1R部分利確30%",
      "overrides": {
        "pm": {
          "partial_close_1r_ratio": 0.30
        }
      }
    },
    {
      "id": "usdjpy-partial30",
      "symbol": "USDJPY",
      "years": "2023-2025",
      "description": "USDJPY 1R部分利確30%（比較用）",
      "overrides": {
        "pm": {
          "partial_close_1r_ratio": 0.30
        }
      }
    }
  ]
}
```
