import time
from pynput.keyboard import Key, Controller, Listener

# --- PyNput 相關設定 (保持不變) ---
keyboard = Controller()
pause = False
currently_pressed = set()  # 記錄目前被按下的鍵

def safe_press(key):
    if key not in currently_pressed:
        keyboard.press(key)
        currently_pressed.add(key)

def safe_release(key):
    if key in currently_pressed:
        keyboard.release(key)
        currently_pressed.remove(key)

def safe_release_all():
    for key in list(currently_pressed): # 迭代副本以避免在迭代時修改集合
        keyboard.release(key)
    currently_pressed.clear()

def wait_with_pause(duration):
    global pause
    start_wait_time = time.time()
    while (time.time() - start_wait_time) < duration:
        if pause:
            print("[暫停中] 釋放所有按鍵")
            safe_release_all()
            while pause:
                time.sleep(0.1) # 暫停時檢查頻率
            print("[恢復]")
            # 由於暫停會中斷計時，這裡需要調整已等待的時間，或者直接中斷當前等待，重新開始移動邏輯
            # 為了簡單起見，我們讓它直接中斷，回到主迴圈重新判斷
            return False # 表示等待未完成，因為被暫停了
        time.sleep(0.01) # 檢查頻率
    return True # 表示等待完成

f9_callback = None   # set by recovery.py to silence alarms on 'm' (was F9)
f10_callback = None  # set by recovery.py to dump the current frame on F10


def on_press(key):
    global pause
    try:
        ch = getattr(key, "char", None)          # letter/number keys expose .char; special keys -> None
        ch = ch.lower() if ch else ch
        if ch == "n":                            # pause / resume toggle (was F8)
            pause = not pause
            print(f"[狀態切換] {'暫停中' if pause else '繼續運行'}")
        elif ch == "m" and f9_callback is not None:   # silence alarms (was F9)
            f9_callback()
        elif key == Key.f10 and f10_callback is not None:  # frame dump (unchanged)
            f10_callback()
    except AttributeError:
        # 非特殊按鍵，例如 'a' 'b' 等
        pass

def click_press(key, duration = 0):
    safe_press(key)
    if duration > 0:
        wait_with_pause(duration)
    safe_release(key)

def attack_during_walk(walk_key, attack_key, duration):
    current = time.time()
    safe_press(walk_key)
    while time.time() - current < duration:
        click_press(attack_key, 0.1)
    safe_release(walk_key)