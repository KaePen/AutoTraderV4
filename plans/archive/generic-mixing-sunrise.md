# WebUI MT5ライブデータ統合 & 口座情報表示

## Context

MT5 Direct接続は成功し、`LiveTradingEngine`は正常に動作しているが、
WebUI上の各エンドポイントはMT5ライブデータを参照しておらず、
ハードコードされたデータやCSVファイルを返している。

**現状の問題**:
1. ダッシュボード: `MarketService.get_dashboard()` が `balance=1,000,000` をハードコード
2. ローソク足: `CandleService` が `data/csv/` からCSVファイルを読み込み（MT5からではない）
3. 指標: `MarketService.get_indicators()` が全てNoneを返すスタブ
4. 口座情報: `AccountInfo` にlogin/server/currency等のID情報がない
5. 口座切替: 未実装

## 変更方針

- ルーター層で `request.app.state.live_engine` を直接参照し、MT5からリアルタイムデータ取得
- `MarketService`（DB依存）はトレード履歴・サマリーの取得にのみ使用
- `AccountInfo` エンティティに口座識別フィールドを追加
- 口座切替はAPI経由でエンジン再起動（login/password/server指定）

## 変更ファイル一覧

| ファイル | 変更 |
|---------|------|
| `src/autotrader/core/entities.py` | `AccountInfo`にlogin/server/name/currency/leverageフィールド追加 |
| `src/autotrader/adapters/mt5/converters.py` | `mt5_account_to_entity()`で識別フィールドをマッピング |
| `src/autotrader/web/schemas/responses.py` | `AccountInfoResponse`に口座識別フィールド追加 |
| `src/autotrader/web/routers/dashboard.py` | `app.state.live_engine`からリアル口座情報取得 |
| `src/autotrader/web/routers/candles.py` | MT5から直接ローソク足を取得するルート追加 |
| `src/autotrader/web/routers/indicators.py` | MT5ローソク足から指標計算して返却 |
| `src/autotrader/web/routers/trading.py` | 口座切替エンドポイント追加、MT5StatusResponseに口座識別追加 |
| `src/autotrader/web/static/js/dashboard.js` | 口座情報表示にlogin/server追加 |
| `src/autotrader/web/templates/dashboard.html` | 口座情報表示エリア追加 |

## 詳細変更

### 1. `core/entities.py` - AccountInfo拡張

```python
class AccountInfo(BaseModel):
    balance: float
    equity: float
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    profit: float = 0.0
    # 口座識別情報（新規追加）
    login: int = 0
    server: str = ""
    name: str = ""
    currency: str = "JPY"
    leverage: int = 0
```

### 2. `adapters/mt5/converters.py` - mt5_account_to_entity拡張

MT5の`account_info()`は`login`, `server`, `name`, `currency`, `leverage`を返す。
これらをAccountInfoにマッピング。

### 3. `web/schemas/responses.py` - AccountInfoResponse拡張

AccountInfoResponseにも同じフィールドを追加（login/server/name/currency/leverage）。

### 4. `web/routers/dashboard.py` - MT5ライブデータ取得

`request.app.state.live_engine`がある場合、そこから口座情報を取得。
なければフォールバック（デフォルト値）。
- `Request`パラメータを追加
- `engine.account_info`からリアルデータを構築
- トレード集計は既存のDB経由ロジックを維持

### 5. `web/routers/candles.py` - MT5ローソク足取得

MT5接続時は`engine._data_provider.get_candles_from_pos()`でライブデータ取得。
未接続時はCSVフォールバック（既存ロジック）。
- `Request`パラメータを追加
- DataFrameからCandleResponseへ変換

### 6. `web/routers/indicators.py` - MT5ローソク足から指標計算

MT5接続時はローソク足を取得し、pandas_taで指標計算。
`_calc_indicators(df)` ヘルパーでRSI/MACD/ADX/BB/ATR/EMAを計算。
既存の`autotrader.calculator`モジュールのロジックを参照。

### 7. `web/routers/trading.py` - 口座切替

**新エンドポイント** `POST /trading/mt5/switch-account`:
- リクエストボディ: `login: int, password: str, server: str`
- 現在のエンジンを停止
- 新MT5Config/LiveTradingConfigでエンジン再作成
- 新エンジンを`app.state.live_engine`に設定

**既存の`get_mt5_status`**: AccountInfoResponseにlogin/server等を含む

### 8. フロントエンド変更

**dashboard.html**: トレーディングコントロール内に口座情報表示エリア追加

**dashboard.js**:
- `renderTradingControl()`で口座情報（login, server）を表示
- `renderMetrics()`でcurrencyに応じた通貨フォーマット

## 検証方法

1. `pytest tests/unit/` - 全テスト回帰なし
2. WebUI起動→MT5自動接続→ダッシュボードで実残高表示確認
3. チャートにMT5ライブローソク足表示確認
4. 指標パネルにリアルRSI/MACD等表示確認
5. 口座情報（login: 75466079, server: XMTrading-MT5 3）表示確認
6. `POST /api/v1/trading/mt5/switch-account` テスト
