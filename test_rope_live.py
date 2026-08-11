"""LIVE single-cycle rope-hang test. SENDS KEY INPUT (moves the character).

Runs exactly ONE rope_hang_break():  align -> down-jump grab -> verify ->
hang a few seconds -> climb+jump back -> verify bounds. Self-recovers if the
grab mislatches. F8 = emergency pause (releases all keys).

Run focused near the game:  python test_rope_live.py
"""
import io
import sys
import time
import ctypes

import win32gui
import win32com.client
from pynput.keyboard import Key, Listener

import keyboard as kb  # keyboard.py: safe_press/release/wait_with_pause/click_press/on_press/pause
from calibrate_live import get_character  # read-only capture + detection

WINDOW_TITLE = "MapleStory Worlds-Artale (?????)"
HANG_SECONDS = 6
CHARACTER_X_L, CHARACTER_X_R = 66, 161


def focus_game():
    hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
    if not hwnd:
        print("[FAIL] game window not found")
        sys.exit(1)
    shell = win32com.client.Dispatch("WScript.Shell")
    shell.SendKeys('%')
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.4)


# ---- load the human-layer rope routines and inject REAL dependencies ----
HL = r"C:\Users\jerry\AppData\Local\Temp\claude\C--jerry-toy-work-maple\76b73c35-1984-400a-a0fc-417f80626173\scratchpad\human_layer.py"
g = {}
exec(compile(io.open(HL, encoding="utf-8").read(), HL, "exec"), g)

panic = {"n": 0}
def _safe_panic():
    kb.safe_release_all()
    panic["n"] += 1
    print("\n*** [PANIC] rope routine lost position -> released all keys, STOPPING. "
          "F8/att'n needed; not auto-clicking. ***\n")

g.update(dict(
    safe_press=kb.safe_press, safe_release=kb.safe_release,
    safe_release_all=kb.safe_release_all, wait_with_pause=kb.wait_with_pause,
    click_press=kb.click_press, get_character=get_character,
    goto_freemarket=_safe_panic, pause=False,
))

HUMAN = g["HUMAN"]
HUMAN["rope_enabled"] = True  # test only

def main():
    print(f"ROPE_MINIMAP_X={HUMAN['ROPE_MINIMAP_X']} PLATFORM_Y={HUMAN['PLATFORM_Y']} "
          f"FALL_DELAY={HUMAN['ROPE_FALL_TO_REGRAB_DELAY']} GRAB_MIN_DROP={HUMAN['ROPE_GRAB_MIN_DROP']}")
    x0, y0 = get_character()
    print(f"start position: ({x0}, {y0})")

    focus_game()
    print("\n>>> CLICK THE GAME WINDOW NOW so it has keyboard focus <<<")
    for i in (5, 4, 3, 2, 1):
        print(f"  starting in {i} ... (F8 to abort)")
        time.sleep(1)

    # --- movement sanity check: confirm keys actually reach the game ---
    xa, ya = get_character()
    kb.click_press(Key.right, 0.35)
    time.sleep(0.3)
    xb, yb = get_character()
    print(f"movement probe: ({xa},{ya}) --Right--> ({xb},{yb})")
    if xa < 0 or xb < 0 or abs(xb - xa) < 1:
        print("[ABORT] character did not move on a Right tap -> keys are NOT reaching "
              "the game (focus problem). No grab attempted.")
        kb.safe_release_all()
        return
    print("keys are reaching the game. Proceeding to rope grab.")

    ok = False
    try:
        ok = g["rope_hang_break"](HANG_SECONDS, CHARACTER_X_L, CHARACTER_X_R)
    finally:
        kb.safe_release_all()

    time.sleep(0.5)
    xf, yf = get_character()
    print(f"\nresult: rope_hang_break -> {ok}")
    print(f"end position: ({xf}, {yf})   panic_calls={panic['n']}")
    in_bounds = xf >= 0 and CHARACTER_X_L - 5 <= xf <= CHARACTER_X_R + 5
    print("character back within patrol bounds:" , in_bounds)


if __name__ == "__main__":
    listener = Listener(on_press=kb.on_press)  # F8 emergency pause
    listener.start()
    try:
        main()
    finally:
        kb.safe_release_all()
        listener.stop()
