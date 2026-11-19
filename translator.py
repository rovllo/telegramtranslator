import asyncio
import logging
import re

from google import genai
from google.genai import errors, types

log = logging.getLogger("translator")

SYSTEM_PROMPT = """You are the translator and editor of a Persian Telegram channel.
You receive the text of a post. Produce the final Persian text for publishing.

Rules:
1. Translate into fluent, natural, correct written Persian. If the text is already Persian,
   just correct and polish it. Use correct Persian spelling, the half-space (ZWNJ) where
   needed (می‌شود، کتاب‌ها), and Persian punctuation (، ؛ ؟ « »).
2. Keep the structure EXACTLY: the same paragraphs, the same line breaks and blank lines,
   list items stay list items, emojis stay where they are.
3. Remove everything that belongs to the source channel: signatures, channel names,
   @usernames of the channel, t.me links, "join/subscribe/follow us" lines, the source's
   promo hashtags and ads. Keep links and mentions that are part of the actual content.
4. Keep names, numbers, prices, dates and units accurate. Write numbers with Persian digits,
   except inside URLs, code, and product/model names.
5. Output ONLY the final text: no explanations, no quotes around it, no Markdown
   (no **, no #, no code fences). If nothing remains after removing the source's
   signature, output nothing.
"""

RETRYABLE = {429, 500, 502, 503, 504}


class Translator:
    def __init__(self, api_key: str, model: str, thinking_level: str = ""):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.thinking_level = thinking_level

    def _config(self) -> types.GenerateContentConfig:
        cfg = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT)
        if self.thinking_level:
            try:
                cfg.thinking_config = types.ThinkingConfig(
                    thinking_level=self.thinking_level.upper()
                )
            except Exception:
                log.warning("thinking_level not supported by SDK, ignoring")
                self.thinking_level = ""
        return cfg

    @staticmethod
    def _prompt(text: str, previous: str | None, feedback: str | None) -> str:
        prompt = f'POST:\n"""\n{text}\n"""'
        if previous:
            prompt += (
                "\n\nA previous attempt produced the output below and the editor rejected it. "
                "Write a better, different version and fix any problems (wrong paragraphing, "
                "leftover signatures or links, awkward Persian, untranslated parts):\n"
                f'"""\n{previous}\n"""'
            )
        if feedback:
            prompt += f"\n\nEditor's instructions for this version (follow them exactly):\n{feedback}"
        return prompt

    @staticmethod
    def _clean(out: str) -> str:
        out = out.strip()
        out = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", out).strip()
        if out.startswith('"""') and out.endswith('"""'):
            out = out[3:-3].strip()
        return out

    async def translate(self, text: str, previous: str | None = None,
                        feedback: str | None = None) -> str:
        prompt = self._prompt(text, previous, feedback)
        delay = 2
        for attempt in range(6):
            try:
                resp = await self.client.aio.models.generate_content(
                    model=self.model, contents=prompt, config=self._config()
                )
                return self._clean(resp.text or "")
            except errors.APIError as e:
                # اگر مدل تنظیم thinking را نپذیرفت، بدون آن دوباره امتحان کن
                if e.code == 400 and self.thinking_level and "think" in str(e).lower():
                    log.warning("model rejected thinking config, retrying without it")
                    self.thinking_level = ""
                    continue
                if e.code in RETRYABLE and attempt < 5:
                    log.warning("Gemini %s, retrying in %ss", e.code, delay)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError("Gemini did not respond")
