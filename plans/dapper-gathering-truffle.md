# ポジション表示グローバル化 + ダッシュボードレイアウト再構成

## Context

現在のダッシュボードでは、ポジション表示がエンジン実行中の通貨ペアに限定されている。
ユーザーは**全通貨ペアのポジションを一覧表示**したい。
同時に、レイアウトを再構成し:
- ポジションパネルをチャートエリアまで拡張（全幅）
- トレードヒストリーの右にインジケーターを並べる

### 現状のレイアウト

```
Row 1: メトリクスストリップ（5列）
Row 2: [Analysis 2/3] + [Position 1/3]   ← grid-cols-3
Row 3: [Chart 2/3]    + [Indicators 1/3] ← grid-cols-3
Row 4: [Trade History 全幅]
```

### 変更後のレイアウト

```
Row 1: メトリクスストリップ（5列）          ← 変更なし
Row 2: [Analysis 全幅]                     ← チャートと分離
Row 3: [Position 全幅]                     ← チャートエリアを吸収して拡張
Row 4: [Trade History 2/3] + [Indicators 1/3] ← 横並び
```

**チャートセクションは削除**（ポジション表示領域拡張のため）

---

## 変更対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `autotrader/live/engine.py` | `_manage_positions()` で全通貨ペア取得 + pip計算修正 |
| `autotrader/web/templates/dashboard.html` | グリッドレイアウト再構成 |
| `autotrader/web/static/js/dashboard.js` | シンボルフィルター削除 + col-span切替削除 |

---

## 1. `engine.py` — ポジション全通貨ペア取得

### 1.1 `_manage_positions()` (行1384)

**現状**: エンジンの通貨ペアのみ取得
```python
positions = await self._executor.get_open_positions_async(
    self._config.symbol
)
```

**変更後**: 全通貨ペア取得（`None`で全件）
```python
positions = await self._executor.get_open_positions_async(
    None
)
```

### 1.2 pip計算をポジションループ内に移動 (行1428-1431)

**現状**: ループ外で固定通貨ペアのpip計算
```python
pip_factor = self._get_pip_size(self._config.symbol)
pip_value = self._get_pip_value(self._config.symbol)

cache_list: list[dict] = []
for position in positions:
```

**変更後**: ループ内で各ポジションの通貨ペア別pip計算
```python
cache_list: list[dict] = []
for position in positions:
    # 通貨ペア別にpip計算（全通貨ペア対応）
    pip_factor = self._get_pip_size(position.symbol)
    pip_value = self._get_pip_value(position.symbol)
```

### 1.3 `_sync_positions()` (行1632) — 変更なし

`_sync_positions()` はPM登録専用（エンジン起動時のみ）。
PM管理はエンジン設定の通貨ペアのみが対象のため、
シンボルフィルターは維持する。

### 影響範囲の安全性

- **外部決済検出** (行1397-1402): `_open_trades` はこのエンジンが開いたticketのみ保持。
  全ポジションを取得しても、他通貨ペアのticketは `_open_trades` に存在しないため影響なし。
- **PM評価** (行1484-1486): `self._pm.get_position(pos_id)` が `None` を返す
  未登録ポジションはスキップされるため、他通貨ペアの管理には干渉しない。
- **ATR計算** (行1408-1421): エンジン通貨ペアのATRのみ使用。PM評価用のため変更不要。

---

## 2. `dashboard.html` — グリッドレイアウト再構成

### 変更概要

```html
<!-- Row 2: ライブアナリティクス（全幅） -->
<div id="analysis-panel" class="card border border-gray-700 hidden">
  <!-- 内容は変更なし -->
</div>

<!-- Row 3: ポジションパネル（全幅・拡張） -->
<div class="card" id="position-panel">
  <div class="flex items-center justify-between mb-3">
    <h2 class="text-sm font-semibold text-gray-300 uppercase tracking-wider">
      Positions
    </h2>
    <span id="position-count" class="text-xs text-gray-500">no open</span>
  </div>
  <div id="position-list" class="overflow-y-auto max-h-80" data-wide="true">
    <div class="flex items-center justify-center h-16 text-gray-500 text-sm">
      ポジションなし
    </div>
  </div>
</div>

<!-- Row 4: トレード履歴(2/3) + インジケータ(1/3) -->
<div class="grid grid-cols-1 lg:grid-cols-3 gap-4 items-stretch">
  <!-- 左: トレード履歴 -->
  <div class="lg:col-span-2" id="trade-history-panel">
    <div class="card h-full flex flex-col">
      <!-- トグルボタン + テーブル -->
    </div>
  </div>
  <!-- 右: インジケータ -->
  <div class="card flex flex-col" id="indicator-panel">
    <!-- TFタブ + グリッド -->
  </div>
</div>
```

