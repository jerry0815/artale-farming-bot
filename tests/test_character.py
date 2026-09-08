import json
import recovery


def test_apply_character_overrides_present_fields():
    m = {"attack_key": "c", "buff_keys": [], "detector": "mob_yolo"}
    c = {"attack_key": "x", "buff_keys": ["a", "j"]}
    out = recovery.apply_character(m, c)
    assert out["attack_key"] == "x"
    assert out["buff_keys"] == ["a", "j"]


def test_apply_character_keeps_map_value_for_absent_fields():
    m = {"attack_key": "c", "buff_interval_secs": 300}
    c = {"attack_key": "x"}                       # no buff_interval_secs
    out = recovery.apply_character(m, c)
    assert out["buff_interval_secs"] == 300       # map value preserved


def test_apply_character_none_char_returns_map_unchanged():
    m = {"attack_key": "c"}
    assert recovery.apply_character(m, None) == m


def test_apply_character_none_char_returns_a_copy():
    m = {"attack_key": "c"}
    out = recovery.apply_character(m, None)
    assert out == m and out is not m       # equal contents, distinct object


def test_apply_character_ignores_none_valued_char_fields():
    m = {"attack_key": "c"}
    c = {"attack_key": None}                       # explicit null -> not an override
    assert recovery.apply_character(m, c)["attack_key"] == "c"


def test_apply_character_never_touches_non_allowlisted_fields():
    m = {"attack_key": "c", "detector": "mob_yolo", "farm_nodes": ["P1"]}
    c = {"attack_key": "x", "detector": "HACKED", "farm_nodes": []}   # non-allowlisted
    out = recovery.apply_character(m, c)
    assert out["detector"] == "mob_yolo"          # untouched
    assert out["farm_nodes"] == ["P1"]            # untouched


def test_apply_character_does_not_mutate_inputs():
    m = {"attack_key": "c"}
    recovery.apply_character(m, {"attack_key": "x"})
    assert m["attack_key"] == "c"                  # original map unchanged


def test_char_buff_keys_replace_map_buff_groups():
    # A character's simple buff_keys must WIN over a map's buff_groups (which otherwise shadows
    # buff_keys in the loop) -- else switching character never changes the buffs.
    m = {"buff_groups": [{"keys": ["d"], "interval_secs": 300}], "attack_key": "c"}
    c = {"buff_keys": ["a", "j"], "buff_interval_secs": 240}
    out = recovery.apply_character(m, c)
    assert out["buff_keys"] == ["a", "j"]
    assert "buff_groups" not in out               # map's groups dropped so the loop uses buff_keys


def test_char_buff_groups_override_map_buff_groups():
    m = {"buff_groups": [{"keys": ["d"]}]}
    c = {"buff_groups": [{"keys": ["a"], "interval_secs": 100}]}
    out = recovery.apply_character(m, c)
    assert out["buff_groups"] == [{"keys": ["a"], "interval_secs": 100}]


def test_load_char_reads_file(tmp_path):
    p = tmp_path / "archer1.json"
    p.write_text(json.dumps({"name": "archer1", "attack_key": "x"}), encoding="utf-8")
    assert recovery.load_char(str(p))["attack_key"] == "x"


def test_load_char_passthrough_dict():
    d = {"attack_key": "x"}
    assert recovery.load_char(d) is d


def test_farming_loop_water_merges_char_before_reading(monkeypatch, tmp_path):
    # Prove the character's attack_key reaches the loop: stub focus() to bail right after the
    # merge, and capture the attack_key the loop resolved.
    import recovery
    seen = {}
    m = {"detector": "time", "farm_nodes": ["P1"], "nodes": [{"name": "P1", "band": [0, 4, 0, 4]}],
         "attack_key": "c"}
    monkeypatch.setattr(recovery.watermap, "load_map", lambda p: dict(m))
    monkeypatch.setattr(recovery.watermap, "minimap_crop", lambda cfg: (20, 171, 229, 259))
    monkeypatch.setattr(recovery.watermap, "node_centers", lambda cfg: {"P1": (2, 2)})
    monkeypatch.setattr(recovery.watermap, "swim_tol", lambda cfg: (10, 8, 16))
    monkeypatch.setattr(recovery, "set_minimap", lambda *a, **k: None)
    monkeypatch.setattr(recovery, "set_enemy_threshold", lambda *a, **k: None)

    def fake_focus():
        seen["attack_key"] = recovery._LAST_WATER_ATTACK_KEY[0]
        return False                                  # bail out of the loop immediately

    monkeypatch.setattr(recovery, "focus", fake_focus)
    ch = tmp_path / "c.json"
    ch.write_text('{"attack_key": "x"}', encoding="utf-8")
    recovery.farming_loop_water("m.json", char=str(ch))
    assert seen["attack_key"] == "x"
