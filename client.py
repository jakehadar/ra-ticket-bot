import collections

import telegram


class TelegramClient:
    def __init__(self, bot_token, chat_id, app_name=''):
        self.bot = telegram.Bot(token=bot_token)
        self.chat_id = int(chat_id)
        self.app_name = app_name
        self.updates_queue = collections.deque()
        self.updates_processed = set()

    def send_message(self, msg):
        text = f"{self.app_name}: {msg}" if self.app_name else msg
        self.bot.send_message(chat_id=self.chat_id, text=text)

    def fetch_updates(self):
        for update in self.bot.get_updates():
            if update not in self.updates_processed:
                if update.effective_user.id == self.chat_id:
                    self.updates_queue.append(update)
                self.updates_processed.add(update)

    def has_pending_updates(self):
        return len(self.updates_queue) > 0

    def pop_update(self):
        if not self.has_pending_updates():
            self.fetch_updates()
        if self.has_pending_updates():
            return self.updates_queue.popleft()

    def pop_newest(self):
        self.fetch_updates()
        if self.has_pending_updates():
            latest = self.updates_queue.pop()
            self.updates_queue.clear()
            return latest
