import os
import json
import re
import html
import hashlib
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

RSS_URL = os.environ["RSS_URL"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

# 可選：Discord 顯示名稱與頭像
WEBHOOK_NAME = os.environ.get("WEBHOOK_NAME", "燕雲官方情報")
WEBHOOK_AVATAR = os.environ.get("WEBHOOK_AVATAR", "")

# 記錄已發送貼文，避免重複
STATE_FILE = Path("state.json")
MAX_SEEN = 100

# 第一次執行時是否把目前最新一條也發送出去
SEND_LATEST_ON_FIRST_RUN = os.environ.get("SEND_LATEST_ON_FIRST_RUN", "false").lower() == "true"


def load_seen():
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data.get("seen", [])
    except Exception:
        return []


def save_seen(seen):
    STATE_FILE.write_text(
        json.dumps({"seen": seen[-MAX_SEEN:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clean_html(value):
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    text = soup.get_text("\n")
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def item_id(entry):
    # 優先用 RSS 原本提供的 id/guid/link
    raw = (
        entry.get("id")
        or entry.get("guid")
        or entry.get("link")
        or (entry.get("title", "") + entry.get("published", ""))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def entry_text(entry):
    # 不同 RSS 來源欄位可能不同，依序嘗試
    candidates = [
        entry.get("summary"),
        entry.get("description"),
        entry.get("content", [{}])[0].get("value")
        if entry.get("content")
        else None,
        entry.get("title"),
    ]
    for value in candidates:
        text = clean_html(value)
        if text:
            return text
    return "(無文字內容)"


def translate_zh_tw(text):
    if not text or text == "(無文字內容)":
        return text

    # Discord 單段不要太長；翻譯服務也更適合分段
    try:
        return GoogleTranslator(source="auto", target="zh-TW").translate(text)
    except Exception as e:
        print(f"翻譯失敗：{e}")
        return "⚠️ 自動翻譯失敗，請查看下方原文。"


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def send_discord(entry):
    original = entry_text(entry)
    translated = translate_zh_tw(original)
    link = entry.get("link", "")

    embed = {
        "title": "燕雲十六聲｜官方 X 更新",
        "url": link if link else None,
        "description": truncate(translated, 3500),
        "fields": [
            {
                "name": "原文",
                "value": truncate(original, 900),
                "inline": False,
            }
        ],
        "footer": {
            "text": "來源：Where Winds Meet 官方 X"
        },
    }

    # 嘗試從 RSS 內抓第一張圖片
    image_url = None

    if entry.get("media_content"):
        for media in entry.media_content:
            url = media.get("url")
            if url:
                image_url = url
                break

    if not image_url:
        raw_html = ""
        if entry.get("content"):
            raw_html = entry.get("content", [{}])[0].get("value", "")
        raw_html = raw_html or entry.get("summary", "")
        soup = BeautifulSoup(raw_html, "html.parser")
        img = soup.find("img")
        if img and img.get("src"):
            image_url = img["src"]

    if image_url:
        embed["image"] = {"url": image_url}

    payload = {
        "username": WEBHOOK_NAME,
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    if WEBHOOK_AVATAR:
        payload["avatar_url"] = WEBHOOK_AVATAR

    response = requests.post(
        DISCORD_WEBHOOK_URL,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()


def main():
    print(f"讀取 RSS：{RSS_URL}")
    feed = feedparser.parse(RSS_URL)

    if feed.bozo and not feed.entries:
        raise RuntimeError(f"RSS 解析失敗：{feed.bozo_exception}")

    entries = list(feed.entries)
    if not entries:
        print("RSS 中沒有項目。")
        return

    seen = load_seen()
    seen_set = set(seen)

    # 第一次執行：預設只建立基準，不轟炸舊貼文
    if not seen:
        current_ids = [item_id(e) for e in entries]
        if SEND_LATEST_ON_FIRST_RUN:
            latest = entries[0]
            print("第一次執行：發送目前最新貼文。")
            send_discord(latest)
        else:
            print("第一次執行：只建立去重基準，不發送舊貼文。")

        save_seen(current_ids[-MAX_SEEN:])
        return

    new_entries = [e for e in entries if item_id(e) not in seen_set]

    if not new_entries:
        print("沒有新貼文。")
        return

    # RSS 通常是新→舊；倒序發送，Discord 顯示才是正常時間順序
    for entry in reversed(new_entries):
        print("發送：", entry.get("title", entry.get("link", "(無標題)")))
        send_discord(entry)
        seen.append(item_id(entry))

    save_seen(seen)
    print(f"完成，共發送 {len(new_entries)} 條。")


if __name__ == "__main__":
    main()
