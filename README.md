# discord-memo-bot

Discord の特定チャンネルに投稿したメッセージを、[Obsidian](https://obsidian.md/) の Daily Note（日付ごとの Markdown ノート）へ自動で追記する個人用 Bot です。

スマホから Discord に一言送るだけで、ローカルの Obsidian Vault にメモが溜まっていくようにすることを目的としています。

## 主な機能

- **`#memo` チャンネルへの投稿をリアルタイムで Daily Note に追記**（`bot.py`）
- **PC がオフラインだった間に投稿されたメッセージの取りこぼしを、オンライン復帰時にまとめて同期**（`sync.py`）
- **`#task` ハッシュタグを付けたメッセージを Markdown のチェックボックス（`- [ ] `）として記録**
- **Discord の「返信（Reply）」機能を使ったメッセージを、返信元の直下にインデント付き箇条書きとして挿入**
- **本文中の URL を、そのページの `<title>` を取得して `[ページタイトル](URL)` という Markdown リンクに自動変換**（取得できない場合は元の URL のまま）
- **同一メッセージの重複追記を防止**（`state.json` / `message_index.json`）

## システム構成・処理の流れ

このリポジトリには役割の異なる 3 つの Python ファイルがあります。

| ファイル | 役割 |
|---|---|
| `bot.py` | Discord に常駐接続し、`on_message` イベントで `#memo` チャンネルの新規メッセージをリアルタイムに処理する |
| `sync.py` | 一度だけ実行して終了するワンショットスクリプト。PC 起動・ログイン時や定期実行（`launchd`）によって、オフライン中に届いていた未取得メッセージをまとめて取り込む |
| `memo_note.py` | `bot.py` と `sync.py` の両方から呼ばれる共通ロジック。`#task` タグの解釈、URL のリンク化、Daily Note への書き込み、返信の挿入位置の管理を担う |

処理の流れ（共通）:

1. Discord の `#memo` チャンネルにメッセージが投稿される
2. `bot.py`（常駐時）または `sync.py`（同期時）がメッセージを受信する
3. `memo_note.append_to_daily_note()` が呼ばれ、
   - 本文から `#task` タグを抽出
   - 本文中の URL をページタイトル付きリンクに変換
   - 返信（Reply）であれば `message_index.json` から返信先の書き込み位置を検索し、その直下に挿入。返信でなければ Obsidian Vault 内の `Diary/YYYY/MM/YYYY-MM-DD.md` の末尾に追記
   - 書き込んだ位置を `message_id` と紐づけて `message_index.json` に記録
4. `sync.py` の場合はさらに、処理したメッセージの ID を `state.json` に保存し、次回起動時の重複防止・差分取得に使う

`sync.py` は `com.furukawaruri.discord-memo-sync.plist` を使って macOS の `launchd` に登録することを想定しています（ログイン時に 1 回実行、以後 120 秒ごとにオンラインかどうかを確認して再試行）。

## 必要な環境

- macOS（`launchd` を利用した自動同期を使う場合。`bot.py` 単体は他 OS でも動作します）
- Python 3.x（開発時は Python 3.14 系で動作確認）
- Obsidian などで管理している Markdown ベースのノート Vault
- Discord Bot アカウント（Discord Developer Portal で作成したアプリケーション）

## セットアップ方法

1. リポジトリを取得し、仮想環境を作成する

   ```bash
   git clone <このリポジトリのURL>
   cd discord-memo-bot
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. 依存パッケージをインストールする

   ```bash
   pip install -r requirements.txt
   ```

3. `memo_note.py` 内の `VAULT_PATH` を、自分の Obsidian Vault のパスに書き換える（デフォルトは `~/Documents/Obsidian Vault`）

   ```python
   VAULT_PATH = Path.home() / "Documents" / "Obsidian Vault"
   ```

4. `.env` ファイルを作成し、Discord Bot Token を設定する（詳細は後述）

5. Discord サーバーに `memo` という名前のテキストチャンネルを作成し、そこに Bot を招待する

## Discord Developer Portal で必要な設定

1. [Discord Developer Portal](https://discord.com/developers/applications) で新しいアプリケーションを作成する
2. 「Bot」タブで Bot を追加し、**Bot Token を発行する**（この値を `.env` に設定する）
3. 同じ「Bot」タブの **Privileged Gateway Intents** セクションで、**「MESSAGE CONTENT INTENT」を ON にする**（`bot.py` / `sync.py` はどちらも `intents.message_content = True` を使ってメッセージ本文を取得しているため必須）
4. 「OAuth2 → URL Generator」で以下を選択し、生成された URL からサーバーに Bot を招待する
   - **SCOPES**: `bot`
   - **BOT PERMISSIONS**: 最低限 `View Channels`（Read Messages）と `Read Message History` が必要（`sync.py` がチャンネル履歴を取得するため）
5. 招待先サーバーに `memo` という名前のテキストチャンネルを作成する（チャンネル名で判定しているため、名前は正確に `memo` にする）

## `.env` の設定方法

プロジェクトルートに `.env` ファイルを作成し、以下のキーを設定する。

```
DISCORD_TOKEN=（Discord Developer Portal で発行した Bot Token）
```

現在使用している環境変数はこの `DISCORD_TOKEN` のみです。Obsidian Vault のパスなどは `.env` ではなく `memo_note.py` 内の定数（`VAULT_PATH`）で管理されています。

`.env` は `.gitignore` に含まれており、Git の追跡対象から除外されています。

## Bot の起動方法

### 常駐 Bot（リアルタイム監視）

```bash
python3 bot.py
```

起動すると `Logged in as <Bot名>` と表示され、以後 `#memo` チャンネルへの投稿を監視し続けます。停止するまでプロセスは終了しません。

### ワンショット同期（オフライン中の取りこぼし回収）

```bash
python3 sync.py
```

一度だけ Discord に接続し、`#memo` チャンネルの未取得メッセージを取り込んだ後、自動的に終了します。オフラインの場合は数秒の疎通確認のみで即座に終了します（`sys.exit(0)`）。

macOS では `com.furukawaruri.discord-memo-sync.plist` を `~/Library/LaunchAgents/` に配置し、`launchctl load` することで、ログイン時および 120 秒間隔での自動実行を設定できます（`plist` 内のパスは環境に合わせて書き換えてください）。

```bash
cp com.furukawaruri.discord-memo-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.furukawaruri.discord-memo-sync.plist
```

## Discord の `#memo` への投稿方法

`memo` という名前のテキストチャンネルに、そのままメッセージを送るだけです。送った内容がそのまま Daily Note の箇条書き（`- HH:MM 本文`）として追記されます。

例:

```
朝ごはんにパンを食べた
```

→ `Diary/2026/08/2026-08-04.md` に以下のように追記されます。

```
- 09:15 朝ごはんにパンを食べた
```

## `#task` の使い方

メッセージ本文に `#task` というハッシュタグを含めて投稿すると、そのタグは本文から取り除かれ、代わりに Markdown のチェックボックス（`- [ ] `）付きの行として記録されます。

例:

```
牛乳を買う #task
```

→

```
- [ ] 09:20 牛乳を買う
```

`#task` 以外のハッシュタグ（`#Dev` など）は特別扱いされず、本文にそのまま残ります。

## Discord の Reply（返信）を使った追加コメントの仕組み

`#memo` チャンネルで、過去に投稿したメッセージに対して Discord の「返信」機能で返信すると、その内容は新しい行として追記されるのではなく、**返信元メッセージの直下にインデントを1段深めた箇条書きとして挿入**されます。

これは `message_index.json` に「どの Discord メッセージ ID が、どのファイルのどの行に書き込まれたか」を記録しておき、返信が来た際にその情報を参照することで実現しています。返信を重ねるとその分インデントが深くなり、ツリー構造になります。

返信元のメッセージが `message_index.json` に見つからない場合（記録前に削除された、`message_index.json` 自体が失われた、など）は、通常の新規メッセージとして末尾に追記されます。

## Obsidian への保存形式

- 保存先: `<Vault>/Diary/YYYY/MM/YYYY-MM-DD.md`（年・月ディレクトリが無ければ自動作成）
- 通常のメッセージ: `- HH:MM 本文`
- `#task` タグ付き: `- [ ] HH:MM 本文`
- 返信: 返信元の行の直下に、ネストの深さに応じて半角スペース4個分（`INDENT_UNIT`）ずつインデントした箇条書きとして挿入
- 本文中の URL: 取得できたページタイトルを使って `[ページタイトル](URL)` 形式のリンクに変換

## `sync.py` による同期の仕組み

- 起動直後に `discord.com:443` への TCP 接続を試み（タイムアウト 3 秒）、失敗した場合は Discord への本接続を試みずに即終了します（オフライン時に長時間ブロックしないため）
- Bot が参加している**すべてのサーバー**の、`memo` という名前の**すべてのテキストチャンネル**を対象に同期します
- チャンネルごとに `state.json` に記録された `last_message_id` を確認し、
  - 記録が無ければ（初回同期）直近 100 件のみを対象にする
  - 記録があれば、そのメッセージ以降に投稿された全メッセージを対象にする
- メッセージは投稿日時順（古い順）に処理され、1 件処理するごとに `state.json` を保存する（途中で失敗しても、処理済み分の再取り込みを防ぐため）
- Discord への接続・ログイン自体のタイムアウトは 30 秒
- オフライン等で同期に失敗した場合は `state.json` を更新せず、終了コード 1 で終了する（次回起動時に同じ範囲から再試行される）

## `state.json` による重複防止

`sync.py` が使用する状態ファイルです。チャンネル ID ごとに、最後に取り込んだメッセージの ID (`last_message_id`) と同期日時 (`last_synced_at`) を保持します。

```json
{
  "channels": {
    "<チャンネルID>": {
      "last_message_id": "<メッセージID>",
      "last_synced_at": "2026-08-04T15:19:12.013171+09:00"
    }
  }
}
```

保存はテンポラリファイル (`state.json.tmp`) に書き出してからリネームする方式で行われ、書き込み途中でのファイル破損を避けています。`.gitignore` により Git の追跡対象からは除外されています。

同様に `message_index.json`（返信の挿入位置の対応表。詳細は前節）も、実際のメモ本文の一部を含むため `.gitignore` で除外されています。データ構造だけを示すサンプルとして `message_index.example.json` をリポジトリに含めています（ダミーの内容で、アプリケーションからは参照されません）。

なお `bot.py`（常駐監視）は Discord からのイベントをそのまま処理するだけで `state.json` は使用しません。重複防止・差分取得の仕組みは `sync.py` 専用です。

## URL タイトル取得機能

本文中に URL が含まれる場合、`memo_note.py` の `fetch_page_title()` がその URL に対して HTTP リクエストを送り、レスポンスの `<title>` タグを取得します。取得できたタイトルは HTML エンティティのデコード・空白の正規化を行った上で、`[ページタイトル](URL)` という Markdown リンクに変換されます。

- タイムアウト: 5 秒
- 読み込み上限: 先頭 65536 バイトまで
- `Content-Type` が `html` を含まない場合は取得を行わない
- 文字コードは `Content-Type` の `charset` を参照し、指定が無ければ UTF-8 として扱う
- 以下の場合はタイトル変換を行わず、URL をそのまま本文に残す
  - ネットワークエラー・タイムアウト
  - HTML 以外のコンテンツ
  - `<title>` タグが存在しない
- タイトル中の `[` `]` はリンク記法が壊れないようエスケープされる
- URL の直後に付いた句読点・括弧（`.` `,` `)` `」` など）はリンクの外側に残される

## ファイル構成

```
discord-memo-bot/
├── bot.py                                    # 常駐監視Bot（リアルタイム処理）
├── sync.py                                   # ワンショット同期スクリプト（launchd想定）
├── memo_note.py                              # #memo→Daily Note変換の共通ロジック
├── requirements.txt                          # 依存パッケージ
├── com.furukawaruri.discord-memo-sync.plist  # launchd用の設定ファイル（sync.pyの定期実行）
├── .env                                      # Discord Bot Token（Git管理外）
├── .gitignore
├── state.json                                # sync.py用の同期状態（Git管理外）
├── message_index.json                        # メッセージID→書き込み位置の対応表（Git管理外、メモ本文を含む）
├── message_index.example.json                # message_index.jsonのデータ構造サンプル（ダミー内容）
├── sync.log / sync.error.log                 # sync.pyの実行ログ（Git管理外）
└── README.md
```

## トラブルシューティング

- **`discord.LoginFailure` が出て起動しない**
  `.env` の `DISCORD_TOKEN` が正しいか確認してください。Developer Portal でトークンを再発行した場合は `.env` も更新が必要です。

- **メッセージを送っても Daily Note に何も追記されない**
  - チャンネル名が正確に `memo` になっているか確認してください（`bot.py` / `sync.py` はチャンネル名で判定しています）
  - Developer Portal で **MESSAGE CONTENT INTENT** が ON になっているか確認してください（OFF の場合、メッセージ本文を取得できません）
  - `memo_note.py` の `VAULT_PATH` が実際の Obsidian Vault のパスと一致しているか確認してください

- **`sync.py` を実行しても「オフラインのため同期をスキップしました」と出る**
  `discord.com:443` への疎通確認（3 秒タイムアウト）に失敗しています。ネットワーク接続を確認してください。

- **`sync.py` のログに「同期に失敗しました（オフラインの可能性があります）」と出る**
  疎通確認後、実際の Discord への接続（ログイン）がタイムアウト（30 秒）または失敗しています。`sync.error.log` に詳細が出力されます。`state.json` は更新されていないため、次回実行時に同じ範囲から再試行されます。

- **返信（Reply）したのに返信元の直下ではなく末尾に追記される**
  返信元メッセージが `message_index.json` に記録されていない場合（`message_index.json` を削除した、返信元がこの Bot 導入前のメッセージである、など）に発生します。この場合は通常の新規メッセージとして扱われます。

- **`launchd` 経由で `sync.py` が動かない**
  `com.furukawaruri.discord-memo-sync.plist` 内のパス（Python の場所、`sync.py` の場所）が実際の環境と一致しているか確認してください。`launchctl load` 後に `sync.log` / `sync.error.log` を確認すると原因を特定しやすいです。

## セキュリティ上の注意

- **Bot Token を Git にコミットしないこと。** `.env` は `.gitignore` に含まれていますが、誤って `git add -f` などで追跡してしまわないよう注意してください。もし誤ってコミット・公開してしまった場合は、Developer Portal で速やかにトークンを再発行（無効化）してください。
- **`state.json` にはチャンネル ID とメッセージ ID が含まれます。** `.gitignore` により Git 管理対象からは除外されています。
- **`message_index.json` には、Daily Note に書き込んだ本文の一部（メモの内容そのもの）が含まれます。** 個人的なメモの内容を含むため `.gitignore` で除外しています。データ構造を示す目的で、ダミー内容の `message_index.example.json` のみをリポジトリに含めています。
- **`sync.log` / `sync.error.log` は `.gitignore` の `*.log` により除外されています。**
- Bot に付与する権限は必要最小限（`View Channels` / `Read Message History`）に留めることを推奨します。
