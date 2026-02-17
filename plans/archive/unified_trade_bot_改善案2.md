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

## 3.1 データ入力層: 1M〜1Dを「並列戦略評価のため常時計算」へ

### 背景（意図の明確化）
- 目的は「必要なTFだけを動的に選ぶ」ことではなく、
  **スキャルピング／短中期／スイング等の複数の“保有期間戦略”を同時に候補化**し、
  各時点で最も有利な戦略を採択して執行する点にある。
- そのため、上位足の将来的方向性（大局観）を含む情報は引き続き有効であり、
  **1Mに限定せず1M〜1DのMTF状態を常時更新**する設計が必要となる。

### 改善案（置換）: MTFStateStore（常時計算） + StrategyPool（並列評価）

**置換対象:** 「必要TFのみ計算」「TimeframeRouter中心」の記述を、以下へ置換する。

#### MTFStateStore（常時計算・確定足更新）
- 入力: 1Mティック/バー（または最小粒度の時系列）
- 出力: 各TF（1M/5M/15M/1H/4H/1D…）の
  - 集約バー状態（確定/未確定の区別）
  - 指標の逐次状態（EMA/ATR/ADX等）
  - シグナルの確定値（TFがクローズした瞬間のみ更新）

> 設計上の要諦は、上位足を「読み込む」か否かではなく、
> **上位足の“確定足ベース”の状態を常時維持し、将来情報混入を排除しつつ参照可能にする**点にある。

#### StrategyPool（戦略ごとの参照TFセットを固定して並列評価）
- 戦略は「参照TFセット」を内包した評価器として定義する（例）:
  - ScalpStrategy: {1M, 5M, 15M} +（弱い大局制約として）{1H}
  - ShortMidStrategy: {15M, 1H, 4H}
  - SwingStrategy: {1H, 4H, 1D}
- 各戦略は毎分（またはイベント）で共通フォーマットの提案を返す:
  - `signal`（BUY/SELL/HOLD）
  - `SL/TP`（当該戦略の時間スケールに整合する設計値）
  - `confidence`
  - `edge_score`（期待値の代理スコア）
  - `risk_flags`（スプレッド拡大・高ボラ・イベント等の危険度）

> これにより、「スキャが有効な局面」と「15Mベースの短中期が有効な局面」を同一時系列上で競合評価できる。


## 3.2 コンセンサス（シグナル統合）: “戦略内統合”へ閉じ、戦略間はメタ選択で裁く

### 問題の再定義
- 従来のコンセンサスは「同一シグナルを、どのTF集合で合意形成するか」を単一規範で扱っていた。
- しかし、あなたの意図する方式では、
  - スキャ用のTF集合と
  - 15Mベース短中期用のTF集合と
  - スイング用のTF集合
  が本質的に異なる。
- よって、**コンセンサスの母集団（対象TF集合）を戦略単位で固定**し、
  **戦略同士の競合はメタ層で解決**するのが設計として自然である。

### 改善方針
- `ALL / MAJORITY / WEIGHTED` といった“グローバルな統合規則”を廃止する。
- 代わりに、
  1) 各戦略が「自分の参照TF集合」で統合（戦略内統合）し、
  2) StrategySelectorが最終的に“採用戦略”を決定（戦略間選択）
  という二層構造に分離する。

### 戦略内統合: InStrategyConsensus（単一・簡潔な規範）
- 各戦略の参照TF集合に対し、以下の単一規範で統合する:
  - TFごとの出力を `{-1,0,+1}` に正規化
  - 戦略内の役割（entry/primary/confirm）に応じて定数重みを付与（戦略内でのみ完結）
  - 合計スコア `S` を閾値 `T_strategy` で判定（BUY/SELL/HOLD）

> 重要なのは、重み付けの自由度を無制限にせず、
> **戦略ごとに固定された少数パラメタに留め、説明可能性と過学習耐性を確保する**点である。

### 戦略間選択: StrategySelector（メタ層）
- 入力: StrategyPoolの提案（signal, SL/TP, confidence, edge_score, risk_flags）
- 出力: 採用戦略と実行オーダ（またはHOLD）
- 典型ルール:
  - 危険フラグ（例: スプレッド拡大）で当該戦略を候補から除外
  - `edge_score` 最大を採択
  - 切替ヒステリシス（チャタリング防止）
  - 保有中は原則戦略固定（exitは当該戦略のPositionManagerが管理）


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

