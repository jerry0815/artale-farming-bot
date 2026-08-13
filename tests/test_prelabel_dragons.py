import prelabel_dragons as pl

def test_to_yolo_line_center_and_size_normalized():
    # box (10,20,30,60) in a 100x200 image -> center (20,40), size (20,40)
    line = pl.to_yolo_line((10, 20, 30, 60), 100, 200)
    assert line == "0 0.200000 0.200000 0.200000 0.200000"

def test_to_yolo_line_respects_class_id():
    line = pl.to_yolo_line((0, 0, 50, 100), 100, 100, cls=3)
    assert line.startswith("3 ")
