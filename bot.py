import asyncio
import os
import ssl

import aiohttp
import certifi
import discord
from dotenv import load_dotenv

from memo_note import append_to_daily_note

# .env ファイルから環境変数を読み込む
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# メッセージ内容を受信するためのインテントを有効化
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    # 起動時に Bot 名をコンソールへ表示する
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message):
    # 自分自身（Bot）のメッセージには反応しない
    if message.author == client.user:
        return

    # #memo チャンネルで受信したメッセージをデイリーノートへ追記する
    if message.channel.name == "memo":
        reply_to_message_id = message.reference.message_id if message.reference else None
        note_path = append_to_daily_note(
            message.content,
            message.id,
            reply_to_message_id=reply_to_message_id,
        )
        print(f"追記しました: {note_path}")


async def main():
    # 実行中のイベントループ内で TCPConnector を生成する必要があるため、
    # client.start() の直前（コルーチン内）で SSL コンテキストと
    # connector を作成し、certifi の CA 証明書で
    # ClientConnectorCertificateError を回避する
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with client:
        client.http.connector = connector
        await client.start(DISCORD_TOKEN)


asyncio.run(main())
