"""استخراج متن و رسانه از Rich Message های تلگرام (Bot API 10.1+).

پیام‌های Rich از چند بلوک ساخته می‌شوند (پاراگراف، تیتر، نقل‌قول، لیست، عکس، ویدیو،
کلاژ و...). این ماژول متن را به‌صورت پاراگراف‌های ساده و رسانه‌ها را به‌صورت file_id
بیرون می‌کشد تا مثل یک پست معمولی ترجمه و منتشر شوند.
"""

from telegram import Message

# کلید رسانه در بلوک ← نوع رسانه در بات
MEDIA_KEYS = {
    "photo": "photo",
    "video": "video",
    "animation": "animation",
    "audio": "audio",
    "document": "document",
    "voice_note": "voice",
    "voice": "voice",
}

# کلیدهایی که متن (RichText) نگه می‌دارند
TEXT_KEYS = ("text", "texts", "title", "summary")
# کلیدهای متنی فرعی که بعد از محتوای بلوک می‌آیند
SIDE_TEXT_KEYS = ("caption", "credit")


def get_rich_message(msg: Message) -> dict | None:
    """rich_message خام پیام را برمی‌گرداند (چه کتابخانه آن را بشناسد چه نه)."""
    value = getattr(msg, "rich_message", None)
    if value is not None:
        return value.to_dict() if hasattr(value, "to_dict") else value
    if msg.api_kwargs:
        value = msg.api_kwargs.get("rich_message")
        if isinstance(value, dict):
            return value
    return None


def flatten_text(rt) -> str:
    """RichText (رشته، آرایه یا شیء) را به متن ساده تبدیل می‌کند."""
    if rt is None:
        return ""
    if isinstance(rt, str):
        return rt
    if isinstance(rt, list):
        return "".join(flatten_text(x) for x in rt)
    if isinstance(rt, dict):
        for key in TEXT_KEYS:
            if key in rt:
                return flatten_text(rt[key])
        for key in ("emoji", "alt", "value"):
            if isinstance(rt.get(key), str):
                return rt[key]
    return ""


def _media_from_block(block: dict):
    for key, kind in MEDIA_KEYS.items():
        value = block.get(key)
        if not value:
            continue
        if isinstance(value, list):  # آرایه PhotoSize: بزرگ‌ترین اندازه
            sizes = [s for s in value if isinstance(s, dict) and s.get("file_id")]
            if sizes:
                best = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))
                return {"type": kind, "file_id": best["file_id"]}
        elif isinstance(value, dict) and value.get("file_id"):
            return {"type": kind, "file_id": value["file_id"]}
    return None


def _walk(block, paragraphs: list[str], media: list[dict]):
    if isinstance(block, list):
        for b in block:
            _walk(b, paragraphs, media)
        return
    if not isinstance(block, dict):
        return

    item = _media_from_block(block)
    if item:
        media.append(item)
    else:
        for key in TEXT_KEYS:
            if key in block:
                text = flatten_text(block[key]).strip()
                if text:
                    paragraphs.append(text)
                break

    for key in ("blocks", "items", "rows", "cells"):
        if key in block:
            _walk(block[key], paragraphs, media)

    for key in SIDE_TEXT_KEYS:
        if key in block:
            text = flatten_text(block[key]).strip()
            if text:
                paragraphs.append(text)


def extract_rich(msg: Message) -> tuple[str, list[dict]]:
    """(متن، رسانه‌ها) را از Rich Message برمی‌گرداند؛ اگر پیام Rich نباشد ("", [])."""
    rich = get_rich_message(msg)
    if not rich:
        return "", []
    paragraphs: list[str] = []
    media: list[dict] = []
    _walk(rich.get("blocks", []), paragraphs, media)
    return "\n\n".join(paragraphs), media
