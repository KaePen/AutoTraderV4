---
name: deploy-live
description: mainブランチで検証済みの変更をliveブランチにデプロイする。main → live へのPRを作成・マージする。
---

# deploy-live

mainブランチで検証済みの変更をliveブランチ（本番リアルトレード環境）にデプロイする。

## 手順

### 1. 差分確認

main と live の差分を確認する:

```bash
git fetch origin main live
git log live..main --oneline
```

- 差分がなければ「デプロイ対象なし」と表示して終了する
- 差分があれば、変更内容のサマリーを表示する

### 2. 変更サマリー表示

差分のコミット一覧を表示し、変更の概要をユーザーに説明する:

```bash
git log live..main --oneline --no-merges
git diff live..main --stat
```

### 3. PR作成

main → live へのPRを作成する:

```bash
gh pr create --base live --head main \
  --title "Deploy to live: <変更サマリー>" \
  --body "$(cat <<'EOF'
## Deploy to live

### 含まれる変更
<コミット一覧をリスト表示>

### 変更サマリー
<変更内容の概要>

---
mainブランチで検証済みの変更をliveブランチにデプロイします。
EOF
)"
```

### 4. PRのURL表示

作成したPRのURLを表示する。

### 5. ユーザー確認

ユーザーにマージの承認を求める。承認されない場合はPRを残して終了する。

### 6. マージ実行

ユーザーの承認後、PRをマージする:

```bash
gh pr merge <PR番号> --merge
```

### 7. マージ後確認

liveブランチが最新化されたことを確認する:

```bash
git fetch origin live
git log live..main --oneline
```

差分がなければデプロイ完了。

## 注意事項

- liveブランチへの直接コミット・プッシュは禁止
- liveへのマージは必ずこのスキル経由で行う
- worktreeブランチからliveへの直接マージは禁止（必ずmain経由）
- マージ前にユーザーの明示的な承認が必要