### 削除するもの
- **チャートセクション全体** (現在の行72-116): `chart-container`, `rsi-panel`, OHLC bar, TFボタン等
- **Analysis+Positionを囲む `grid-cols-3` div** (行16): Analysis単独で全幅に
- **Chart+Indicatorsを囲む `grid-cols-3` div** (行73): 不要に

### scripts参照の削除
- `<script src="/static/js/chart.js?v=3">` の削除（チャート不使用のため）

### ポジションリスト `max-h`
- 現在: `max-h-64` (256px)
- 変更後: `max-h-80` (320px) — 全幅でより多くのポジションを表示

---

## 3. `dashboard.js` — フロントエンドロジック修正

### 3.1 `renderPositions()` シンボルフィルター削除 (行967-970)

**現状**:
```javascript
const displayPositions = this.symbol
  ? this.positions.filter(p => p.symbol === this.symbol)
  : this.positions;
```

**変更後**:
```javascript
// 全通貨ペアのポジションを表示
const displayPositions = this.positions;
```

### 3.2 `renderPositions()` wide固定化 (行979-980)

**現状**:
```javascript
const isWide = listEl.dataset.wide === 'true';
```

**変更後**:
```javascript
// 全幅パネルのため常にwideモード
const isWide = true;
```

### 3.3 `renderAnalysis()` col-span切替削除 (行188-206)

**現状**: ライブ/バックテストモードでポジションパネルの幅を動的変更
```javascript
const posPanel = document.getElementById('position-panel');
const posList = document.getElementById('position-list');
if (!isLive) {
  panel.classList.add('hidden');
  if (posPanel) {
    posPanel.classList.remove('lg:col-span-1');
    posPanel.classList.add('lg:col-span-3');
  }
  if (posList) posList.dataset.wide = 'true';
  return;
}
panel.classList.remove('hidden');
if (posPanel) {
  posPanel.classList.remove('lg:col-span-3');
  posPanel.classList.add('lg:col-span-1');
}
if (posList) posList.dataset.wide = 'false';
```

**変更後**: ポジションパネルは常に全幅のためcol-span操作不要
```javascript
if (!isLive) {
  panel.classList.add('hidden');
  return;
}
panel.classList.remove('hidden');
```

### 3.4 チャート関連コードの無効化

`ChartManager.init('chart-container', ...)` の呼び出しを削除またはガード。
チャート関連のイベントハンドラ（TFボタン、指標トグル、OHLC更新等）も不要に。

---

## 検証方法

```bash
# 1. 既存テスト（ポジション関連）
python -m pytest tests/unit/live/ -v -k "position" --no-header

# 2. 全テスト
python -m pytest tests/ -x -q

# 3. ダッシュボード動作確認（ブラウザ）
# - ポジションパネルが全幅表示されること
# - 複数通貨ペアのポジションが表示されること
# - インジケーターがトレード履歴の右に配置されること
# - Analysisパネルがライブ/デモモードで正常表示されること
```

---

## 変更まとめ

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| ポジション取得範囲 | エンジン通貨ペアのみ | 全通貨ペア |
| pip計算 | ループ外・固定通貨ペア | ループ内・各ポジション別 |
| ポジションパネル幅 | 1/3（ライブ時） | 全幅（常時） |
| フロントエンドフィルター | `this.symbol` でフィルター | フィルターなし |
| チャートセクション | 2/3幅で表示 | 削除 |
| インジケーター配置 | チャート右 (1/3) | トレード履歴右 (1/3) |
| トレード履歴配置 | 全幅 | 2/3幅 |
