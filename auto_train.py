import cv2
import numpy as np
import mss
import os
from glob import glob
import pygetwindow as gw
from screeninfo import get_monitors
from PIL import Image
import time
from pynput.keyboard import Key, Controller, Listener
import win32gui
import win32ui
from ctypes import windll
import pyautogui
import sys
from detection import *
from keyboard import *
import ctypes
import win32gui
import win32com
import win32com.client
from exp_processor import ExpProcessor


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

# --- 截圖與偵測函數 (從之前的程式碼複製過來，並將 debug 邏輯移到 main) ---
def capture_window_screenshot(window_title):
    window = gw.getWindowsWithTitle(window_title)
    if len(window) == 0:
        return None, None
    window = window[0]
    left, top, right, bottom = window.left, window.top, window.right, window.bottom
    width = right - left
    height = bottom - top
    monitors = get_monitors()
    for monitor_info in monitors: # 改名避免與 mss.monitor 衝突
        if left >= monitor_info.x and right <= monitor_info.x + monitor_info.width and bottom >= monitor_info.y and top <= monitor_info.y + monitor_info.height:
            try: 
                with mss.mss() as sct:
                    monitor = {"left": left, "top": top, "width": width, "height": height}
                    screenshot = sct.grab(monitor)
                    np_img = np.array(screenshot)
                    np_img = cv2.cvtColor(np_img, cv2.COLOR_BGRA2BGR)
                    img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                    return img, np_img
            except Exception as e:
                print(e)
                sys.exit(1)
    return None, None

# def capture_window_screenshot(window_title=None, hwnd=None):
#     target_hwnd = None

#     if hwnd and win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
#         target_hwnd = hwnd
#     elif window_title:
#         target_hwnd = win32gui.FindWindow(None, window_title)

#     if not target_hwnd:
#         if window_title:
#             print(f"找不到視窗 '{window_title}'，請確保遊戲正在運行並位於螢幕上。")
#         elif hwnd:
#             print(f"提供的 HWND ({hwnd}) 無效或不可見，無法擷取。")
#         return None

#     # --- 獲取視窗客戶區的尺寸和螢幕座標 ---
#     left, top, right, bottom = win32gui.GetClientRect(target_hwnd)
#     width = right - left
#     height = bottom - top

#     # 將客戶區座標轉換為螢幕座標
#     # win32gui.ClientToScreen 將客戶區的 (0,0) 點轉換為螢幕座標
#     screen_left, screen_top = win32gui.ClientToScreen(target_hwnd, (left, top))
#     # screen_right, screen_bottom = win32gui.ClientToScreen(target_hwnd, (right, bottom)) # 不需要這個，因為我們有寬高

#     # 獲取整個螢幕的裝置上下文 (DC)
#     # 為什麼要獲取整個螢幕的 DC？
#     # 因為 BitBlt 是從一個 DC 複製到另一個 DC。如果遊戲直接繪製到螢幕 DC，
#     # 而不是自己的視窗 DC，那麼從螢幕 DC 複製會更有效。
#     # 這是處理某些遊戲（尤其是全螢幕無邊框模式或 DirectX 遊戲）黑屏問題的常見方法。
#     # 注意：這可能會有輕微的性能開銷，因為它擷取了整個螢幕的一部分。
#     hdesktop = win32gui.GetDesktopWindow()
#     desktop_dc = win32gui.GetWindowDC(hdesktop)
#     mfc_dc = win32ui.CreateDCFromHandle(desktop_dc)

#     # 建立一個與螢幕 DC 相容的記憶體 DC
#     save_dc = mfc_dc.CreateCompatibleDC()
#     save_bitmap = win32ui.CreateBitmap()
#     save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
#     save_dc.SelectObject(save_bitmap)

