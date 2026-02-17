# 要旨

提示された現行フローはマルチ時間足（multi-timeframe; MTF）情報を計算している一方で、**(i) 取引の保有期間（スキャルピング／デイトレード／スイング）を内生的に選択する戦術決定レイヤ**と、**(ii) 戦術および不確実性（confidence・ボラティリティ・DD状態等）に応じてリスク量（ポジション量）を連続的に配分するポジション・サイジング機構**が欠落している。この欠落は、期待収益の最大化だけでなく、リスク制御（分散・尾部損失・ドローダウン）を同時に満たす上で構造的な制約となる。以下では、設計上の不整合点を同定したうえで、追加・統合・削除の指針を、情報フローの挿入位置に即して再構成する。

---

# 1. 現行フローの主要な設計問題（系統的洗い出し）

対象: 提示フロー（IndicatorCalculator → RiskManager → TimeframeEvaluator → ADX → Consolidate → HTF整合 → TP/SL比 → TradeSimulator）

## 1.1 戦術（保有期間）の内生化が欠落

- 出力に `primary_tf` が含まれるにもかかわらず、**当該 **``** を決定する推論過程がフロー内に存在しない**。
- M5を主軸に上位足を参照する設計は示唆されるが、参照の役割が「エントリー時刻の微調整」に局在し、**保有期間に整合する利得抽出（profit extraction）戦略の選択**に結び付いていない。
- 結果として、短期・中期の意思決定が混在し、**時間スケール不整合（time-scale mismatch）によるシグナル劣化**を誘発しうる。

## 1.2 ポジション量（ロット）決定の不在

- `RiskManager.can_trade()` は、日次損失上限やクールダウン等の\*\*ゲーティング（取引可否判定）\*\*に偏っており、 **「どれだけ賭けるか」**（position sizing）を規定しない。
- ATR等からSL距離（pips）が算定されていても、 **許容損失（%・通貨額・pips）→数量（lot）への写像**が存在しないため、リスク量が戦略・相場状況に追随しない。

## 1.3 フィルタが固定でレジーム適応性に乏しい

- ADX（例: H1/H4 ≥ 25）の固定閾値は、トレンド相場を前提とする一方で、 レンジ相場で有効な戦術（mean reversion等）を過度に排除しうる。
- TP/SL比率の固定は、ボラティリティ、トレンド強度、レンジ度合いにより最適化されるべきパラメタの**静的化**であり、 期待値（EV）の相場依存性を無視する。

## 1.4 上位足整合の二重適用による過剰抑制

- `TimeframeEvaluator` 内の整合ボーナスと、別ステップのHTF整合チェックが併存し、 同一仮説（HTF整合が望ましい）の**重複評価**となっている。
- これはエントリー頻度の低下・サンプル偏り・過学習（overfitting）を助長しうる。

## 1.5 SL/TP統合則の同定不能性（identifiability）

- 「最大SLと最大TPを採用」という統合則は保守的に見えるが、 **どの時間足由来の意思決定かが曖昧**となり、R倍分布・勝率推定の解釈可能性を損なう。
- 例えば、TPだけが上位足由来で肥大し、SLは短期由来で過大になる等、 戦術と整合しないリスクリワードが生成され得る。

## 1.6 保有中の管理（position management）が設計に含まれない

- エグジットがSL/TP到達や強制決済に限定され、 Time exit（最大保有）、トレーリング、建値移動、部分利確、シグナル弱化時撤退等が戦術として定義されていない。
- これは期待値の下方バイアスとドローダウン増大に直結しやすい。

## 1.7 バックテスト実装上の潜在的欠陥

- MTF処理における「確定足のみで判断」「時間足整列（同期）」が暗黙で、仕様として明示されない。
- pip換算が固定（例: `/100`）の前提に見え、通貨ペア・小数桁変更で破綻しうる。

---

# 2. 追加・統合・削除の設計方針（挿入位置ベース）

以下では、あなたが重視する2点（保有期間判断・ロット可変）を核に、機能の追加・統合・削除を定義する。

## 2.1 追加: MarketRegimeDetector（相場レジーム推定）

**挿入位置:** IndicatorCalculator 直後

- 入力: 各TFのATR、ADX、トレンド（SMA傾き等）、レンジ指標（例: BB幅、戻り率分散）
- 出力: `regime ∈ {TREND_STRONG, TREND_WEAK, RANGE, HIGH_VOL, …}`
- 目的: 後段の戦術選択、参照時間足集合、フィルタ閾値、TP/SL設計を\*\*条件付き（conditional）\*\*に切り替える。

## 2.2 追加: HoldingPeriod / PrimaryTF Selector（戦術・主要時間足の内生化）

