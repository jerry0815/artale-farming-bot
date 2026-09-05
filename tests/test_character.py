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


def test_load_char_reads_file(tmp_path):
    p = tmp_path / "archer1.json"
    p.write_text(json.dumps({"name": "archer1", "attack_key": "x"}), encoding="utf-8")
    assert recovery.load_char(str(p))["attack_key"] == "x"


def test_load_char_passthrough_dict():
    d = {"attack_key": "x"}
    assert recovery.load_char(d) is d
