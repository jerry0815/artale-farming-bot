"""Supervised single climb burst: focus game, hold Up for N seconds, release,
screenshot. Climbing up a rope cannot drop the character. F8 aborts.

Usage: python climb_burst.py [seconds]   (default 1.0)
"""
import sys, time, ctypes, ctypes.wintypes
from pynput.keyboard import Key, Listener
import keyboard as kb
from calibrate_live import _capture, get_character
import cv2
from PIL import Image

TITLE = "MapleStory Worlds-Artale (?????)"
SP = r"C:\Users\jerry\AppData\Local\Temp\claude\C--jerry-toy-work-maple\76b73c35-1984-400a-a0fc-417f80626173\scratchpad"
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0


def focus():
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    gt = u.GetWindowThreadProcessId(hwnd, None)
    u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)
    u.AttachThreadInput(cur, gt, True)
    u.ShowWindow(hwnd, 9); u.BringWindowToTop(hwnd); u.SetForegroundWindow(hwnd)
    u.AttachThreadInput(cur, gt, False)
    time.sleep(0.4)


def shot(name):
    Image.fromarray(cv2.cvtColor(_capture(), cv2.COLOR_BGR2RGB)).save(SP + "\\" + name)


def main():
    print("before:", get_character())
    shot("climb_before.png")
    focus()
    print(f"holding Up for {secs}s ... (F8 to abort)")
    try:
        kb.safe_press(Key.up)
        end = time.time() + secs
        while time.time() < end:
            if kb.pause:
                break
            time.sleep(0.05)
    finally:
        kb.safe_release_all()
    time.sleep(0.4)
    print("after :", get_character())
    shot("climb_after.png")
    print("saved climb_after.png")


if __name__ == "__main__":
    lis = Listener(on_press=kb.on_press); lis.start()
    try:
        main()
    finally:
        kb.safe_release_all(); lis.stop()
