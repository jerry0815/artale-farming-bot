import threading
import time

import notify as notify_mod


def _capturing_notifier(cooldown=120.0, url="https://discord/webhook"):
    """Notifier whose poster records posts and signals an Event so the daemon-thread
    send can be awaited deterministically."""
    posts = []
    got = threading.Event()

    def poster(u, content):
        posts.append((u, content))
        got.set()

    n = notify_mod.DiscordNotifier(url=url, cooldown=cooldown, poster=poster, host="TESTPC")
    return n, posts, got


def test_disabled_when_no_url():
    n, posts, _ = _capturing_notifier(url="")
    assert not n.enabled
    assert n.notify("lie_check", "boom") is False
    time.sleep(0.02)
    assert posts == []


def test_notify_posts_message_with_host_and_body():
    n, posts, got = _capturing_notifier()
    assert n.enabled
    assert n.notify("lie_check", "captcha detected") is True
    assert got.wait(2.0)
    url, content = posts[0]
    assert url == "https://discord/webhook"
    assert "TESTPC" in content
    assert "captcha detected" in content


def test_second_call_within_cooldown_is_suppressed():
    n, posts, got = _capturing_notifier(cooldown=120.0)
    assert n.notify("lie_check", "first") is True
    assert got.wait(2.0)
    assert n.notify("lie_check", "second") is False   # same key, still cooling down
    time.sleep(0.05)
    assert len(posts) == 1


def test_cooldown_expires_allows_resend():
    n, posts, got = _capturing_notifier(cooldown=0.0)   # no cooldown -> always allowed
    assert n.notify("lie_check", "a") is True
    assert got.wait(2.0)
    got.clear()
    assert n.notify("lie_check", "b") is True
    assert got.wait(2.0)
    assert len(posts) == 2


def test_distinct_keys_do_not_share_cooldown():
    n, posts, got = _capturing_notifier(cooldown=120.0)
    assert n.notify("lie_check", "x") is True
    assert n.notify("another_player", "y") is True      # different key -> independent
    # both daemon posts should land
    for _ in range(200):
        if len(posts) == 2:
            break
        time.sleep(0.01)
    assert len(posts) == 2


def test_poster_exception_never_propagates():
    def boom(u, c):
        raise RuntimeError("network down")

    n = notify_mod.DiscordNotifier(url="https://x", cooldown=0.0, poster=boom, host="T")
    assert n.notify("k", "m") is True   # dispatch succeeds; the thread swallows the error
    time.sleep(0.05)                    # let the daemon thread run + print
