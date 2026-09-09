import time
from pynput.keyboard import Key, Controller, Listener
import ctypes
import win32gui
import win32com
import win32com.client

keyboard = Controller()
pause = False
currently_pressed = set()  # 記錄目前被按下的鍵

# === 🛠️ 可調整參數區 ===
left_duration = 2
right_a_duration = 1.5
right_duration = 2
left_a_duration = 1.5
cycle_time = 17
check_interval = 0.05
repeat_time = 8
# ======================

def get_maple_window(maple_name = "MapleStory Worlds-Artale (?????)"):
    return win32gui.FindWindow(None, maple_name)

def is_maple_found():
    if get_maple_window() == 0:
        return False
    else:
        return True

def is_maple_active(maple_name = "MapleStory Worlds-Artale (?????)"):
    window_id = win32gui.FindWindow(None, maple_name)
    foreground_id = win32gui.GetForegroundWindow()
    if foreground_id == window_id:
        return True
    else:
        return False
    
def show_maple():
    if is_maple_found() is False:
        return
    else:
        shell = win32com.client.Dispatch("WScript.Shell")  # not sure if helps
        shell.SendKeys('%')
        # win32gui.SetForegroundWindow(try_get_window())
        ctypes.windll.user32.SetForegroundWindow(get_maple_window()) 
        ctypes.windll.user32.ShowWindow(get_maple_window(), 9)  # SW_RESTORE = 9
        safe_release_all()

def safe_press(key):
    if key not in currently_pressed:
        keyboard.press(key)
        currently_pressed.add(key)

def safe_release_all():
    for key in list(currently_pressed):
        keyboard.release(key)
    currently_pressed.clear()

def wait_with_pause(duration):
    global pause
    elapsed = 0
    while elapsed < duration:
        if pause:
            print("[暫停中] 釋放所有按鍵")
            safe_release_all()
            while pause:
                time.sleep(0.1)
            print("[恢復]")
            break  # 中斷當前動作，恢復後重新進入主迴圈
        time.sleep(min(check_interval, duration - elapsed))
        elapsed += check_interval

def press_key_with_pause(key, duration):
    safe_press(key)
    wait_with_pause(duration)
    safe_release_all()

def on_press(key):
    global pause
    ch = getattr(key, "char", None)              # 'n' pause/resume toggle (was F8)
    if ch and ch.lower() == "n":
        pause = not pause
        print(f"[狀態切換] {'暫停中' if pause else '繼續運行'}")

def movement_loop():
    while True:
        if not is_maple_active():
            show_maple()
        start_time = time.time()

        print(f"← for {left_duration}s")
        for _ in range(repeat_time):
            press_key_with_pause(Key.left, left_duration)

            print(f"→ and hold A for {right_a_duration}s")
            safe_press('a')
            press_key_with_pause(Key.right, right_a_duration)
            safe_release_all()  # 確保 'a' 被釋放

        print(f"→ for {right_duration}s")
        for _ in range(repeat_time):
            press_key_with_pause(Key.right, right_duration)

            print(f"← and hold A for {left_a_duration}s")
            safe_press('a')
            press_key_with_pause(Key.left, left_a_duration)
            safe_release_all()

        # elapsed = time.time() - start_time
        # if elapsed < cycle_time:
        #     wait_with_pause(cycle_time - elapsed)

if __name__ == "__main__":
    print("自動操作啟動中，按 'n' 可暫停 / 恢復")
    time.sleep(3)

    listener = Listener(on_press=on_press)
    listener.start()

    movement_loop()
