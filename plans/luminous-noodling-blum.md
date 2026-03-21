# AT4 常駐起動バッチファイル作成

## Context

AT4（Live WebUI）を常駐アプリとして動かすための起動スクリプトが必要。
BT系（Runner, BT WebUI）は管理者専用のため対象外。
デスクトップに配置してダブルクリックで即起動できるようにする。

## 起動するプロセス

| プロセス | コマンド | ポート |
|---------|---------|--------|
| Live WebUI | `uv run python -m autotrader.web` | 8000 |

## 実装内容

### `start_at4.bat` をデスクトップに作成

```bat
@echo off
chcp 65001 >nul
title AutoTraderV4

cd /d "%~dp0"

echo ========================================
echo   AutoTraderV4 Starting...
echo   http://localhost:8000
echo ========================================

uv run python -m autotrader.web
```

- `%~dp0`: バッチファイル自身のディレクトリ = プロジェクトルート（環境非依存）

- ウィンドウタイトル「AutoTraderV4」でログが常時表示される
- プロセスが異常終了した場合もウィンドウが残りエラーが確認できる
- Ctrl+C で停止可能

### 配置先

- `D:\Projects\AutoTraderV4\start_at4.bat`（git管理対象）

## 修正対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `start_at4.bat` | **新規** AT4起動バッチ |

## 検証方法

1. `start_at4.bat` をダブルクリック → cmdウィンドウが開きLive WebUIが起動
2. `http://localhost:8000` にブラウザでアクセスできることを確認
3. Ctrl+C で停止できることを確認
