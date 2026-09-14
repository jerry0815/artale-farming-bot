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


def test_apply_buffs_sets_groups_and_drops_simple():
    cfg = {"name": "archer1", "attack_key": "x", "buff_keys": ["a", "j"],
           "buff_interval_secs": 300, "buff_settle_secs": 0.6}
    out = panel.apply_buffs(cfg, [{"keys": ["a", "j", "3"], "interval_secs": 300}])
    assert out["buff_groups"] == [{"keys": ["a", "j", "3"], "interval_secs": 300}]
    assert "buff_keys" not in out and "buff_interval_secs" not in out
    assert out["name"] == "archer1" and out["buff_settle_secs"] == 0.6   # others preserved
    assert cfg["buff_keys"] == ["a", "j"]                                # input not mutated


def test_apply_buffs_replaces_existing_groups():
    cfg = {"buff_groups": [{"keys": ["d"], "interval_secs": 300}], "platform_half": 60}
    out = panel.apply_buffs(cfg, [{"keys": ["3"], "interval_secs": 300}])
    assert out["buff_groups"] == [{"keys": ["3"], "interval_secs": 300}]
    assert out["platform_half"] == 60


def test_validate_groups():
    assert panel.validate_groups([{"keys": ["a"], "interval_secs": 300}]) == (True, "ok")
    assert panel.validate_groups([]) == (True, "ok")                       # clearing is allowed
    assert panel.validate_groups([{"keys": [], "interval_secs": 300}])[0] is False
    assert panel.validate_groups([{"keys": ["a"], "interval_secs": 0}])[0] is False
    assert panel.validate_groups([{"keys": ["a"], "interval_secs": "x"}])[0] is False
    assert panel.validate_groups("nope")[0] is False
