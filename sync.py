"""起動時に一度だけ実行し、Discord #memo チャンネルの未取得メッセージを
Daily Note へ反映して終了するワンショット同期スクリプト。

bot.py（常駐監視Bot）とは別に、PCがオフラインの間にスマホ等から
#memo へ投稿されたメッセージを、PCがオンラインに戻ったタイミング
（Mac起動時・ログイン時）でまとめて取り込むために使う。

- 二重登録を防ぐため、state.json にチャンネルごとの最終取得メッセージIDを保持する。
- 初回同期（stateが無いチャンネル）は直近100件のみを対象にする。
- #task タグの変換ルールは memo_note.py を共有することで bot.py と常に一致させる。
"""

import asyncio
import json
import os
import socket
import ssl
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import certifi
import discord
from dotenv import load_dotenv

from memo_note import append_to_daily_note

# launchd経由の実行ではCWDが不定なため、スクリプトと同じ場所の.envを明示的に指定する
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

STATE_PATH = Path(__file__).parent / "state.json"
MEMO_CHANNEL_NAME = "memo"
FIRST_SYNC_LIMIT = 100

# 起動〜ログインのタイムアウト（オフライン時に無限待機しないようにする）
CONNECT_TIMEOUT_SECONDS = 30

# Discordへの本接続を試みる前の軽量な疎通確認（オフライン時に数秒で諦めるため）
PREFLIGHT_TIMEOUT_SECONDS = 3.0


def is_online() -> bool:
    try:
        with socket.create_connection(("discord.com", 443), timeout=PREFLIGHT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"channels": {}}
    with STATE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    tmp_path = STATE_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp_path.replace(STATE_PATH)


def find_memo_channels() -> list[discord.TextChannel]:
    channels = []
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == MEMO_CHANNEL_NAME:
                channels.append(channel)
    return channels


async def sync_channel(channel: discord.TextChannel, state: dict) -> None:
    channel_key = str(channel.id)
    channel_state = state["channels"].get(channel_key)
    last_message_id = channel_state["last_message_id"] if channel_state else None

    if last_message_id is None:
        # 初回同期: 直近 FIRST_SYNC_LIMIT 件のみを対象にする
        history = [msg async for msg in channel.history(limit=FIRST_SYNC_LIMIT)]
        history.reverse()  # 古い順に処理する
        print(f"[{channel.guild.name}/#{channel.name}] 初回同期: 直近{len(history)}件を対象")
    else:
        after = discord.Object(id=int(last_message_id))
        history = [
            msg async for msg in channel.history(after=after, oldest_first=True, limit=None)
        ]
        print(f"[{channel.guild.name}/#{channel.name}] 差分同期: {len(history)}件の新規メッセージ")

    processed = 0
    for message in history:
        # 自分自身（Bot）のメッセージには反応しない
        if message.author == client.user:
            continue

        when = message.created_at.astimezone()
        reply_to_message_id = message.reference.message_id if message.reference else None
        note_path = append_to_daily_note(
            message.content,
            message.id,
            when=when,
            reply_to_message_id=reply_to_message_id,
        )
        processed += 1

        # 1件処理するたびに state を保存し、途中で失敗しても
        # 既に処理済みのメッセージを再度取り込まないようにする
        state["channels"][channel_key] = {
            "last_message_id": str(message.id),
            "last_synced_at": datetime.now().astimezone().isoformat(),
        }
        save_state(state)
        print(f"  追記: {note_path.relative_to(note_path.parents[3])} <- {message.content!r}")

    if not history:
        # 新規メッセージが無くても、初回同期済みであることを記録する
        state["channels"][channel_key] = {
            "last_message_id": channel_state["last_message_id"] if channel_state else "0",
            "last_synced_at": datetime.now().astimezone().isoformat(),
        }
        save_state(state)

    print(f"[{channel.guild.name}/#{channel.name}] {processed}件を追記しました")


async def run_sync() -> None:
    state = load_state()
    channels = find_memo_channels()

    if not channels:
        print(f"警告: #{MEMO_CHANNEL_NAME} チャンネルが見つかりませんでした", file=sys.stderr)
        return

    for channel in channels:
        await sync_channel(channel, state)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    try:
        await run_sync()
    finally:
        await client.close()


async def main():
    # 実行中のイベントループ内で TCPConnector を生成する必要があるため、
    # client.start() の直前（コルーチン内）で SSL コンテキストと
    # connector を作成し、certifi の CA 証明書で
    # ClientConnectorCertificateError を回避する
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with client:
        client.http.connector = connector
        await asyncio.wait_for(client.start(DISCORD_TOKEN), timeout=CONNECT_TIMEOUT_SECONDS)


if __name__ == "__main__":
    if not is_online():
        # オフライン時は本接続を試みず即座に終了する（次のStartIntervalで再試行される）
        print("オフラインのため同期をスキップしました")
        sys.exit(0)

    try:
        asyncio.run(main())
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        # オフライン等でDiscordに接続できない場合はエラー終了する。
        # state.json は更新していないため、次回起動時に同じ範囲から再同期される。
        print(f"同期に失敗しました（オフラインの可能性があります）: {e}", file=sys.stderr)
        sys.exit(1)
    except discord.LoginFailure as e:
        print(f"Discordへのログインに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)