#     # --- 使用 BitBlt 複製像素 ---
#     # 參數: 目標DC, 目標X, 目標Y, 寬度, 高度, 來源DC, 來源X, 來源Y, 複製操作碼 (SRCCOPY)
#     # SRCCOPY = 0x00CC0020 (標準複製)
#     result = windll.gdi32.BitBlt(save_dc.GetSafeHdc(), 0, 0, width, height,
#                                 mfc_dc.GetSafeHdc(), screen_left, screen_top, 0x00CC0020)

#     if not result:
#         print(f"BitBlt failed for HWND: {target_hwnd}.")
#         # 清理資源
#         save_dc.DeleteDC()
#         mfc_dc.DeleteDC()
#         win32gui.ReleaseDC(hdesktop, desktop_dc)
#         return None, None

#     # 從位圖獲取 DIB 數據
#     bmpinfo = save_bitmap.GetInfo()
#     bmpstr = save_bitmap.GetBitmapBits(True)

#     img = Image.frombuffer(
#         'RGB',
#         (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
#         bmpstr, 'raw', 'BGRX', 0, 1
#     )

#     np_img = np.array(img)
#     if img.mode == 'RGB':
#         # OpenCV 的圖片通常是 (高, 寬, 頻道) 且頻道順序是 BGR
#         # numpy 陣列的順序是 (高, 寬, 頻道)
#         # pil_img.mode 'RGB' -> np_img 的頻道順序也是 RGB
#         # 所以從 RGB 轉 BGR 需要 cv2.COLOR_RGB2BGR
#         ocv_img = cv2.cvtColor(np_img, cv2.COLOR_RGB2BGR)
#     else:
#         # 如果不是 RGB，直接使用，或者根據實際情況處理
#         # 這裡假設如果不是 RGB，那可能已經是 BGR 或其他兼容格式
#         # 否則需要根據實際情況調整轉換
#         ocv_img = np_img

#     # 清理資源
#     save_dc.DeleteDC()
#     mfc_dc.DeleteDC()
#     win32gui.ReleaseDC(hdesktop, desktop_dc) # 釋放桌面 DC

#     return img, ocv_img



