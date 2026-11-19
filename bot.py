import asyncio
import html
import json
import logging
import re
import uuid

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
    ReplyParameters,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from storage import Storage
from translator import Translator

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

storage = Storage(config.DATA_FILE)
translator = Translator(config.GEMINI_API_KEY, config.GEMINI_MODEL, config.THINKING_LEVEL)

CAPTION_LIMIT = 1024
ALBUM_WAIT = 2.5
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

SEND_METHOD = {
    "photo": "send_photo",
    "video": "send_video",
    "animation": "send_animation",
    "document": "send_document",
    "audio": "send_audio",
    "voice": "send_voice",
}
INPUT_MEDIA = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "document": InputMediaDocument,
    "audio": InputMediaAudio,
}

_album_buffer: dict[str, list[Message]] = {}
_busy: set[str] = set()


# ---------------- helpers ----------------
def is_admin(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in config.ADMIN_IDS


def extract_media(msg: Message):
    if msg.photo:
        return {"type": "photo", "file_id": msg.photo[-1].file_id}
    if msg.video:
        return {"type": "video", "file_id": msg.video.file_id}
    if msg.animation:  # باید قبل از document چک شود
        return {"type": "animation", "file_id": msg.animation.file_id}
    if msg.document:
        return {"type": "document", "file_id": msg.document.file_id}
    if msg.audio:
        return {"type": "audio", "file_id": msg.audio.file_id}
    if msg.voice:
        return {"type": "voice", "file_id": msg.voice.file_id}
    return None


def render(translation: str) -> str:
    text = html.escape(translation.strip(), quote=False)
    if text and config.BOLD_FIRST_LINE:
        first, sep, rest = text.partition("\n")
        if sep and len(first) <= 150:
            text = f"<b>{first}</b>{sep}{rest}"
    return config.TEMPLATE.format(text=text, signature=config.SIGNATURE).strip()


def visible_len(body: str) -> int:
    plain = html.unescape(re.sub(r"<[^>]+>", "", body))
    return len(plain.encode("utf-16-le")) // 2


def keyboard(pid: str, only_retry: bool = False) -> InlineKeyboardMarkup:
    retry = InlineKeyboardButton("🔄 ترجمه دوباره", callback_data=f"re:{pid}")
    if only_retry:
        return InlineKeyboardMarkup([[retry]])
    publish = InlineKeyboardButton("📢 انتشار", callback_data=f"pub:{pid}")
    return InlineKeyboardMarkup([[publish, retry]])


async def send_post(bot, chat_id: int, post: dict, markup=None) -> list[int]:
    """پست را با قالب نهایی می‌فرستد. دکمه‌ها روی آخرین پیام قرار می‌گیرند."""
    body = render(post["translation"])
    fits = visible_len(body) <= CAPTION_LIMIT
    media = post["media"]
    ids: list[int] = []

    if not media:
        m = await bot.send_message(chat_id, body, parse_mode=ParseMode.HTML,
                                   reply_markup=markup, link_preview_options=NO_PREVIEW)
        return [m.message_id]

    if len(media) == 1:
        item = media[0]
        method = getattr(bot, SEND_METHOD[item["type"]])
        m = await method(chat_id, item["file_id"],
                         caption=body if fits else None,
                         parse_mode=ParseMode.HTML,
                         reply_markup=markup if fits else None)
        ids.append(m.message_id)
        if not fits:
            m = await bot.send_message(chat_id, body, parse_mode=ParseMode.HTML,
                                       reply_markup=markup, link_preview_options=NO_PREVIEW)
            ids.append(m.message_id)
        return ids

    group = []
    for i, item in enumerate(media):
        cap = body if (i == 0 and fits) else None
        group.append(INPUT_MEDIA[item["type"]](item["file_id"], caption=cap,
                                                parse_mode=ParseMode.HTML if cap else None))
    msgs = await bot.send_media_group(chat_id, group)
    ids += [m.message_id for m in msgs]

    if not fits:
        m = await bot.send_message(chat_id, body, parse_mode=ParseMode.HTML,
                                   reply_markup=markup, link_preview_options=NO_PREVIEW)
        ids.append(m.message_id)
    elif markup:
        m = await bot.send_message(chat_id, "👆 پیش‌نمایش آلبوم بالا", reply_markup=markup,
                                   reply_parameters=ReplyParameters(message_id=msgs[0].message_id))
        ids.append(m.message_id)
    return ids


async def show_preview(bot, post: dict):
    ids = await send_post(bot, post["admin_chat"], post, markup=keyboard(post["id"]))
    post["preview_ids"] = ids
    storage.put_post(post)


async def show_error(bot, post: dict, error: Exception):
    m = await bot.send_message(post["admin_chat"], f"❌ خطا در ترجمه:\n{error}",
                               reply_markup=keyboard(post["id"], only_retry=True))
    post["preview_ids"] = [m.message_id]
    storage.put_post(post)


async def delete_messages(bot, chat_id: int, ids: list[int]):
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except TelegramError:
            pass


_COMMON_FIELDS = {
    "message_id", "date", "chat", "from", "forward_origin", "forward_from", "forward_from_chat",
    "forward_from_message_id", "forward_signature", "forward_sender_name", "forward_date",
    "is_automatic_forward", "via_bot", "has_protected_content", "author_signature",
    "entities", "caption_entities", "reply_markup", "edit_date", "media_group_id",
    "show_caption_above_media", "sender_chat",
}


async def report_unsupported(bot, admin_chat: int, msg: Message):
    """نوع پیام پشتیبانی‌نشده را گزارش می‌کند تا بتوان پشتیبانی‌اش را اضافه کرد."""
    data = msg.to_dict()
    fields = sorted(k for k in data if k not in _COMMON_FIELDS)
    extra = sorted(msg.api_kwargs.keys()) if msg.api_kwargs else []
    log.warning("Unsupported message: %s", json.dumps(data, ensure_ascii=False, default=str))
    text = "این پیام متن یا رسانه‌ی پشتیبانی‌شده ندارد."
    if fields:
        text += "\n\nفیلدهای پیام: " + ", ".join(fields)
    if extra:
        text += "\nفیلدهای ناشناخته: " + ", ".join(extra)
    await bot.send_message(admin_chat, text)


# ---------------- core flow ----------------
async def create_post(bot, admin_chat: int, messages: list[Message]):
    messages.sort(key=lambda m: m.message_id)
    media = [x for x in (extract_media(m) for m in messages) if x]
    source = next((m.text or m.caption for m in messages if (m.text or m.caption)), "") or ""

    if not media and not source.strip():
        await report_unsupported(bot, admin_chat, messages[0])
        return

    post = {
        "id": uuid.uuid4().hex[:10],
        "admin_chat": admin_chat,
        "media": media,
        "source": source,
        "translation": "",
        "preview_ids": [],
        "published": False,
    }
    status = await bot.send_message(admin_chat, "⏳ در حال ترجمه…")
    try:
        if source.strip():
            post["translation"] = await translator.translate(source)
    except Exception as e:
        log.exception("translate failed")
        await status.delete()
        await show_error(bot, post, e)
        return
    await status.delete()
    await show_preview(bot, post)


async def retranslate(bot, post: dict, feedback: str | None = None):
    pid = post["id"]
    if pid in _busy:
        return
    _busy.add(pid)
    try:
        status = await bot.send_message(post["admin_chat"], "⏳ در حال ترجمه دوباره…")
        try:
            if post["source"].strip():
                post["translation"] = await translator.translate(
                    post["source"], previous=post["translation"] or None, feedback=feedback
                )
        except Exception as e:
            log.exception("retranslate failed")
            await status.delete()
            await delete_messages(bot, post["admin_chat"], post["preview_ids"])
            await show_error(bot, post, e)
            return
        await status.delete()
        await delete_messages(bot, post["admin_chat"], post["preview_ids"])
        await show_preview(bot, post)
    finally:
        _busy.discard(pid)


async def flush_album(bot, chat_id: int, key: str):
    await asyncio.sleep(ALBUM_WAIT)
    messages = _album_buffer.pop(key, [])
    if messages:
        try:
            await create_post(bot, chat_id, messages)
        except Exception:
            log.exception("album processing failed")
            await bot.send_message(chat_id, "❌ خطا در پردازش آلبوم.")


# ---------------- handlers ----------------
async def on_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    # ریپلای متنی روی پیش‌نمایش = ترجمه دوباره با توضیح شما
    if msg.reply_to_message and msg.text and not msg.forward_origin:
        post = storage.find_by_message(msg.reply_to_message.message_id)
        if post and not post.get("published"):
            await retranslate(context.bot, post, feedback=msg.text)
            return

    if msg.media_group_id:
        key = msg.media_group_id
        first = key not in _album_buffer
        _album_buffer.setdefault(key, []).append(msg)
        if first:
            context.application.create_task(flush_album(context.bot, msg.chat_id, key))
        return

    await create_post(context.bot, msg.chat_id, [msg])


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(update):
        await q.answer("اجازه ندارید.", show_alert=True)
        return

    action, _, pid = q.data.partition(":")
    post = storage.get_post(pid)
    if not post:
        await q.answer("این پست دیگر در حافظه نیست.", show_alert=True)
        return

    if action == "re":
        if pid in _busy:
            await q.answer("در حال انجام است…")
            return
        await q.answer("در حال ترجمه دوباره…")
        await retranslate(context.bot, post)
        return

    if action == "pub":
        if post.get("published"):
            await q.answer("این پست قبلاً منتشر شده.", show_alert=True)
            return
        chats = storage.chats()
        if not chats:
            await q.answer("هیچ کانال یا گروهی ثبت نشده. بات را ادمین کنید یا از /addchat استفاده کنید.",
                           show_alert=True)
            return
        await q.answer("در حال انتشار…")

        ok, failed = [], []
        for chat_id, info in chats.items():
            try:
                await send_post(context.bot, int(chat_id), post)
                ok.append(info["title"])
            except TelegramError as e:
                failed.append(f"{info['title']}: {e.message}")

        post["published"] = bool(ok)
        storage.put_post(post)
        if ok:
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except TelegramError:
                pass

        report = []
        if ok:
            report.append("✅ منتشر شد در:\n" + "\n".join(f"• {t}" for t in ok))
        if failed:
            report.append("❌ ناموفق:\n" + "\n".join(f"• {t}" for t in failed))
        await context.bot.send_message(post["admin_chat"], "\n\n".join(report),
                                       reply_parameters=ReplyParameters(message_id=q.message.message_id))


async def on_my_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.my_chat_member
    chat = cmu.chat
    if chat.type == "private":
        return
    status = cmu.new_chat_member.status
    title = chat.title or str(chat.id)
    if status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
        storage.add_chat(chat.id, title, chat.type)
        text = f"✅ ثبت شد: {title}"
    elif status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        storage.remove_chat(chat.id)
        text = f"➖ حذف شد: {title}"
    else:
        return
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text)
        except TelegramError:
            pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "سلام! هر پستی بفرستید یا فوروارد کنید (متن، عکس، ویدیو، آلبوم…) ترجمه و قالب‌بندی می‌شود.\n\n"
        "• 📢 انتشار: ارسال به همه کانال‌ها و گروه‌های ثبت‌شده\n"
        "• 🔄 ترجمه دوباره: یک نسخه جدید\n"
        "• ریپلای روی پیش‌نمایش با توضیح = ترجمه دوباره طبق توضیح شما\n\n"
        "دستورات:\n"
        "/chats فهرست مقصدها\n"
        "/addchat @channel یا آیدی عددی\n"
        "/removechat آیدی عددی"
    )


