import pytesseract
from PIL import Image, ImageOps

# 注意：Tesseract 路徑的設定應在 main.py 或 settings.py 載入時完成
tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = tesseract_path

def preprocess_exp_image(image: Image.Image) -> Image.Image:
    """
    對 EXP 圖片區域進行前處理以優化 OCR 辨識。
    """
    if image is None:
        return None
    image = image.convert("L")  # 轉為灰階
    # 增加對比度，參數2稍微降低對比 (根據原始碼)
    image = ImageOps.autocontrast(image, 2)
    # 避免二值化，讓 Tesseract 自己處理灰階影像
    return image

def run_ocr(image: Image.Image, ocr_type: str) -> str:
    """
    使用指定的設定字串對給定的 PIL 圖片執行 Tesseract OCR。
    ocr_type 應該是 'exp' 或 'coin'。
    返回辨識到的原始文字。
    """
    if image is None:
        return "" # 如果圖片為 None，直接返回空字串

    try:
        # pytesseract.pytesseract.tesseract_cmd 應在 main.py 或 settings.py 載入時設定好
        text = pytesseract.image_to_string(image, config="--psm 7 -c tessedit_char_whitelist=0123456789.%%[]")
        return text.strip() # 移除首尾空白和換行符號
    except pytesseract.TesseractNotFoundError:
        # 這個錯誤理論上應該在 main.py 啟動時就被檢測到，這裡作為一個備份處理
        return ""
    except Exception as e:
        print(f"OCR 辨識時發生錯誤：{e}")
        return ""

# 這個模組只提供函數，沒有 __main__ 區塊
