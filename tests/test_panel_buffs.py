import json

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


def test_save_buffs_writes_and_preserves(tmp_path):
    p = tmp_path / "archer1.json"
    p.write_text(json.dumps({"name": "archer1", "buff_keys": ["a"],
                             "buff_interval_secs": 300, "note": "弓神"}), encoding="utf-8")
    ok, msg = panel.save_buffs(str(p), [{"keys": ["a", "3"], "interval_secs": 300}],
                               allowed_paths=[str(p)])
    assert ok, msg
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["buff_groups"] == [{"keys": ["a", "3"], "interval_secs": 300}]
    assert "buff_keys" not in data and data["note"] == "弓神"     # preserved, unescaped
    assert "\\u" not in p.read_text(encoding="utf-8")                     # CJK not escaped on disk


def test_save_buffs_rejects_unlisted_file(tmp_path):
    p = tmp_path / "evil.json"
    p.write_text("{}", encoding="utf-8")
    ok, msg = panel.save_buffs(str(p), [{"keys": ["a"], "interval_secs": 300}], allowed_paths=[])
    assert ok is False and "config" in msg


def test_save_buffs_rejects_bad_groups(tmp_path):
    p = tmp_path / "m.json"
    p.write_text("{}", encoding="utf-8")
    ok, msg = panel.save_buffs(str(p), [{"keys": [], "interval_secs": 300}], allowed_paths=[str(p)])
    assert ok is False


def test_potion_flag_round_trips_through_the_panel():
    cfg = {"buff_groups": [{"keys": ["d"], "interval_secs": 300},
                           {"keys": ["1"], "interval_secs": 600, "potion": True}]}
    groups = panel.normalize_buffs(cfg)
    assert groups == [{"keys": ["d"], "interval_secs": 300},
                      {"keys": ["1"], "interval_secs": 600, "potion": True}]
    out = panel.apply_buffs({}, groups)
    assert out["buff_groups"] == groups
