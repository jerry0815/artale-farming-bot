"""Fast atomic rope EXIT: focus, climb Up to the top, then jump onto the platform
in one uninterrupted sequence (no pausing on the rope). 'n' aborts.

Climb stops when y reaches platform level OR y stops decreasing (top reached) OR
a hard time cap. Then it up-jumps (Up held + Alt) to hop onto the platform.
"""
import time, ctypes
from pynput.keyboard import Key, Listener
import keyboard as kb
from calibrate_live import _capture, get_character
import cv2
from PIL import Image

TITLE = "MapleStory Worlds-Artale (?????)"
SP = r"C:\Users\jerry\AppData\Local\Temp\claude\C--jerry-toy-work-maple\76b73c35-1984-400a-a0fc-417f80626173\scratchpad"
JUMP = Key.alt_l
PLATFORM_TOP_Y = 92     # band-crop y at/above which we're at platform level
CLIMB_MAX = 4.0


def focus():
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, TITLE)
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    gt = u.GetWindowThreadProcessId(hwnd, None)
    u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)
    u.AttachThreadInput(cur, gt, True)
    u.ShowWindow(hwnd, 9); u.BringWindowToTop(hwnd); u.SetForegroundWindow(hwnd)
    u.AttachThreadInput(cur, gt, False)
    time.sleep(0.3)


def main():
    print("before:", get_character())
    focus()
    kb.safe_press(Key.up)
    last_y, stable, t0 = None, 0, time.time()
    reason = "cap"
    try:
        while time.time() - t0 < CLIMB_MAX:
            if kb.pause:
                reason = "'n'"; break
            x, y = get_character()
            if y >= 0:
                if y <= PLATFORM_TOP_Y:
                    reason = "reached-platform-y"; break
                if last_y is not None and y >= last_y:   # not climbing anymore
                    stable += 1
                    if stable >= 3:
                        reason = "top-reached(no-more-climb)"; break
                else:
                    stable = 0
                last_y = y
            time.sleep(0.08)
        # up-jump onto the platform (keep Up held, tap Jump)
        if reason != "'n'":
            kb.safe_press(JUMP)
            time.sleep(0.15)
            kb.safe_release(JUMP)
            time.sleep(0.12)
    finally:
        kb.safe_release_all()
    print(f"climb stop reason: {reason}")
    time.sleep(0.5)
    print("after :", get_character())
    Image.fromarray(cv2.cvtColor(_capture(), cv2.COLOR_BGR2RGB)).save(SP + r"\climbjump_after.png")
    print("saved climbjump_after.png")


if __name__ == "__main__":
    lis = Listener(on_press=kb.on_press); lis.start()
    try:
        main()
    finally:
        kb.safe_release_all(); lis.stop()
