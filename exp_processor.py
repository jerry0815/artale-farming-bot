import time
import ocr_processor
from collections import deque
import re

class ExpProcessor:
    """
    處理核心應用程式邏輯：擷取、OCR、解析、追蹤增益、錯誤統計和計算平均值。
    不包含任何 GUI 相關的邏輯。
    """
    def __init__(self):

        # 歷史數據 (儲存 (時間戳, 增益, EXP百分比)) - 使用 list 記錄所有歷史，用於最終報告
        self.exp_history = []
        self.gain_reasonability_multiplier = 3.0
        self.min_history_for_dynamic = 10
        self.initial_max_exp_gain = 50000

        # 新增：用於記錄最近合理增益的隊列，用於動態合理性判斷
        self._recent_reasonable_exp_gains = deque(maxlen=200)

        # 新增：記錄已累積的合理增益數量
        self._reasonable_exp_gain_count = 0

        # 狀態變數
        self.start_time = time.time()
        # 儲存上一次成功解析的絕對數值
        self.last_exp_value = None

        # 累計總增益和錯誤統計
        self.total_exp_gain = 0.0 # EXP 增益使用浮點數

        self.exp_total_attempts = 0
        self.exp_total_errors = 0

        # 可選：儲存錯誤日誌以便除錯
        self.error_logs = [] # 儲存解析錯誤的詳細資訊

        # 儲存最近一次處理後的圖片供 GUI 顯示
        self.last_exp_processed_image = None

        # 新增旗標，用於追蹤是否是第一次嘗試截圖
        self._is_first_capture = True

    def get_last_n_minutes_gain(self, n=10):
        """
        計算或估計最近 n 分鐘 (600 秒) 的 EXP 或 金幣 獲取量。
        """
        current_time = time.time()
        history = self.exp_history

        # 修改列表推導式，根據是經驗值還是金幣歷史來正確解包
        recent_gains = [(ts, gain) for ts, gain in history if ts >= current_time - 60*n] # 解包三個值，但只取前兩個

        # 計算最近 n 分鐘的實際累積增益
        actual_recent_gain = sum(gain for _, gain in recent_gains)

        return actual_recent_gain

    
    def parse_exp_data(self, raw_text: str):
        """
        從 OCR 原始文字中解析並修正 EXP 數字和百分比。
        返回 (exp_number_str, exp_percent_str) 元組，如果解析失敗則返回 (None, None)。
        """
        text = raw_text.replace('\n', '').replace(' ', '') # 清理常見的噪音

        # 嘗試辨識正常格式：exp數字 + 分隔符號 + 百分比數字 + '%'
        # 匹配一個或多個數字 (\d+), 後面跟著零個或多個非數字字元 ([^\d]*),
        # 再跟著一個或多個數字 (\d+), 可選地跟著一個點、逗號或分號 ([.,:;])?,
        # 再可選地跟著一到兩位數字 (\d{1,2})?, 最後是零個或多個空白 (\s*) 和百分號 (%)
        # 修正 regex 以更精確地匹配百分比部分
        match = re.search(r"(\d+)[^\d]*(\d+)([.,:;])?(\d{1,2})?\s*%", text)
        if match:
            exp_number = match.group(1)
            left = match.group(2) # 百分比的整數部分
            right = match.group(4) or "00" # 百分比的小數部分，如果沒有則默認為 "00"

            # 確保小數部分有兩位，不足補零，超過截斷
            while len(right) < 2:
                right += '0'
            right = right[:2]

            exp_percent = f"{left}.{right}"
            return exp_number, exp_percent

        # 嘗試抓取格式錯誤但只有兩段數字的狀況（如 "5568" 但原本是 "55.68%"）
        # 這裡假設第一個數字是 exp 數字，第二個數字（或部分）是百分比
        percent_match = re.findall(r"\d+", text)
        if len(percent_match) >= 2:
            exp_number = percent_match[0]
            percent_raw = percent_match[1]
            # 嘗試將第二段數字解釋為百分比的小數部分
            if len(percent_raw) >= 2: # 至少有兩位才可能表示 xx.yy
                # 取最後兩位作為小數部分，前面的作為整數部分
                left = percent_raw[:-2] if len(percent_raw) > 2 else '0' # 如果只有兩位，整數部分是0
                right = percent_raw[-2:]

                # 確保小數部分有兩位，不足補零，超過截斷
                while len(right) < 2:
                    right += '0'
                right = right[:2]

                exp_percent = f"{left}.{right}"
                return exp_number, exp_percent
            elif len(percent_raw) == 1: # 如果只有一位，可能是 xx.y 這種情況，補零為 xx.y0
                # 這裡假設單個數字是整數部分，小數點後為 00
                exp_percent = f"{percent_raw}.00" # 假設是個位數，例如 5 -> 5.00
                return exp_number, exp_percent
            # 如果 percent_raw 長度 < 1，則不構成百分比，這裡不再處理

        # 未能找到可辨識的模式，解析失敗
        return None, None

    def _is_exp_gain_reasonable(self, gain):
        """
        根據最近的成功 EXP 增益歷史或初始固定閾值，判斷本次增益是否合理。
        返回 True 表示合理，False 表示不合理。
        """
        if gain < 0:
            return False # 負增益總是不合理 (除非遊戲機制允許)

        # 如果累積的合理增益數量不足，使用初始固定閾值
        if self._reasonable_exp_gain_count < self.min_history_for_dynamic:
            return gain <= self.initial_max_exp_gain

        # 如果累積的合理增益數量足夠，使用動態閾值
        # 計算最近合理增益的最大值，避免除以零或空隊列
        if not self._recent_reasonable_exp_gains:
            # 如果因為某種原因隊列為空，回退到初始閾值判斷，並打印警告
            print("警告：合理 EXP 增益隊列為空，但已達到最小歷史數量，回退到初始閾值判斷。")
            return gain <= self.initial_max_exp_gain

        recent_max_gain = max(self._recent_reasonable_exp_gains)
        # 避免最大值為零導致閾值為零 (如果遊戲有最小增益，這裡需要調整)
        dynamic_threshold = max(recent_max_gain, 1) * self.gain_reasonability_multiplier # 確保至少有一個最小閾值

        # 如果本次增益超過動態閾值，則認為不合理
        return gain <= dynamic_threshold

    def process_exp_value(self, exp_img_raw, n=3):
        exp_val = None
        # 前處理和 OCR
        exp_processed_image = ocr_processor.preprocess_exp_image(exp_img_raw)
        exp_raw_text = ocr_processor.run_ocr(exp_processed_image, 'exp')

        # 解析數據
        exp_num_str, exp_pct_str = self.parse_exp_data(exp_raw_text)
        # print(f"Debug: {exp_num_str}, {exp_pct_str}")
        # 嘗試將解析到的字串轉換為數值
        try:
            if exp_num_str:
                exp_val = int(exp_num_str)
        except (ValueError, TypeError):
            # 轉換失敗，保持為 None
            pass
        # 計算 EXP 獲取量 (如果本次和上一次的值都成功解析)
        if exp_val is not None: # 只要本次成功解析，就嘗試計算增益並更新 last_value
            if self.last_exp_value is not None and self.last_exp_value != exp_val:
                exp_gain = exp_val - self.last_exp_value
                # 判斷增益是否合理 (使用新的判斷方法)
                if exp_gain > 0 and self._is_exp_gain_reasonable(exp_gain):
                    # 增益合理，累計到總量並記錄歷史
                    self.total_exp_gain += exp_gain
                    self.exp_history.append((time.time(), exp_gain))
                    self._recent_reasonable_exp_gains.append(exp_gain)
                    self._reasonable_exp_gain_count += 1 # 增加合理增益計數

                    # 增益合理，更新 last_exp_value
                    self.last_exp_value = exp_val
                else:
                    # 重要：即使增益異常，只要本次 exp_val 成功解析，就更新 last_exp_value
                    # 這樣可以讓程式盡快從前一個錯誤值中恢復
                    self.last_exp_value = exp_val
                    print(f"檢測到異常 EXP 增益 ({exp_gain:.2f})。原始文字：'{exp_raw_text}'") # 避免頻繁打印

            else:
                self.last_exp_value = exp_val

        recent_exp_gain = self.get_last_n_minutes_gain(n=n)
        # return recent_exp_gain, exp_processed_image
        return [recent_exp_gain, exp_num_str, exp_pct_str], exp_processed_image