**挿入位置:** RegimeDetector直後（原則としてRiskManagerより前）

- 入力: `regime` と MTFシグナルの要約統計（方向一致度、HTFの明確度、直近ボラ等）
- 出力: `plan = {mode, primary_tf, entry_tf, manage_tf, max_holding_bars}`
  - 例（概念）:
    - スキャ: primary=M5、entry=1M/5M、manage=15M、max\_hold=30〜90分
    - デイトレ: primary=15M/1H、entry=5M/15M、manage=1H、max\_hold=当日
    - スイング: primary=4H/D1、entry=1H/4H、manage=4H/D1、max\_hold=数日

## 2.3 追加: PositionSizer（リスク量→数量への写像）

**挿入位置:** SignalConsolidator（最終シグナル確定）直後

- 入力: `equity`（バックテストでは仮想）、`risk_per_trade_pct`、`SL_pips`、`confidence/regime/連敗/DD` 等
- 出力: `lot`（または数量）
- 最小実装（まずは単純で良い）:
  - `risk_budget = equity × risk_per_trade_pct`
  - `risk_adjust = clamp(0.5〜1.5, f(confidence, regime, dd_state))`
  - `lot = (risk_budget × risk_adjust) / (SL_pips × pip_value_per_lot)`

## 2.4 追加: 制約機の分離（Entry / In-Position）

**挿入位置:**

- EntryConstraintEngine: PositionSizerの直前（推奨）

- InPositionConstraintEngine: PositionManager内部

- 目的: 「計算機→制約機→判定機」および「保有中はトレード機能が制約参照」という既定方針を、情報フローとして整合させる。

## 2.5 追加: PositionManager（保有中の戦術）

**挿入位置:** TradeExecutionを包含する形で中核化（TradeSimulatorの置換または内包）

- 機能（最低限）:
  - Time exit（mode依存）
  - 建値移動（例: 1R到達）
  - トレーリング（ATRまたは構造ベース）
  - 部分利確（例: 1Rで一部、残りは追随）
  - manage\_tfの弱化／反転による撤退

---

# 3. 追加要求の反映（データ入力層／コンセンサス／プリセット削除）

## 3.1 データ入力層: 1M〜1Dを「必要に応じて参照」へ

### 問題

- 固定の時間足集合を前提とすると、戦術や相場に応じた情報参照の選択が困難で拡張性が低い。

### 改善: TimeframeRouter（動的参照時間足選択）

**挿入位置:** RegimeDetector直後〜Selector内部

- 入力: 利用可能TF（1M〜1D）、`regime`、`mode`
- 出力: `tf_set = {entry_tf, primary_tf, confirm_tfs[], manage_tf}`

#### 代表的設定（例）

- スキャ: entry=1M/5M、primary=5M、confirm=15M（必要なら1Hは大局方向のみ）、manage=15M
- デイトレ: entry=5M/15M、primary=15M/1H、confirm=1H/4H、manage=1H
- スイング: entry=1H/4H、primary=4H/D1、confirm=D1、manage=4H/D1

> 設計要点: 「全TFを常時計算」ではなく、**1M等の基礎データから必要なTFバーを生成・抽出し、必要な指標のみ計算する**設計へ寄せる（計算資源と因果整合性の両面で有利）。

## 3.2 コンセンサス（シグナル統合）: 単一規範へ整理し、modeに従属させる

### 問題

- MAJORITYは対象TF集合の設計が本質であり、スキャでD1/H4を含めると時間スケール不整合を引き起こしやすい。
- WEIGHTEDは調整自由度が高く、説明可能性を損ね、過学習リスクを増やす。

### 方針

- `ALL / MAJORITY / WEIGHTED` を廃止し、**単一の統合規範**に収束させる。
- 統合規範は、Selector/Routerが返す `tf_set` に従属し、**戦術ごとに参照TFが変わること**を設計で保証する。

### 置換: ModeAwareScoreConsensus（閾値付きスコア合算）

**置換対象:** `consensus_rule` パラメタ群を撤去し、本方式へ一本化。

- 手順:

  1. Router/Selectorで `confirm_tfs` を確定
  2. 各TFの出力を `{-1, 0, +1}`（SELL/HOLD/BUY）に正規化
  3. 重みは「固定プリセット」ではなく **modeにより自動付与**（例: primary=3, entry=2, confirm=1, それ以外=0〜1）
  4. 合計スコア `S` に対し閾値 `T(mode, regime)` を適用
     - `S ≥ +T` → BUY、`S ≤ -T` → SELL、その他 → HOLD

- mode別の直観（例）:

  - スキャ: 1M/5M/15M中心、1Hは「逆向き禁止」程度の弱制約に限定
  - デイトレ: 5M/15M/1H/4H
  - スイング: 1H/4H/D1

