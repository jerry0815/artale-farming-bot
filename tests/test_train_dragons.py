# tests/test_train_dragons.py
import train_dragons as td

def test_assign_split_is_deterministic_80_20():
    paths = [f"img_{i}.png" for i in range(10)]
    train, val = td.assign_split(paths, val_every=5)
    # sorted indices 0 and 5 go to val -> 2 val, 8 train, no overlap
    assert len(val) == 2 and len(train) == 8
    assert set(train).isdisjoint(val)
    assert sorted(train + val) == sorted(paths)

def test_assign_split_val_every_controls_ratio():
    paths = [f"img_{i}.png" for i in range(20)]
    _, val = td.assign_split(paths, val_every=4)
    assert len(val) == 5   # indices 0,4,8,12,16
