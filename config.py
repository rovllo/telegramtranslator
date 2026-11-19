import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "low").strip()
ADMIN_IDS = [int(x) for x in os.environ["ADMIN_IDS"].split(",") if x.strip()]
DATA_FILE = os.getenv("DATA_FILE", "data.json")

# ---------------- قالب انتشار (HTML تلگرام) ----------------
# امضای کانال خودتان
SIGNATURE = '🆔 <a href="https://t.me/YourChannel">@YourChannel</a>'

# خط اول (تیتر) پررنگ شود؟
BOLD_FIRST_LINE = True

# {text} = متن ترجمه‌شده، {signature} = امضا
TEMPLATE = "{text}\n\n{signature}"