# --- 主程式邏輯 ---
def main_loop():
    window_title = 'MapleStory Worlds-Artale (?????)'
    
    # 小地圖的固定座標 (需要手動測量) - 這些值你需要根據你的實際情況調整
    MINIMAP_X_OFFSET = 20 # 根據你的螢幕截圖，小地圖左上角的x座標
    MINIMAP_Y_OFFSET = 171 # 小地圖左上角的y座標
    MINIMAP_WIDTH = 229   # 小地圖的寬度
    MINIMAP_HEIGHT = 145  # 小地圖的高度

    minimap_character_template_folder = 'assets/minimap_character/' 
    platform_templates_folder = 'assets/minimap_platforms/' 
    task_arrow_templates_folder = 'assets/task_arrows/' 
    rune_templates_folder = 'assets/runes/'

    # 檢查模板檔案和資料夾是否存在
    if not os.path.exists(minimap_character_template_folder) or not glob(os.path.join(minimap_character_template_folder, '*.png')):
        print(f"錯誤：找不到小地圖角色模板圖片：{minimap_character_template_folder}")
        print("程式將終止。")
        return

    if not os.path.exists(platform_templates_folder) or not glob(os.path.join(platform_templates_folder, '*.png')):
        print(f"請在 '{platform_templates_folder}' 資料夾中放置小地圖平台模板圖片！")
        print("程式將終止。")
        return

    # === 🛠️ 可調整參數區 (用於小地圖移動邏輯) ===
    RUNE_CHECK_INTERVAL = 5 # 每隔 N 秒檢查一次符文 (5分鐘)
    RUNE_ACTIVATION_WAIT_TIME = 2 # 點擊符文後等待符文互動介面出現的時間
    MOVE_THRESHOLD_X_MINIMAP = 35# 角色中心距離平台邊緣在小地圖上多少像素開始移動
    ATTACK_DURATION = 3 # 攻擊按鍵按下的時間（秒），原來的 right_a_duration, left_a_duration
    MOVE_DURATION_PER_STEP = 0.15 # 每次按方向鍵的時長，用於微調
    LOOP_IDLE_TIME = 0.1 # 每輪偵測後的間隔時間
    MINIMAP_Y_PLATFORM_TOLERANCE = 3 # 角色黃點與平台Y軸的容忍度
    TARGET_PLATFORM_HEIGHT = 100
    current_state = 0
    TARGET_ROPE_X = 145
    TARGET_ROPE_Y = 92
    MOVEMENT_THRESHOLD = 2
    # 控制左右巡邏狀態
    # 0: 準備向右移動
    # 1: 向右移動中
    # 2: 準備向左移動
    # 3: 向左移動中
    # 4: 正在攻擊
    # 5: 暫停移動，等待偵測
    # 符文處理的狀態
    # 20: 檢查符文階段
    # 21: 移動到符文階段
    # 22: 點擊符文階段 (然後進入箭頭任務處理)
    # 23: 符文處理完成，冷卻中
    global pause
    current_state = 0 
    last_state = 0
    current_state_time = 0
    current_platform_minimap = None
    character_centers_minimap = None

    TASK_ARROW_SEQUENCE = ['up', 'down', 'left', 'right'] # 根據你的實際任務順序調整
    task_arrow_step = 0 

    last_rune_solve_time = time.time() # 記錄上次解符文的時間
    stop_flag = False
    exp_processor = ExpProcessor()

    while True:
        current_time = time.time()
        # 如果暫停，則不執行任何動作
        if pause:
            safe_release_all()
            time.sleep(0.1)
            continue
        elif stop_flag:
            stop_flag = False
            last_rune_solve_time = time.time()
        if not is_maple_active():
            show_maple()

        pil_img, np_img = capture_window_screenshot(window_title)
        if np_img is None:
            print(f"找不到視窗 '{window_title}'，請確保遊戲正在運行並位於螢幕上。")
            time.sleep(2)
            continue
        #exp process==============================================
        recent_exp_gain = None
        original_width = pil_img.width
        original_height = pil_img.height

        # 應用偏移量
        width_offset = 22
        height_offset = 56
        # 如果寬度是 10 的倍數，偏移量為 0
        if original_width % 10 == 0:
            width_offset = 0
            height_offset = 0

        # 計算應用偏移量後的有效尺寸，用於長寬比匹配
        current_width = original_width - width_offset
        current_height = original_height - height_offset
        # 獲取匹配到的參考解析度和原始裁切區域
        reference_width, reference_height = 1920,1080
        original_exp_region = (1070, 995, 1275, 1030)

        # 根據應用偏移量後的尺寸和參考比例，計算新的裁切座標 (相對於應用偏移量後的左上角)
        try:
            # 計算原始區域相對於參考解析度的比例
            exp_left_prop = original_exp_region[0] / reference_width
            exp_top_prop = original_exp_region[1] / reference_height
            exp_right_prop = original_exp_region[2] / reference_width
            exp_bottom_prop = original_exp_region[3] / reference_height


            # 根據應用偏移量後的尺寸和計算出的比例，計算新的裁切座標
            # 然後再加上偏移量，得到相對於原始螢幕左上角的座標
            new_exp_left = int(current_width * exp_left_prop) + width_offset
            new_exp_top = int(current_height * exp_top_prop) + height_offset
            new_exp_right = int(current_width * exp_right_prop) + width_offset
            new_exp_bottom = int(current_height * exp_bottom_prop) + height_offset

            # 確保裁切座標在原始圖片範圍內且有效 (right > left, bottom > top)
            new_exp_left = max(0, new_exp_left)
            new_exp_top = max(0, new_exp_top)
            new_exp_right = min(original_width, new_exp_right) # 使用原始寬度進行邊界檢查
            new_exp_bottom = min(original_height, new_exp_bottom) # 使用原始高度進行邊界檢查
            width_diff = current_width - reference_width
            height_diff = current_height - reference_height

            # 計算調整量，與尺寸差異成反比，並限制在 +/- 30 範圍內
            # 這裡使用一個簡單的線性映射，例如每差 100 像素調整 1 像素
            adjustment_factor = 0.01 # 每 100 像素差異調整 1 像素 (1/100 = 0.01)
            max_width_adjustment = 30 # 最大調整量為 +/- 30 像素

            # 寬度調整
            raw_adj_x = width_diff * adjustment_factor
            adj_x = int(max(-max_width_adjustment, min(max_width_adjustment, raw_adj_x)))

            # 應用調整到裁切座標
            adjusted_exp_left = new_exp_left + adj_x

            # 確保調整後的裁切座標在原始圖片範圍內且有效 (right > left, bottom > top)
            new_exp_left = max(0, adjusted_exp_left)

            exp_cropped_image = pil_img.crop((adjusted_exp_left, new_exp_top, new_exp_right, new_exp_bottom))
            recent_exp_gain, exp_processed_image = exp_processor.process_exp_value(exp_cropped_image, n=3)
            #=============================================
        except Exception as e:
            # 裁切失敗時打印錯誤信息
            print(f"根據比例計算或裁切圖片時發生錯誤: {e}")

        # 裁剪小地圖區域
        minimap_end_x = MINIMAP_X_OFFSET + MINIMAP_WIDTH
        minimap_end_y = MINIMAP_Y_OFFSET + MINIMAP_HEIGHT
        minimap_end_x = min(minimap_end_x, np_img.shape[1])
        minimap_end_y = min(minimap_end_y, np_img.shape[0])
        minimap_img = np_img[MINIMAP_Y_OFFSET:minimap_end_y, MINIMAP_X_OFFSET:minimap_end_x]
        
        if minimap_img.size == 0:
            print("裁剪的小地圖區域為空。")
            print(f"原始螢幕尺寸: {np_img.shape}")
            print(f"嘗試裁剪範圍: Y({MINIMAP_Y_OFFSET}:{minimap_end_y}), X({MINIMAP_X_OFFSET}:{minimap_end_x})")
            time.sleep(LOOP_IDLE_TIME)
            continue
        
        # 進行偵測
        character_centers_minimap = detect_character_on_minimap(minimap_img, 
                                                            templates_folder=minimap_character_template_folder, 
                                                            threshold=0.75,)
                                                            # debug=True)

        platform_rects_minimap = detect_platforms_on_minimap(minimap_img, 
                                                           templates_folder=platform_templates_folder, 
                                                           threshold=0.65,)
        
        # 繪圖到 minimap_img 上 (用於顯示)
        display_minimap_img = minimap_img.copy() 

        for (px1, py1, px2, py2) in platform_rects_minimap:
            cv2.rectangle(display_minimap_img, (px1, py1), (px2, py2), (255, 0, 0), 1)

        if character_centers_minimap:
            char_minimap_center_x, char_minimap_center_y = character_centers_minimap[0]
            print(char_minimap_center_x, char_minimap_center_y)
            cv2.circle(display_minimap_img, (char_minimap_center_x, char_minimap_center_y), 3, (0, 255, 0), -1)

        cv2.imshow("Detected Minimap (Press 'q' to quit, any other key to continue)", display_minimap_img)
        key = cv2.waitKey(1) # 等待1毫秒，讓視窗有時間更新
        if key == ord('q'): 
            cv2.destroyAllWindows()
            safe_release_all() # 確保結束時釋放所有按鍵
            print("使用者按下 'q' 鍵，程式結束。")
            return
        
        # === 符文偵測觸發 ===
        # if current_state < 20 and (time.time() - last_rune_check_time) >= RUNE_CHECK_INTERVAL:
        #     print(f"時間到，檢查符文生成... (上次檢查時間: {time.strftime('%H:%M:%S', time.localtime(last_rune_check_time))})")
        #     last_state = current_state
        #     current_state = 20 # 進入檢查符文階段
        #     safe_release_all() # 確保沒有按鍵按著

        if current_state >= 0 and current_state <= 3:
            # --- 角色移動與攻擊邏輯 ---
            if not character_centers_minimap:
                print("小地圖上未偵測到角色黃點，停止移動，等待下一次偵測...")
                # safe_release_all()
                last_move_direction = None
                time.sleep(LOOP_IDLE_TIME)
                continue

            char_minimap_center_x, char_minimap_center_y = character_centers_minimap[0]

            current_platform_minimap = None
            for (px1, py1, px2, py2) in platform_rects_minimap:
                # 判斷角色黃點的Y座標是否在平台區域的Y範圍內
                # 以及X座標是否在平台區域的X範圍內
                if (char_minimap_center_y >= py1 - MINIMAP_Y_PLATFORM_TOLERANCE and 
                    char_minimap_center_y <= py2 + MINIMAP_Y_PLATFORM_TOLERANCE and
                    char_minimap_center_x >= px1 and 
                    char_minimap_center_x <= px2):
                    current_platform_minimap = (px1, py1, px2, py2)
                    break

            if current_platform_minimap:
                px1, py1, px2, py2 = current_platform_minimap
                print(f"平台範圍:{px1, py1, px2, py2}, 最近經驗:{recent_exp_gain}, 符文後時間:{int(current_time-last_rune_solve_time)}")
                
                # 判斷是否需要向右移動
                if current_state == 0 or current_state == 1: # 準備向右或正在向右
                    if char_minimap_center_x < px2 - MOVE_THRESHOLD_X_MINIMAP:
                        # 還未到達右邊緣，繼續向右移動
                        safe_release(Key.left) # 確保左鍵被釋放
                        safe_release('a') # 確保攻擊鍵被釋放
                        safe_press(Key.right)
                        last_move_direction = Key.right
                        current_state = 1 # 進入向右移動中狀態
                        # print(f"向右移動中... 角色X: {char_minimap_center_x}, 平台右緣: {px2}")
                    else:
                        # 到達右邊緣，準備攻擊並轉向左
                        print(f"到達平台右邊緣 ({char_minimap_center_x} >= {px2 - MOVE_THRESHOLD_X_MINIMAP})，開始攻擊並轉向左。")
                        safe_release(Key.right) # 停止向右
                        safe_release(Key.left) # 確保左鍵沒被按著
                        click_press('c', 1)
                        click_press(Key.left, 0.5)
                        safe_press('a') # 按下攻擊鍵
                        # 持續攻擊 ATTACK_DURATION 秒
                        if not wait_with_pause(ATTACK_DURATION): # 如果在等待中被暫停，跳過此次循環
                            continue 
                        safe_release('a') # 釋放攻擊鍵
                        current_state = 2 # 準備向左移動
                        last_move_direction = Key.left # 準備向左移動

                # 判斷是否需要向左移動
                elif current_state == 2 or current_state == 3: # 準備向左或正在向左
                    if char_minimap_center_x > px1 + MOVE_THRESHOLD_X_MINIMAP:
                        # 還未到達左邊緣，繼續向左移動
                        safe_release(Key.right) # 確保右鍵被釋放
                        safe_release('a') # 確保攻擊鍵被釋放
                        safe_press(Key.left)
                        last_move_direction = Key.left
                        current_state = 3 # 進入向左移動中狀態
                        # print(f"向左移動中... 角色X: {char_minimap_center_x}, 平台左緣: {px1}")
                    else:
                        # 到達左邊緣，準備攻擊並轉向右
                        print(f"到達平台左邊緣 ({char_minimap_center_x} <= {px1 + MOVE_THRESHOLD_X_MINIMAP})，開始攻擊並轉向右。")
                        safe_release(Key.left) # 停止向左
                        safe_release(Key.right) # 確保右鍵沒被按著
                        click_press('c', 1)
                        click_press(Key.right, 0.5)
                        safe_press('a') # 按下攻擊鍵
                        # 持續攻擊 ATTACK_DURATION 秒
                        if not wait_with_pause(ATTACK_DURATION): # 如果在等待中被暫停，跳過此次循環
                            continue
                        safe_release('a') # 釋放攻擊鍵
                        current_state = 0 # 準備向右移動
                        last_move_direction = Key.right # 準備向右移動
                
                else: # 預設或未知狀態，回歸準備向右
                    current_state = 0
                    safe_release_all() # 確保沒有按鍵被按著

            else: # 未偵測到在任何已知平台上
                print("小地圖上未偵測到角色在任何已知的平台上，可能需要跳躍或尋找平台。")
                safe_release_all() # 停止所有移動和攻擊
                last_move_direction = None 
                # 這裡可以添加跳躍尋找平台的邏輯，例如：
                # pyautogui.press('alt') # 假設跳躍鍵
                # time.sleep(0.5) # 跳躍後等待一會兒
                # 或者，如果角色掉下去了，可能需要重置狀態或執行回到安全區域的邏輯
                # 暫時保持靜止，讓下一次循環重新偵測
        
        # === 符文處理新狀態 ===
        elif current_state == 20: # 檢查符文階段
            print("檢查符文生成中...")
            detected_runes = detect_rune(np_img, templates_folder=rune_templates_folder, threshold=0.5, debug=True)
            
            if detected_runes:
                print(f"偵測到符文！移動到符文位置。")
                current_state = last_state # 進入移動到符文階段
                # 這裡我們只取第一個偵測到的符文，如果有多個符文，可能需要更複雜的選擇邏輯
                target_rune_pos = (detected_runes[0][0] + (detected_runes[0][2] - detected_runes[0][0]) // 2, 
                                   detected_runes[0][1] + (detected_runes[0][3] - detected_runes[0][1]) // 2)
                # 符文位置是螢幕絕對座標
                # 你需要將角色移動到這個螢幕座標附近，這涉及到遊戲內的尋路邏輯
                # 最簡單的方式是模擬方向鍵直到角色位於符文X座標附近，然後模擬跳躍
                print(f"目標符文中心座標: {target_rune_pos}")
                
                # --- 符文移動簡化邏輯 ---
                # 這裡需要根據你的遊戲地圖和角色移動特性來實現
                
                # 假設移動到X軸附近後，直接進入點擊階段
                # current_state = 22 # 進入點擊符文階段
                # 符文的 Y 軸處理通常是跳躍或傳送，這裡暫時不模擬複雜的跳躍
                
            else:
                print("未偵測到符文，返回平台巡邏。")
                current_state = last_state # 返回平台巡邏
                last_rune_check_time = time.time() # 未偵測到，重新計時
                
        elif current_state == 21: # 移動到符文階段 (這是一個佔位符，因為符文移動邏輯複雜，可能需要你的自定義實現)
            print("移動到符文位置 (此處需要更詳細的尋路邏輯)...")
            # 在這裡，你應該實現複雜的尋路邏輯，例如：
            # 1. 根據小地圖上符文和角色的相對位置，計算出需要移動的方向和距離。
            # 2. 模擬跳躍、爬繩、傳送等操作以到達符文。
            # 3. 判斷角色是否已到達符文位置。
            # 當到達後，切換到 current_state = 22
            
            # 簡化：假設已經通過之前的簡單移動到了X軸附近
            current_state = 22 # 進入點擊符文階段
            time.sleep(0.5) # 短暫等待

        elif current_state == 22: #處理任務箭頭階段 (符文處理中)
            print("點擊符文...")
            safe_release_all() # 確保沒有按鍵按著
            if task_arrow_step < len(TASK_ARROW_SEQUENCE):
                target_direction = TASK_ARROW_SEQUENCE[task_arrow_step]
                print(f"準備點擊第 {task_arrow_step + 1} 個箭頭: {target_direction}")
                
                # 重新偵測一次，確保箭頭存在且在正確位置
                current_arrows = detect_task_arrow(np_img, templates_folder=task_arrow_templates_folder, threshold=0.75, debug=False)
                
                found_target_arrow = None
                for arrow_info in current_arrows:
                    if arrow_info[4] == target_direction:
                        found_target_arrow = arrow_info
                        break

                if found_target_arrow:
                    # 無需點擊符文上的箭頭，而是按下對應的方向鍵
                    print(f"  模擬按下方向鍵: {target_direction}")
                    
                    if target_direction == 'up':
                        pyautogui.press('up')
                    elif target_direction == 'down':
                        pyautogui.press('down')
                    elif target_direction == 'left':
                        pyautogui.press('left')
                    elif target_direction == 'right':
                        pyautogui.press('right')
                    else:
                        print(f"未知箭頭方向: {target_direction}")

                    time.sleep(0.5) 
                    
                    task_arrow_step += 1 

                else:
                    print(f"未偵測到目標箭頭 '{target_direction}'。可能已完成或順序有誤，返回檢查階段。")
                    current_state = 10 # 如果沒找到，返回檢查階段，重新偵測所有箭頭
            else:
                print("所有任務箭頭已處理完成。符文解鎖成功。")
                current_state = 0 # 返回平台巡邏
                task_arrow_step = 0
                last_rune_check_time = time.time() # 符文處理結束，重新計時
        elif current_state == 33:
            if char_minimap_center_x > TARGET_ROPE_X - MOVEMENT_THRESHOLD:
                current_state = 34
                continue
            char_minimap_center_x, char_minimap_center_y = character_centers_minimap[0]
            while char_minimap_center_x < TARGET_ROPE_X - MOVEMENT_THRESHOLD:
                # 還未到達右邊緣，繼續向右移動
                safe_release(Key.left) # 確保左鍵被釋放
                safe_release('a') # 確保攻擊鍵被釋放
                safe_press(Key.right)
                pil_img, np_img = capture_window_screenshot(window_title)
                minimap_end_x = MINIMAP_X_OFFSET + MINIMAP_WIDTH
                minimap_end_y = MINIMAP_Y_OFFSET + MINIMAP_HEIGHT
                minimap_end_x = min(minimap_end_x, np_img.shape[1])
                minimap_end_y = min(minimap_end_y, np_img.shape[0])
                minimap_img = np_img[MINIMAP_Y_OFFSET:minimap_end_y, MINIMAP_X_OFFSET:minimap_end_x]
                character_centers_minimap = detect_character_on_minimap(minimap_img, 
                                                        templates_folder=minimap_character_template_folder, 
                                                        threshold=0.75,)
                char_minimap_center_x, char_minimap_center_y = character_centers_minimap[0]
                print(f"目前角色位置: {char_minimap_center_x},{char_minimap_center_y}")
            safe_release(Key.right)
            current_state = 35
        elif current_state == 34:
            if char_minimap_center_x < TARGET_ROPE_X - MOVEMENT_THRESHOLD:
                current_state = 33
                continue
            while abs(char_minimap_center_x-TARGET_ROPE_X) > MOVEMENT_THRESHOLD:
                # 還未到達右邊緣，繼續向右移動
                safe_release(Key.right) # 確保左鍵被釋放
                safe_release('a') # 確保攻擊鍵被釋放
                safe_press(Key.left)
                pil_img, np_img = capture_window_screenshot(window_title)
                minimap_end_x = MINIMAP_X_OFFSET + MINIMAP_WIDTH
                minimap_end_y = MINIMAP_Y_OFFSET + MINIMAP_HEIGHT
                minimap_end_x = min(minimap_end_x, np_img.shape[1])
                minimap_end_y = min(minimap_end_y, np_img.shape[0])
                minimap_img = np_img[MINIMAP_Y_OFFSET:minimap_end_y, MINIMAP_X_OFFSET:minimap_end_x]
                character_centers_minimap = detect_character_on_minimap(minimap_img, 
                                                        templates_folder=minimap_character_template_folder, 
                                                        threshold=0.75,)
                char_minimap_center_x, char_minimap_center_y = character_centers_minimap[0]
                print(f"目前角色位置: {char_minimap_center_x},{char_minimap_center_y}")
            safe_release(Key.left)
            current_state = 35
        elif current_state == 35:
            safe_press(Key.alt)
            safe_press(Key.up)
            while abs(char_minimap_center_y-TARGET_ROPE_Y) > MOVEMENT_THRESHOLD:
                pil_img, np_img = capture_window_screenshot(window_title)
                minimap_end_x = MINIMAP_X_OFFSET + MINIMAP_WIDTH
                minimap_end_y = MINIMAP_Y_OFFSET + MINIMAP_HEIGHT
                minimap_end_x = min(minimap_end_x, np_img.shape[1])
                minimap_end_y = min(minimap_end_y, np_img.shape[0])
                minimap_img = np_img[MINIMAP_Y_OFFSET:minimap_end_y, MINIMAP_X_OFFSET:minimap_end_x]
                character_centers_minimap = detect_character_on_minimap(minimap_img, 
                                                        templates_folder=minimap_character_template_folder, 
                                                        threshold=0.75,)
                char_minimap_center_x, char_minimap_center_y = character_centers_minimap[0]
                print(f"目前角色位置: {char_minimap_center_x},{char_minimap_center_y}")
                if abs(char_minimap_center_x - TARGET_ROPE_X) > MOVEMENT_THRESHOLD and char_minimap_center_y > 108:
                    safe_release_all()
                    current_state = 33
                    break
                if char_minimap_center_y < 100:
                    safe_release(Key.alt)
            safe_release(Key.alt)
            safe_release(Key.up)
            current_state = 0
            continue
                
        if current_platform_minimap != None and current_platform_minimap[1] > TARGET_PLATFORM_HEIGHT and current_state < 30:
            current_state = 33 # 調整角色位置

        if current_state != last_state:
            last_state = current_state
            current_state_time = 0
        else:
            end_time = time.time()
            current_state_time += (end_time - current_time)
            current_time = end_time
            if current_state_time > 20:
                current_state_time = 0
                current_state = 0
        if current_time - last_rune_solve_time > 60*28 and recent_exp_gain < 100:
            pause = True
            stop_flag = True
            safe_release_all()
            print(f"怕怕...暫停一下")
            time.sleep(22)
            button = 'left'
            clicks = 2
            x_coord = 1534 # 2581
            y_coord = 1800 # 1766

            pyautogui.moveTo(x_coord, y_coord, duration=0.5)
            print("自由市場GOGO")
            # 點擊滑鼠
            pyautogui.click(x=x_coord, y=y_coord, clicks=clicks, interval=0.25, button=button)
            continue
        time.sleep(LOOP_IDLE_TIME) # 每輪偵測後等待

# --- 主入口點 ---
if __name__ == "__main__":
    print("自動巡邏與攻擊啟動中，按 'n' 可暫停 / 恢復。")
    # 建議先等待幾秒，讓你有時間切換到遊戲視窗
    time.sleep(3) 

    listener = Listener(on_press=on_press)
    listener.start() # 啟動監聽線程

    try:
        main_loop()
    except KeyboardInterrupt:
        print("程式被使用者中斷。")
    finally:
        safe_release_all() # 確保程式結束時釋放所有按鍵
        cv2.destroyAllWindows() # 關閉所有OpenCV視窗
        listener.stop() # 停止監聽線程
        listener.join() # 等待監聽線程結束