import cv2
import numpy as np
import os
from glob import glob

def apply_nms(rects, overlapThresh=0.3):
    if len(rects) == 0:
        return []

    boxes = np.array(rects)
    x1 = boxes[:,0]
    y1 = boxes[:,1]
    x2 = boxes[:,2]
    y2 = boxes[:,3]
    areas = (x2 - x1) * (y2 - y1)
    order = np.argsort(y2)
    keep = []

    while len(order) > 0:
        i = order[-1]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[:-1]])
        yy1 = np.maximum(y1[i], y1[order[:-1]])
        xx2 = np.minimum(x2[i], x2[order[:-1]])
        yy2 = np.minimum(y2[i], y2[order[:-1]])

        w = np.maximum(0, xx2 - xx1)
        h = np.maximum(0, yy2 - yy1)

        inter = w * h
        iou = inter / (areas[i] + areas[order[:-1]] - inter)

        order = order[np.where(iou <= overlapThresh)[0]]

    return boxes[keep].astype(int).tolist()
# 偵測小地圖上的角色 (黃色點點) - 改為模板匹配
def detect_character_on_minimap(minimap_img, templates_folder='assets/minimap_character', threshold=0.75, debug=False):
    """
    在小地圖上偵測角色 (黃色點點) 使用模板匹配。
    
    Args:
        minimap_img (np.array): 小地圖的影像 (BGR格式)。
        template_path (str): 角色黃點模板圖片的路徑。
        threshold (float): 模板匹配的相似度閾值。
        debug (bool): 是否儲存偵測結果圖。
        
    Returns:
        list: 角色點的中心座標列表，每個元素為 [center_x, center_y]。
    """
    template_paths = glob(os.path.join(templates_folder, '*.png'))
    
    if not template_paths:
        print(f"警告：'{templates_folder}' 資料夾中沒有找到小地圖角色模板模板圖片。")
        return []

    character_centers = []
    while len(character_centers) == 0:
        for template_path in template_paths:
            dot_template = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if dot_template is None:
                print(f"警告：無法讀取小地圖角色模板圖片：{template_path}")
                continue

            h_tpl, w_tpl = dot_template.shape[:2]
            if dot_template.shape[0] > minimap_img.shape[0] or dot_template.shape[1] > minimap_img.shape[1]:
                return []

            # 使用模板匹配
            result = cv2.matchTemplate(minimap_img, dot_template, cv2.TM_CCOEFF_NORMED)
            loc = np.where(result >= threshold)
            all_dot_rects = []
            for pt in zip(*loc[::-1]):
                all_dot_rects.append([pt[0], pt[1], pt[0] + w_tpl, pt[1] + h_tpl])
            final_dot_rects = apply_nms(all_dot_rects, overlapThresh=0.1)
            for (x1, y1, x2, y2) in final_dot_rects:
                cX = (x1 + x2) // 2
                cY = (y1 + y2) // 2
                character_centers.append([cX, cY])
            if len(character_centers) > 0:
                break
        if debug:
            print(f"偵測到 {len(character_centers)} 個黃點，NMS前: {len(all_dot_rects)}")
        threshold -= 0.1

    if debug:
        debug_img = minimap_img.copy()
        for cX, cY in character_centers:
            # 畫出偵測到的黃點中心，用綠色圓點標示以區分原黃點
            cv2.circle(debug_img, (cX, cY), max(2, w_tpl//2), (0, 255, 0), -1) 
        os.makedirs("debug_output", exist_ok=True)
        output_path = os.path.join("debug_output", "minimap_character_detected.png")
        cv2.imwrite(output_path, debug_img)
        print(f"小地圖角色偵測結果圖已輸出：{output_path}，偵測到 {len(character_centers)} 個黃點，NMS前: {len(all_dot_rects)}")

    return character_centers


def detect_yellow_dot(minimap_img, templates_folder='assets/minimap_character', threshold=0.7):
    template_paths = glob(os.path.join(templates_folder, '*.png'))
    
    best_match = None
    max_val = -1
    
    for t_path in template_paths:
        tpl = cv2.imread(t_path)
        if tpl is None: continue
        h, w = tpl.shape[:2]
        
        res = cv2.matchTemplate(minimap_img, tpl, cv2.TM_CCOEFF_NORMED)
        _, current_max_val, _, current_max_loc = cv2.minMaxLoc(res)
        
        # 紀錄所有模板中，信心值最高的那一個
        if current_max_val > max_val:
            max_val = current_max_val
            best_match = {
                'pos': (current_max_loc[0] + w // 2, current_max_loc[1] + h // 2),
                'score': current_max_val
            }

    # 檢查最高分是否過門檻
    if best_match and best_match['score'] >= threshold:
        return [best_match['pos']] # 回傳格式統一為 list
    
    return []

def detect_red_dots(minimap_img, templates_folder='assets/minimap_other_character', threshold=0.7, debug=False):
    template_paths = glob(os.path.join(templates_folder, '*.png'))
    
    all_rects = [] # 用來存儲所有模板偵測到的框
    
    for t_path in template_paths:
        tpl = cv2.imread(t_path)
        if tpl is None: continue
        h, w = tpl.shape[:2]
        
        res = cv2.matchTemplate(minimap_img, tpl, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        
        for pt in zip(*loc[::-1]):
            # 格式：[x1, y1, x2, y2]
            all_rects.append([pt[0], pt[1], pt[0] + w, pt[1] + h])

    if not all_rects:
        return []

    # 關鍵：將所有模板的結果統一進行 NMS，避免同一個紅點被多個模板重複偵測
    final_rects = apply_nms(np.array(all_rects), overlapThresh=0.3)
    
    red_centers = []
    for (x1, y1, x2, y2) in final_rects:
        red_centers.append([(x1 + x2) // 2, (y1 + y2) // 2])
        
    if debug:
        print(f"紅點偵測完成：共找到 {len(red_centers)} 個敵軍")
        
    return red_centers

def detect_platforms_on_minimap(minimap_img, templates_folder='assets/minimap_platforms/', threshold=0.7):
    all_rects = []
    template_paths = glob(os.path.join(templates_folder, '*.png'))
    if not template_paths:
        return []
    for template_path in template_paths:
        tpl = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if tpl is None:
            continue
        h, w = tpl.shape[:2]
        result = cv2.matchTemplate(minimap_img, tpl, cv2.TM_CCOEFF_NORMED)
        loc = np.where(result >= threshold)
        for pt in zip(*loc[::-1]):
            all_rects.append([pt[0], pt[1], pt[0] + w, pt[1] + h])
    final_rects = apply_nms(all_rects, overlapThresh=0.2)
    return final_rects

# 偵測符文
def detect_rune(game_img, templates_folder='assets/runes/', threshold=0.8, debug=False):
    """
    在遊戲畫面上偵測符文。
    
    Args:
        game_img (np.array): 遊戲畫面影像 (BGR格式)。
        templates_folder (str): 包含符文模板的資料夾。
        threshold (float): 模板匹配的相似度閾值。
        debug (bool): 是否在偵測結果圖上繪製框。
        
    Returns:
        list: 偵測到的符文 bounding box 列表，每個元素為 [x1, y1, x2, y2, score]。
    """
    all_detections = []
    
    # 符文可能出現在遊戲畫面的任何位置，特別是地圖中的隨機點
    # 因此這裡通常不建議裁剪 ROI，或者如果符文只會出現在特定幾個固定點，可以為每個點定義小 ROI
    # 如果符文生成是隨機的，就直接使用 game_img 進行全圖搜尋
    search_img = game_img.copy() 

    template_paths = glob(os.path.join(templates_folder, '*.png'))
    
    if not template_paths:
        print(f"警告：'{templates_folder}' 資料夾中沒有找到符文模板圖片。")
        return []

    for template_path in template_paths:
        tpl = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if tpl is None:
            print(f"警告：無法讀取符文模板圖片：{template_path}")
            continue

        h_tpl, w_tpl = tpl.shape[:2]
        
        # 進行模板匹配
        result = cv2.matchTemplate(search_img, tpl, cv2.TM_CCOEFF_NORMED)
        loc = np.where(result >= threshold)
        
        for pt in zip(*loc[::-1]):
            x1 = pt[0]
            y1 = pt[1]
            x2 = x1 + w_tpl
            y2 = y1 + h_tpl
            all_detections.append([x1, y1, x2, y2, result[pt[1], pt[0]]]) # 儲存分數

    # 對所有偵測結果應用 NMS (符文通常不會重疊，但以防萬一)
    final_detections = apply_nms(all_detections, overlapThresh=0.1) # 符文重疊閾值可以設低一點

    if debug:
        debug_img = game_img.copy()
        for (x1, y1, x2, y2, score) in final_detections:
            cv2.rectangle(debug_img, (x1, y1), (x2, y2), (255, 0, 255), 2) # 紫色框
            cv2.putText(debug_img, f"Rune: {score:.2f}", (x1, y1 - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)

        os.makedirs("debug_output", exist_ok=True)
        output_path = os.path.join("debug_output", "rune_detected.png")
        cv2.imwrite(output_path, debug_img)
        print(f"符文偵測結果圖已輸出：{output_path}，偵測到 {len(final_detections)} 個符文。")

    return final_detections

# 每個模板各自的門檻。詛咒/怪物模板的誤判分數很低 (負樣本 ~0.3-0.6)，跨視窗大小
# 比對時分數也偏低 (~0.85)，所以門檻放低仍有很大安全邊際；透明圖形標題文字容易和
# 場景誤撞 (負樣本可達 ~0.84)，需要較高門檻。沒列到的模板用傳入的 threshold。
LIE_CHECK_THRESHOLDS = {
    "transparent_title.png": 0.88,
    "curse_lock.png": 0.80,
    "curse_banner.png": 0.78,
    "monster_instr.png": 0.80,
}

# 每個模板佔畫面寬度的比例 (模板寬 / 來源截圖寬)。這些介面大小是相對於畫面的，
# 所以在任何視窗大小下，介面寬度 = fraction * 該畫面寬度。用這個直接把模板縮到
# 「這個畫面裡它應有的寬度」，比對就落在 scale≈1.0，掃描範圍可以很窄。
# 沒列到的模板退回用 reference_width 估算 (見下)。要新增模板時，
# capture_lie_template.py 會印出裁切區塊的 fraction 供填入。
LIE_CHECK_FRACTIONS = {
    "transparent_title.png": 0.1096,   # 295 / 2691
    "curse_banner.png":      0.4361,   # 1170 / 2683
    "curse_lock.png":        0.0596,   # 160 / 2683
    "monster_instr.png":     0.2115,   # 605 / 2860
}

# 偵測人機驗證 / 詛咒等需要真人處理的畫面 (lie check)
def detect_lie_check(game_img, templates_folder='assets/lie_check/', threshold=0.88,
                     work_width=700, reference_width=2750.0,
                     scale_min=0.85, scale_max=1.2, scale_step=0.05,
                     template_filter=None, thresholds=None, fractions=None, debug=False):
    """
    偵測「需要真人介入」的畫面 (人機驗證、詛咒符文警告等)。

    做法：把畫面縮到固定工作寬度 (work_width)，再對每個模板做多比例
    模板匹配。只要任一模板的最高分超過門檻，就視為偵測到。

    關鍵：這些介面大小是「相對於畫面」的 (UI 會隨視窗縮放)，所以在任何視窗大小下
    介面寬度 = fraction * 該畫面寬度。比對前把每個模板縮到「這個畫面裡它應有的
    寬度」(LIE_CHECK_FRACTIONS[name] * 工作畫面寬)，比對就落在 scale≈1.0，
    只需在 1.0 附近小幅掃描 (scale_min..scale_max) 吸收 UI 縮放/DPI 的細微差異。
    沒有列在 LIE_CHECK_FRACTIONS 的模板，退回用 work_width/reference_width 估算。

    注意：這樣只解決「大小」；分數上限仍受「模板來源的呈現方式」影響 (全螢幕裁的
    模板拿去比視窗模式的實際畫面，會因反鋸齒/背景不同而掉到 ~0.85)，那是用
    per-template 門檻 (LIE_CHECK_THRESHOLDS) 處理，不是靠縮放。要新增/更新模板，
    請用實際遊戲截圖裁切 (capture_lie_template.py 會印出 fraction 供填入)。

    Args:
        game_img (np.array): 遊戲畫面影像 (BGR格式)。
        templates_folder (str): 包含 lie-check 模板的資料夾。
        threshold (float): 模板匹配的相似度閾值。
        work_width (int): 比對前把畫面縮到的工作寬度 (畫面較窄時不放大)。
        reference_width (float): 模板來源截圖的畫面寬度 (用來正規化模板大小)。
        scale_min/scale_max/scale_step (float): 在 1.0 附近的縮放掃描範圍與步進。
        template_filter (list|None): 只比對這些檔名的模板 (None = 全部)。用來讓
            時間敏感的畫面 (透明圖形) 只跑單一便宜模板、快速偵測。
        thresholds (dict|None): 每模板門檻 (basename -> 值)，None = LIE_CHECK_THRESHOLDS。
        fractions (dict|None): 每模板佔畫面寬比例 (basename -> 值)，None = LIE_CHECK_FRACTIONS。
        debug (bool): 是否印出每個模板的最高分。

    Returns:
        list: 命中的模板列表，每個元素為 (template_name, score)。沒有命中時回傳 []。
    """
    template_paths = glob(os.path.join(templates_folder, '*.png'))
    if template_filter is not None:
        allow = set(template_filter)
        template_paths = [p for p in template_paths if os.path.basename(p) in allow]
    if not template_paths:
        return []

    img_h, img_w = game_img.shape[:2]
    # 縮到固定工作寬度 (只縮不放大)
    f = min(1.0, work_width / img_w) if img_w > 0 else 1.0
    search = game_img if f == 1.0 else cv2.resize(
        game_img, (int(img_w * f), int(img_h * f)), interpolation=cv2.INTER_AREA)
    sh, sw = search.shape[:2]

    n_steps = int(round((scale_max - scale_min) / scale_step)) + 1
    scales = [round(scale_min + i * scale_step, 4) for i in range(n_steps)]
    thr_map = thresholds if thresholds is not None else LIE_CHECK_THRESHOLDS
    frac_map = fractions if fractions is not None else LIE_CHECK_FRACTIONS

    hits = []
    for template_path in template_paths:
        tpl = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if tpl is None:
            print(f"警告：無法讀取 lie-check 模板圖片：{template_path}")
            continue
        name = os.path.basename(template_path)
        thr = thr_map.get(name, threshold)      # 每模板門檻，沒列到就用全域 threshold
        h0, w0 = tpl.shape[:2]
        # 把模板縮到「這個畫面裡它應有的寬度」：有 fraction 就用 fraction*畫面寬，
        # 沒有就退回 work_width/reference_width 估算。掃描以此為中心 (scale≈1.0)。
        frac = frac_map.get(name)
        base_w = int(frac * sw) if frac is not None else int(w0 * sw / reference_width)
        base_w = max(1, base_w)
        base_h = max(1, int(base_w * h0 / w0))
        base = cv2.resize(tpl, (base_w, base_h),
                          interpolation=cv2.INTER_AREA if base_w < w0 else cv2.INTER_LINEAR)

        best = -1.0
        for s in scales:
            tw, th = int(base_w * s), int(base_h * s)
            if tw < 8 or th < 8 or tw > sw or th > sh:
                continue  # 縮放後太小或超出畫面，跳過
            scaled = cv2.resize(base, (tw, th),
                        interpolation=cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR)
            result = cv2.matchTemplate(search, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > best:
                best = max_val
            if max_val >= thr and not debug:
                break  # 已達門檻即可提前結束此模板 (debug 模式仍掃完取最高分)

        if debug:
            print(f"lie-check 模板 {name}: 最高分 {best:.3f} (門檻 {thr})")
        if best >= thr:
            hits.append((name, float(best)))

    return hits

def detect_task_arrow(game_img, templates_folder='assets/task_arrows/', threshold=0.75, debug=False):
    raw_detections = []
    
    # 這是箭頭任務圖片的 ROI 假設，**請務必根據你實際的遊戲畫面測量和調整**
    # 範例：箭頭位於一個橫向條帶，可能在遊戲畫面中偏下方
    game_img_h, game_img_w = game_img.shape[:2]
    
    # 根據 image_e18148.jpg 來看，箭頭在畫面較為中間偏下的位置
    # 假設箭頭區域大概在螢幕的 60%Y 處開始，高度約 15%，寬度佔大部分
    ARROW_ROI_X_START = 500
    ARROW_ROI_Y_START = 500
    ARROW_ROI_WIDTH = 900
    ARROW_ROI_HEIGHT = 160
    
    # 確保 ROI 不會超出圖像邊界
    ARROW_ROI_X_END = min(ARROW_ROI_X_START + ARROW_ROI_WIDTH, game_img_w)
    ARROW_ROI_Y_END = min(ARROW_ROI_Y_START + ARROW_ROI_HEIGHT, game_img_h)

    # 裁剪 ROI
    roi_img = game_img[ARROW_ROI_Y_START : ARROW_ROI_Y_END, 
                       ARROW_ROI_X_START : ARROW_ROI_X_END].copy()
    
    if roi_img.size == 0:
        print("警告: 箭頭任務 ROI 區域為空。請檢查 ARROW_ROI 參數。")
        return []

    template_paths = glob(os.path.join(templates_folder, '*.png'))
    
    if not template_paths:
        print(f"警告：'{templates_folder}' 資料夾中沒有找到 '*.png' 箭頭模板圖片。")
        return []

    for template_path in template_paths:
        tpl = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if tpl is None:
            print(f"警告：無法讀取箭頭模板圖片：{template_path}")
            continue
        
        if tpl.shape[0] > roi_img.shape[0] or tpl.shape[1] > roi_img.shape[1]:
            print(f"錯誤：模板圖片 '{os.path.basename(template_path)}' ({tpl.shape[0]}x{tpl.shape[1]}) 大於或等於 ROI 區域 ({roi_img.shape[0]}x{roi_img.shape[1]})，請調整 ROI 或模板。")
            continue

        arrow_direction = os.path.basename(template_path).replace('arrow_', '').replace('.png', '')

        h_tpl, w_tpl = tpl.shape[:2]
        
        result = cv2.matchTemplate(roi_img, tpl, cv2.TM_CCOEFF_NORMED)
        loc = np.where(result >= threshold)
        
        for pt in zip(*loc[::-1]):
            x1 = pt[0] + ARROW_ROI_X_START # 加回 ROI 偏移量
            y1 = pt[1] + ARROW_ROI_Y_START # 加回 ROI 偏移量
            x2 = x1 + w_tpl
            y2 = y1 + h_tpl
            raw_detections.append([x1, y1, x2, y2, arrow_direction, result[pt[1], pt[0]]]) # 儲存分數

    # 對原始偵測結果應用 NMS (基於座標)
    final_detections = apply_nms(raw_detections, overlapThresh=0.2)

    if debug:
        debug_img = game_img.copy()
        cv2.rectangle(debug_img, (ARROW_ROI_X_START, ARROW_ROI_Y_START), 
                      (ARROW_ROI_X_END, ARROW_ROI_Y_END), 
                      (0, 0, 255), 2) # 紅色框標示 ROI
        
        for (x1, y1, x2, y2, direction, score) in final_detections:
            cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 255, 255), 2) # 黃色框
            cv2.putText(debug_img, f"{direction}: {score:.2f}", (x1, y1 - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

        os.makedirs("debug_output", exist_ok=True)
        output_path = os.path.join("debug_output", "task_arrow_detected.png")
        cv2.imwrite(output_path, debug_img)
        print(f"任務箭頭偵測結果圖已輸出：{output_path}，偵測到 {len(final_detections)} 個箭頭。")

    return final_detections