# 5. 改訂版フロー（統合案：並列戦略評価＋メタ選択）

以下の構成により、**MTF状態を常時維持しつつ、複数の保有期間戦略を並列に評価し、各時点で最適戦略を選択して執行**する因果鎖が明確化される。

```text
【データ入力】(1M〜1D; MTF状態を常時更新)
        │
        ▼
MTFStateStore（各TFの集約バー＋指標状態を逐次更新；確定足のみでシグナル更新）
        │
        ▼
MarketRegimeDetector（相場レジーム推定：TREND/RANGE/HIGH_VOL 等）
        │
        ▼
RiskManager.can_trade()（日次損失・DD・クールダウン等のゲーティング）
        │
        ▼
StrategyPool（保有期間別戦略を並列評価）
  ├─ ScalpStrategy（{1M,5M,15M}+弱い{1H}）
  ├─ ShortMidStrategy（{15M,1H,4H}）
  └─ SwingStrategy（{1H,4H,1D}）
        │
        ▼
StrategySelector（危険度除外＋edge_score最大＋切替ヒステリシス；保有中は原則固定）
        │
        ├── NG → HOLD（理由付与）
        │
        ▼
EntryConstraintEngine（エントリー制約）
        │
        ▼
PositionSizer（risk_budget ÷ SL距離 → lot；regime/連敗/DDで調整）
        │
        ▼
TradeExecution（約定・スプレッド反映）
        │
        ▼
PositionManager（採用戦略に紐付く管理：TimeExit/Trail/部分利確/撤退）
        │
        ▼
結果ログ・集計（勝率、EV、R倍分布、DD、戦略別・レジーム別分解）
```

---

# 6. Claude Code 受け渡し用：改修プラン（ファイルとして渡せる完全版）

本章は Claude Code にそのまま渡せるように、**目的・差分・新規追加・置換点・実装順・受入条件**を1つに統合した“改修パッケージ”である。

## 6.1 変更目的（What / Why）

### What
- `generate_signal()` を単線（単一モード前提）から、**輻輳型（並列戦略評価＋メタ選択）**へ移行する。
- 同一時点で
  - スキャ（1M周辺）
  - 短中期（15M周辺）
  - スイング（H1〜D1）
 という複数の保有期間戦略を候補化し、**edge_score を軸に最適戦略を採択**する。

### Why
- 既存方式では「単一時間軸の意思決定に寄り、局面適応が弱い」「HTF整合が過剰抑制になる」などの構造課題が残る。
- 輻輳型により、局面ごとに**有利な保有期間を自動選択**でき、取引機会と収益機会を増やしつつ、危険局面ではスキャを抑制できる。

---

## 6.2 現行資産の流用方針（Keep）

以下は原則流用し、破壊的変更を避ける。

- `MarketRegimeDetector`（レジーム推定）
- `TimeframeEvaluator`（TF別シグナル・SL計算）
- `PositionSizer`（lot算出）
- `RiskManager.can_trade`（日次損失・DD・クールダウンのゲート）

---

## 6.3 中心設計の置換点（Replace / Absorb）

### A. TradingModeSelector（単一モード選択）
- **置換**：全体で1回のモード選択を廃止し、戦略ごとに「固定 plan（保有期間）」を持つ。
- 戦略間の競合は StrategySelector が裁定する。

### B. TimeframeRouter（全体でのTF役割割当）
- **吸収**：Routerの役割は各 Strategy 内に内包する（参照TFセットと役割は戦略定義で固定）。

### C. ModeAwareScoreConsensus（全体コンセンサス）
- **移設**：コンセンサスは戦略内統合（InStrategyConsensus）へ移動。
- 戦略間は StrategySelector で裁定。

### D. HTF必須フィルタ（全体必須）
- **変更**：HTF整合は戦略別に強度可変。
  - スキャ：弱制約（逆向き禁止・減点）
  - 短中期：中制約（条件付きHOLD or 大きく減点）
  - スイング：強制約（failなら即HOLD）

---

## 6.4 新規追加モジュール（New）

### 1) 型定義（共通I/F）
- `src/autotrader/decision/unified/types.py`
  - `ProposedTrade`
    - `strategy_id: str`
    - `direction: BUY|SELL|HOLD`
    - `confidence: float`
    - `edge_score: float`
    - `sl_pips: float` / `tp_pips: float`
    - `primary_tf, entry_tf, confirm_tfs`
    - `risk_flags: dict[str,bool]`
    - `reason: str`
    - `debug_scores: dict`（buy/sellスコアなど）
  - `StrategyContext`
    - `timestamp`
    - `regime_result`
    - `spread/session/hour`
    - `open_position_info`（保有中判定に必要なら）

