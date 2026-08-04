"""#memo チャンネルのメッセージをDaily Noteへ変換・追記する共通ロジック。

bot.py（常駐監視）と sync.py（起動時同期）の両方から利用され、
#task タグの変換ルールが両者で常に一致するようにする。

Discordの返信（reply）は、返信先メッセージの直下にインデント付き箇条書きとして
挿入する。「どのメッセージがDaily Noteのどのファイル・どの行に書かれたか」を
message_index.json に記録しておくことで、後から届く返信が確実に返信先を
見つけられるようにしている。

本文中のURLは、Webページの<title>を取得できれば [ページタイトル](URL) の
Markdownリンク形式に変換する（取得できない場合は元のURLのまま残す）。
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path

# Obsidian Vault のルートディレクトリ（環境に合わせて変更する）
VAULT_PATH = Path.home() / "Documents" / "Obsidian Vault"

# 本文中のハッシュタグとして特別な意味を持たせるタグ一覧
TASK_TAG = "task"
RECOGNIZED_TAGS = {TASK_TAG}

# 本文中のURL検出パターン（末尾に付きがちな約物は後で切り離す）
URL_PATTERN = re.compile(r"https?://[^\s<>]+")
URL_TRAILING_PUNCTUATION = ".,;:!?)]}、。」』】　"

# URLタイトル取得のタイムアウト・User-Agent
TITLE_FETCH_TIMEOUT_SECONDS = 5
TITLE_FETCH_USER_AGENT = "Mozilla/5.0 (compatible; ObsidianMemoBot/1.0)"
TITLE_FETCH_MAX_BYTES = 65536

# DiscordメッセージID -> Daily Note上の書き込み位置 の対応表
# （Vault内ではなく、このスクリプト自体の管理情報として保持する）
INDEX_PATH = Path(__file__).parent / "message_index.json"

# ネストの1階層あたりのインデント幅
INDENT_UNIT = "    "


def extract_tags(content: str) -> tuple[set[str], str]:
    """本文から認識可能なハッシュタグ（例: #task）を抽出して取り除く。

    複数のタグを同時に指定できる。認識しないハッシュタグは本文にそのまま残す。
    """
    found_tags: set[str] = set()

    def replace(match: re.Match) -> str:
        tag = match.group(1)
        if tag in RECOGNIZED_TAGS:
            found_tags.add(tag)
            return ""
        return match.group(0)

    cleaned = re.sub(r"#(\w+)", replace, content)
    # タグを取り除いたことでできた余分な空白を整形する
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return found_tags, cleaned


def _extract_charset(content_type: str) -> str | None:
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    return match.group(1) if match else None


def fetch_page_title(url: str) -> str | None:
    """指定URLのWebページから<title>を取得する。

    取得できない場合（ネットワークエラー、タイムアウト、HTML以外のコンテンツ、
    <title>が存在しないなど）はNoneを返す。
    """
    try:
        request = urllib.request.Request(url, headers={"User-Agent": TITLE_FETCH_USER_AGENT})
        with urllib.request.urlopen(request, timeout=TITLE_FETCH_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None
            raw = response.read(TITLE_FETCH_MAX_BYTES)
    except (urllib.error.URLError, OSError, ValueError):
        return None

    charset = _extract_charset(content_type) or "utf-8"
    try:
        html_text = raw.decode(charset, errors="ignore")
    except LookupError:
        html_text = raw.decode("utf-8", errors="ignore")

    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    title = unescape(match.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    return title or None


def linkify_urls(text: str) -> str:
    """本文中のURLを [ページタイトル](URL) のMarkdownリンクへ変換する。

    タイトルが取得できないURLはそのままの文字列として残す。
    """

    def replace(match: re.Match) -> str:
        url = match.group(0)

        # URLの直後に付いた句読点・括弧類はURLに含めず、リンクの外側に残す
        trailing = ""
        while url and url[-1] in URL_TRAILING_PUNCTUATION:
            trailing = url[-1] + trailing
            url = url[:-1]
        if not url:
            return match.group(0)

        title = fetch_page_title(url)
        if not title:
            return match.group(0)

        safe_title = title.replace("[", "\\[").replace("]", "\\]")
        return f"[{safe_title}]({url}){trailing}"

    return URL_PATTERN.sub(replace, text)


def _load_index() -> dict:
    if not INDEX_PATH.exists():
        return {}
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_index(index: dict) -> None:
    tmp_path = INDEX_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    tmp_path.replace(INDEX_PATH)


def _record_message(message_id: int, note_path: Path, anchor_line: str, depth: int) -> None:
    index = _load_index()
    index[str(message_id)] = {
        "file": note_path.relative_to(VAULT_PATH).as_posix(),
        "line": anchor_line,
        "depth": depth,
    }
    _save_index(index)


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _insert_line_after_block(note_path: Path, anchor_line: str, anchor_depth: int, new_line: str) -> None:
    """anchor_line（とその既存の返信ブロック）の直後に new_line を挿入する。

    anchor_line が見つからない場合は ValueError を送出し、呼び出し側で
    通常の末尾追記にフォールバックできるようにする。
    """
    if not note_path.exists():
        raise ValueError(f"note file not found: {note_path}")

    lines = note_path.read_text(encoding="utf-8").split("\n")

    idx = next((i for i, line in enumerate(lines) if line == anchor_line), None)
    if idx is None:
        raise ValueError(f"anchor line not found: {anchor_line!r}")

    anchor_indent = anchor_depth * len(INDENT_UNIT)

    # anchor_line自身の複数行本文や、既存の返信（より深いインデント）を
    # スキップし、ブロックの終わり（同階層以下の箇条書き・見出し・空行）で止める
    insert_at = idx + 1
    while insert_at < len(lines):
        line = lines[insert_at]
        if line.strip() == "":
            break
        stripped = line.lstrip(" ")
        if stripped.startswith("- ") or stripped.startswith("#"):
            if _line_indent(line) <= anchor_indent:
                break
        insert_at += 1

    lines.insert(insert_at, new_line)
    note_path.write_text("\n".join(lines), encoding="utf-8")


def append_to_daily_note(
    content: str,
    message_id: int,
    when: datetime | None = None,
    reply_to_message_id: int | None = None,
) -> Path:
    """Daily Noteへメッセージを追記する。

    - 通常のメッセージ: Diary/YYYY/MM/YYYY-MM-DD.md の末尾に追記する
      （when省略時は現在時刻。bot.py のリアルタイム受信向け）。
    - Discordの返信（reply_to_message_id指定時）: message_index.json から
      返信先メッセージの書き込み位置を引き、その直下にインデント付き箇条書きで
      挿入する。返信先が見つからない場合は通常のメッセージとして末尾に追記する。

    書き込んだメッセージ自身の位置は message_id で message_index.json に
    記録し、以後この message への返信が来た際に参照できるようにする。
    """
    tags, body = extract_tags(content)
    body = linkify_urls(body)
    now = when if when is not None else datetime.now()
    checkbox = "[ ] " if TASK_TAG in tags else ""
    time_str = now.strftime("%H:%M")

    parent = None
    if reply_to_message_id is not None:
        parent = _load_index().get(str(reply_to_message_id))

    if parent is not None:
        depth = parent["depth"] + 1
        raw_line = f"{INDENT_UNIT * depth}- {checkbox}{time_str} {body}"
        anchor_line = raw_line.split("\n", 1)[0]
        note_path = VAULT_PATH / parent["file"]
        try:
            _insert_line_after_block(note_path, parent["line"], parent["depth"], raw_line)
        except ValueError:
            # 返信先が見つからない（既に削除・未追跡など）場合は通常のメモとして扱う
            parent = None

    if parent is None:
        depth = 0
        raw_line = f"- {checkbox}{time_str} {body}"
        anchor_line = raw_line.split("\n", 1)[0]
        note_path = (
            VAULT_PATH
            / "Diary"
            / now.strftime("%Y")
            / now.strftime("%m")
            / f"{now.strftime('%Y-%m-%d')}.md"
        )

        # 年・月のディレクトリが存在しない場合は作成する
        note_path.parent.mkdir(parents=True, exist_ok=True)

        # 既存ファイルの末尾が改行で終わっていない場合は、改行を1つ補ってから追記する
        needs_leading_newline = False
        if note_path.exists() and note_path.stat().st_size > 0:
            with note_path.open("rb") as f:
                f.seek(-1, os.SEEK_END)
                needs_leading_newline = f.read(1) != b"\n"

        # ファイルが存在しない場合は新規作成し、存在する場合は末尾に追記する
        with note_path.open("a", encoding="utf-8") as f:
            if needs_leading_newline:
                f.write("\n")
            f.write(raw_line + "\n")

    _record_message(message_id, note_path, anchor_line, depth)
    return note_path