> 効果: 「多数決の母集団問題」を構造的に解消し、WEIGHTEDの曖昧さを、mode依存の自動規則へ吸収する。

## 3.3 プリセット機能: 削除し、内部の自動計画（plan）へ移譲

- ユーザー手動切替を前提とするプリセットは削除する。
- 代替として、Selectorが生成する `plan(mode, tf_set, risk_profile)` を唯一の実行計画とみなし、 ボット内部で `regime`、直近ボラ、ドローダウン状態、連敗等に応じて、modeや `risk_adjust` を連続的に微調整する。

---

# 4. 既存ステップの統合／削除方針（冗長性の除去）

## 4.1 ADX単独フィルタの廃止（RegimeDetectorへ統合）

- ADXはレジーム推定の一要素として吸収し、
  - トレンド戦術では閾値を上げる
  - レンジ戦術では低ADXを許容する といった**条件付き閾値**へ再設計する。

## 4.2 HTF整合の二重判定を解消（Selector/Consensusへ集約）

- HTF整合はSelectorとConsensusに集約する。
  - スイングでは必須制約
  - スキャでは大局方向の弱制約（あるいは無視）

## 4.3 SL/TP統合則の再定義

- 基本は `primary_tf`（または `manage_tf`）由来のSL/TPを採用し、 entry\_tfはエントリー最適化（微調整）として扱う。
- pipsの上下限（cap）で異常値を抑制し、同定可能性と再現性を担保する。

## 4.4 TP/SL比率の静的固定を撤廃（mode/regime依存へ）

- 目安:
  - スキャ: 1.0〜1.8
  - デイトレ: 1.5〜2.5
  - スイング: 2.0〜4.0
- 初期実装は離散プリセット値でも良いが、適用は `plan` の内部で自動選択されるべきである。

---

# 5. 改訂版フロー（統合案）

以下の構成により、**戦術（保有期間）→リスク量（lot）→執行→保有中管理**が一貫した因果鎖として接続される。

```text
【データ入力】(1M〜1D; 必要TFを動的選択)
        │
        ▼
IndicatorCalculator（必要TFのみ計算）
        │
        ▼
MarketRegimeDetector（相場レジーム推定）
        │
        ▼
TimeframeRouter（entry/primary/confirm/manage TFの動的選択）
        │
        ▼
HoldingPeriod / PrimaryTF Selector（modeとplanの決定）
        │
        ▼
RiskManager.can_trade()（日次損失・DD・クールダウン等のゲーティング）
        │
        ▼
TimeframeEvaluator.evaluate()（planに従い必要TFのみ評価）
        │
        ▼
ModeAwareScoreConsensus（単一規範によるシグナル統合）
        │
        ├── NG → HOLD（理由付与）
        │
        ▼
EntryConstraintEngine（エントリー制約）
        │
        ▼
PositionSizer（risk_budget ÷ SL距離 → lot）
        │
        ▼
TradeExecution（約定・スプレッド反映）
        │
        ▼
PositionManager（TimeExit/Trail/部分利確/撤退）
        │
        ▼
結果ログ・集計（勝率、EV、R倍分布、DD、分位点等）
```

---

# 6. 最小実装セット（実装優先度に整合）

分析・トレード・バックテストを最優先とする前提で、効果が大きく依存関係が少ない順に示す。

1. **PositionSizer（数量決定）**: 同一勝率でも資本曲線の形状を大きく変える。

2. **Selector + Router（modeと参照TFの内生化）**: 時間スケール不整合を根治し、戦術間の混線を防ぐ。

3. **PositionManagerのTime exit + 建値移動**: 期待値の底上げとDD抑制に寄与しやすい。

4. **RegimeDetectorへのADX統合 + Consensus一本化**: フィルタ冗長性と過学習余地を削減する。

---

# 7. 要求事項との対応関係（トレーサビリティ）

- 保有期間（スキャ／中期等）の判断: **Selector（mode・primary\_tf・max\_holding） + Router（参照TF集合）**
- リスクレベルに応じたロット可変: **PositionSizer（risk\_budget→lot） + risk\_adjust（DD/連敗/信頼度）**
- データ入力の柔軟化（1M〜1D）: **TimeframeRouter（動的選択） + 必要TFのみ計算**
- コンセンサスの整理: **ModeAwareScoreConsensus（単一規範）**
- プリセット削除: **手動プリセット撤去、planの内部自動切替へ移譲**

必要であれば、次段として「mode別集計（戦術別の勝率・EV・DD）」「regime別性能（条件付き期待値）」をバックテスト出力に含めることで、改善ループの収束速度を高められる。