async def cmd_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chats = storage.chats()
    if not chats:
        await update.effective_message.reply_text("هنوز مقصدی ثبت نشده.")
        return
    lines = [f"• {info['title']} ({info['type']})\n  {cid}" for cid, info in chats.items()]
    await update.effective_message.reply_text("مقصدهای انتشار:\n\n" + "\n".join(lines))


async def cmd_addchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("مثال: /addchat @mychannel یا /addchat -1001234567890")
        return
    arg = context.args[0]
    try:
        target = int(arg)
    except ValueError:
        target = arg if arg.startswith("@") else f"@{arg}"
    try:
        chat = await context.bot.get_chat(target)
        me = await context.bot.get_chat_member(chat.id, context.bot.id)
    except TelegramError as e:
        await update.effective_message.reply_text(f"❌ پیدا نشد یا بات عضو نیست: {e.message}")
        return
    if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
        await update.effective_message.reply_text("❌ بات در این چت عضو یا ادمین نیست.")
        return
    storage.add_chat(chat.id, chat.title or str(chat.id), chat.type)
    await update.effective_message.reply_text(f"✅ ثبت شد: {chat.title}")


async def cmd_removechat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("مثال: /removechat -1001234567890")
        return
    try:
        removed = storage.remove_chat(int(context.args[0]))
    except ValueError:
        removed = False
    await update.effective_message.reply_text("✅ حذف شد." if removed else "پیدا نشد.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


def main():
    app = Application.builder().token(config.BOT_TOKEN).build()
    admin = filters.User(user_id=config.ADMIN_IDS)

    app.add_handler(CommandHandler("start", cmd_start, filters=admin))
    app.add_handler(CommandHandler("chats", cmd_chats, filters=admin))
    app.add_handler(CommandHandler("addchat", cmd_addchat, filters=admin))
    app.add_handler(CommandHandler("removechat", cmd_removechat, filters=admin))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & admin & ~filters.COMMAND, on_admin_message))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(pub|re):"))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(on_error)

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