### 2) 戦略内統合
- `src/autotrader/decision/unified/in_strategy_consensus.py`
  - 入力：TFごとの評価結果（buy/sell/HOLDの正規化）＋役割重み
  - 出力：direction/confidence/score_detail
  - ※最初は ModeAwareScoreConsensus を参考に“縮退版”でよい

### 3) Strategy 抽象 + 具体実装
- `src/autotrader/decision/unified/strategies/base.py`
  - `BaseStrategy.evaluate(context, mtf_data) -> ProposedTrade | None`
- `.../strategies/scalp.py`
- `.../strategies/short_mid.py`
- `.../strategies/swing.py`

初期実装の戦略定義（例）
- ScalpStrategy: TF={1M,5M,15M} (+弱HTF=1H)
- ShortMidStrategy: TF={15M,1H,4H}
- SwingStrategy: TF={1H,4H,1D}

### 4) StrategyPool
- `src/autotrader/decision/unified/strategy_pool.py`
  - 登録戦略を順次評価し `List[ProposedTrade]` を返す

### 5) StrategySelector
- `src/autotrader/decision/unified/strategy_selector.py`
  - 危険度除外（risk_flags）
  - `edge_score` 最大を採択
  - 切替ヒステリシス（チャタリング防止）
  - 保有中は原則戦略固定（ポジションに strategy_id を紐付け）

---

## 6.5 edge_score（戦略間比較の基準）

最初から厳密期待値推定は不要。**一貫した proxy** を採用する。

推奨（初期）:

```text
edge_score =
    base_confidence
  × score_margin_factor
  × regime_fit_factor
  × cost_factor
  × htf_conflict_factor
```

- `base_confidence`：戦略内統合が出す 0〜1
- `score_margin_factor`：buy-sell差の正規化（0〜1）
- `regime_fit_factor`：レジーム適合（戦略ごとに簡単な係数表でよい）
- `cost_factor`：スプレッド/時間帯など（0.5〜1.0）
- `htf_conflict_factor`：HTF逆行の強さ（スキャは減点、スイングは失格に近い）

---

## 6.6 generate_signal() 改修方針（具体）

### 変更概要
- 既存の
  - mode_selector
  - router
  - mode_aware_consensus
  - HTF必須フィルタ
  を中心にした単線フローを、以下に差し替える。

### 新しい骨格
1. regime_detector
2. risk_manager.can_trade（ゲート）
3. strategy_pool.evaluate_all（候補生成）
4. strategy_selector.choose（採択）
5. entry_constraints
6. position_sizer
7. signal出力（chosen strategy情報を付与）

### 出力互換
- 既存 `ConsolidatedSignal` を拡張する場合は、
  - `chosen_strategy_id`
  - `edge_score`
  - `risk_flags`
  を追加し、未使用箇所はデフォルト値で互換を保つ。

---

## 6.7 実装順（安全な段階導入）

1) `types.py` の追加
2) `in_strategy_consensus.py` の追加
3) 3戦略（scalp/short_mid/swing）を最小で作成（既存 TimeframeEvaluator を呼ぶだけでも可）
4) `strategy_pool.py` を追加
5) `strategy_selector.py` を追加（risk_flags除外＋edge_score最大＋ヒステリシス）
6) `trade_bot.py` の `generate_signal()` を新骨格へ改修
7) Backtestログ拡張（strategy別/レジーム別の集計を推奨）

---

## 6.8 受入条件（最低限のDefinition of Done）

- バックテストが既存I/Fで完走する（例外なく signal が返る）
- ログに `chosen_strategy_id` が出る
- 同一時点で3戦略が評価され、edge_scoreで採択される
- HTF整合が戦略別に効く（スキャがD1/H4で即死しない）
- 保有中は戦略切替しない（ExitはPositionManagerが管理）

---

## 6.9 追加の推奨（後続改善のため）

- Backtest出力を「戦略別」「レジーム別」に分解し、切替の寄与を定量化
- SELL精度改善は、戦略別の閾値・HTF制約強度・edge_score補正の調整で行う（全体ルールを複雑化しない）
- 切替ヒステリシス（Δ, N分）の最適化をタスク化

