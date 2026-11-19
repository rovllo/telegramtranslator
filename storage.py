import json
import os
from pathlib import Path

MAX_POSTS = 100


class Storage:
    """ذخیره کانال‌ها/گروه‌ها و پست‌های در انتظار در یک فایل JSON."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data = {"chats": {}, "posts": {}}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- chats ----
    def add_chat(self, chat_id: int, title: str, chat_type: str):
        self.data["chats"][str(chat_id)] = {"title": title, "type": chat_type}
        self._save()

    def remove_chat(self, chat_id: int) -> bool:
        removed = self.data["chats"].pop(str(chat_id), None) is not None
        self._save()
        return removed

    def chats(self) -> dict:
        return dict(self.data["chats"])

    # ---- posts ----
    def put_post(self, post: dict):
        posts = self.data["posts"]
        posts[post["id"]] = post
        while len(posts) > MAX_POSTS:
            posts.pop(next(iter(posts)))
        self._save()

    def get_post(self, pid: str):
        return self.data["posts"].get(pid)

    def find_by_message(self, message_id: int):
        for post in reversed(list(self.data["posts"].values())):
            if message_id in post.get("preview_ids", []):
                return post
        return None
