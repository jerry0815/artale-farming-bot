# tests/test_capture_frames.py
import os
import numpy as np
import capture_frames

def test_save_frame_writes_clean_png(tmp_path):
    img = np.zeros((8, 8, 3), np.uint8)
    img[2, 2] = (200, 50, 50)
    p = capture_frames.save_frame(str(tmp_path), 7, img)
    assert p.endswith("frame_00007.png")
    assert os.path.exists(p)

def test_save_frame_roundtrips_pixels_unmodified(tmp_path):
    import cv2
    img = np.random.randint(0, 255, (12, 10, 3), np.uint8)
    p = capture_frames.save_frame(str(tmp_path), 1, img)
    back = cv2.imread(p, cv2.IMREAD_COLOR)
    assert back.shape == img.shape
    assert np.array_equal(back, img)   # no overlay drawn on training frames
