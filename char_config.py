"""Extracted from core.py (behavior unchanged). Functions call the engine as core.<name>;
core.py re-exports them so callers/tests keep using core.<name> and monkeypatch still binds."""
import core


def load_char(path):
    """Load a chars/<name>.json character config. `path` may be a path string (read as
    UTF-8 JSON) or an already-loaded dict (returned unchanged)."""
    if isinstance(path, dict):
        return path
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_character(map_cfg, char_cfg):
    """Overlay a character's key settings onto a map config. Returns a SHALLOW COPY of
    `map_cfg` with each present CHAR_FIELDS value from `char_cfg` overriding the map's
    (precedence character -> map -> code default). A field absent or None in `char_cfg`
    leaves the map's value untouched. `char_cfg` falsy -> `map_cfg` returned unchanged."""
    if not char_cfg:
        return dict(map_cfg)  # always a copy, per the docstring's guarantee
    merged = dict(map_cfg)
    for k in core.CHAR_FIELDS:
        if char_cfg.get(k) is not None:
            merged[k] = char_cfg[k]
    # A character's buff spec REPLACES the map's. The loop prefers buff_groups over buff_keys,
    # so if the character gives the simple buff_keys form (and not buff_groups), drop the map's
    # buff_groups -- otherwise it would shadow the character's buffs (switching character would
    # leave the buffs unchanged).
    if char_cfg.get("buff_keys") is not None and char_cfg.get("buff_groups") is None:
        merged.pop("buff_groups", None)
    # Attack: a character's single attack_key means "all mobs use this key". Don't DROP the
    # map's per-mob attack_keys (that would turn off class detection, breaking priority_class /
    # per-mob targeting) -- instead rewrite each class to the character's key. Keeps the class
    # taxonomy (with_class stays on) while enforcing the single key.
    if char_cfg.get("attack_key") is not None and char_cfg.get("attack_keys") is None:
        if merged.get("attack_keys"):
            merged["attack_keys"] = {k: char_cfg["attack_key"] for k in merged["attack_keys"]}
    return merged


def parse_buff_groups(map_cfg, skill_interval):
    """Normalize a map config's buff spec into a list of (keys, interval_secs) tuples, each
    group firing on its OWN interval (e.g. 'd' every 5min and a slow 'G' every 15min run
    independently). Config form: "buff_groups": [{"keys":["d"],"interval_secs":300}, ...].
    Back-compat: no buff_groups -> one group from buff_keys / buff_interval_secs (falling back
    to the low end of `skill_interval` when no interval is given). Empty key sets are dropped."""
    groups = map_cfg.get("buff_groups")
    if groups:
        buff_groups = [(list(g.get("keys", [])), float(g.get("interval_secs", 300))) for g in groups]
    else:
        bk = map_cfg.get("buff_keys", [])
        bi = map_cfg.get("buff_interval_secs")
        buff_groups = [(list(bk), float(bi) if bi else float(skill_interval[0]))] if bk else []
    return [(k, iv) for k, iv in buff_groups if k]      # drop empty key sets
