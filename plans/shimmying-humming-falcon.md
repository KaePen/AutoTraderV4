# AT4 起動時自動トレードON引数の追加

## Context

AT4を常駐プログラムとして運用する際、起動後に毎回WebUIから手動で自動トレードをONにするのは煩雑。
`--auto-trade` フラグで起動時に全8採用ペアの自動トレードを一括ONにする。

既に `AUTOTRADER_AUTO_TRADE=1` 環境変数で同等機能が存在するが、CLIフラグの方が直感的。
両方サポートし、CLIフラグが環境変数より優先される設計とする。

## 変更ファイル

### 1. `autotrader/web/__main__.py` (引数追加)
- `--auto-trade` フラグを `argparse` に追加
- `args.auto_trade` を環境変数 `AUTOTRADER_AUTO_TRADE` にセットして既存ロジックを再利用

```python
parser.add_argument(
    "--auto-trade",
    action="store_true",
    default=False,
    help="起動時に全採用ペアの自動トレードをONにする",
)
```

main()内:
```python
if args.auto_trade:
    os.environ["AUTOTRADER_AUTO_TRADE"] = "1"
```

### 2. 変更なし
- `autotrader/web/main.py`: 既に `AUTOTRADER_AUTO_TRADE` 環境変数を読んで `enable_auto_trade` に反映している (L105-109)
- `autotrader/live/config.py`: `enable_auto_trade: bool = False` はデフォルト値のまま
- フロントエンド: 変更不要（エンジン状態を反映するだけ）

## 検証方法

1. `python -m autotrader.web --auto-trade` で起動
2. AT4ダッシュボードで全8ペアの自動トレードがONになっていることを確認
3. `python -m autotrader.web`（フラグなし）で起動 → 従来通り全OFFを確認
