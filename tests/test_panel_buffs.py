import panel


def test_normalize_buffs_groups_schema():
    cfg = {"buff_groups": [{"keys": ["d"], "interval_secs": 300},
                           {"keys": ["g"], "interval_secs": 900}]}
    assert panel.normalize_buffs(cfg) == [
        {"keys": ["d"], "interval_secs": 300},
        {"keys": ["g"], "interval_secs": 900},
    ]


def test_normalize_buffs_simple_schema():
    cfg = {"buff_keys": ["a", "j"], "buff_interval_secs": 300}
    assert panel.normalize_buffs(cfg) == [{"keys": ["a", "j"], "interval_secs": 300}]


def test_normalize_buffs_simple_default_interval():
    # buff_keys with no interval -> default 300s
    assert panel.normalize_buffs({"buff_keys": ["a"]}) == [{"keys": ["a"], "interval_secs": 300}]


def test_normalize_buffs_none():
    assert panel.normalize_buffs({"name": "x"}) == []
    assert panel.normalize_buffs({"buff_groups": []}) == []
