"""Optional Discord webhook alerts for 'needs a human' events (lie-check / another player).

When the audible alarm fires the bot also pings a Discord channel (via an incoming
webhook), so you get a phone notification while away from the PC. Enable it by putting
the webhook URL in ONE of:
  * env var  DISCORD_WEBHOOK_URL
  * a file   notify.json  next to this module:  {"discord_webhook_url": "https://..."}
If neither is set, Discord notifications are silently OFF and the audible alarm is
completely unaffected -- this is a pure add-on to the existing alarm.

Sends are per-key rate limited (a repeating alarm won't spam the channel) and run on a
daemon thread, so posting never blocks or crashes the farming loop.
"""
import json
import os
import socket
import threading
import time

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify.json")


def _load_url():
    """Webhook URL from the env var (preferred) or notify.json; '' if neither is set."""
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if url:
        return url
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as fh:
            return (json.load(fh).get("discord_webhook_url") or "").strip()
    except (OSError, ValueError):
        return ""


def _http_post(url, content):
    """Default poster: fire the webhook. Kept tiny so tests can inject a fake."""
    import requests
    requests.post(url, json={"content": content}, timeout=10)


class DiscordNotifier:
    """Rate-limited, fire-and-forget Discord webhook poster.

    `notify(key, message)` posts at most once per `cooldown` seconds PER key, so a
    persistent alarm re-pings as an occasional reminder instead of spamming. A missing
    URL makes every call a silent no-op."""

    def __init__(self, url=None, cooldown=120.0, poster=_http_post, host=None):
        self.url = (url or "").strip()
        self.cooldown = cooldown
        self._poster = poster
        self._host = host or socket.gethostname()
        self._last = {}                       # key -> last send monotonic time
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.url)

    def notify(self, key, message):
        """Dispatch a post if this `key` hasn't fired within `cooldown`. Returns True if
        a send was dispatched (on a daemon thread), False if disabled or rate-limited."""
        if not self.url:
            return False
        now = time.monotonic()
        with self._lock:
            if now - self._last.get(key, -1e9) < self.cooldown:
                return False
            self._last[key] = now
        content = f"\U0001f3a3 **{self._host}** {time.strftime('%H:%M:%S')}\n{message}"
        threading.Thread(target=self._send, args=(content,), daemon=True).start()
        return True

    def _send(self, content):
        try:
            self._poster(self.url, content)
        except Exception as e:                # never let a network hiccup touch the bot
            print(f"[notify] discord post 失敗: {e}")


_singleton = None


def notifier():
    """Shared notifier, URL loaded once from env/notify.json."""
    global _singleton
    if _singleton is None:
        _singleton = DiscordNotifier(url=_load_url())
    return _singleton


def send(key, message):
    """Convenience: notify via the shared singleton."""
    return notifier().notify(key, message)
