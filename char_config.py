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


def buff_plan(map_cfg, skill_interval):
    """Like parse_buff_groups, plus each group's kind: [(keys, interval_secs, potion)]. A group
    with "potion": true is instant (1, 3); the rest are SKILLS (d, g) that need the attack root to
    clear and a gap between casts (see cast_buffs)."""
    groups = map_cfg.get("buff_groups")
    if groups:
        plan = [(list(g.get("keys", [])), float(g.get("interval_secs", 300)), bool(g.get("potion")))
                for g in groups]
    else:
        plan = [(k, iv, False) for k, iv in parse_buff_groups(map_cfg, skill_interval)]
    return [(k, iv, p) for k, iv, p in plan if k]


def cast_buffs(due, kb, sleep, settle=0.6, gap=0.7, press=0.3, potion_press=0.05,
               potion_gap=0.05):
    """Press the due buff groups: due = [(keys, potion)]. SKILLS need her stopped with the attack
    root cleared (release all, `settle` s) and `gap` s after each cast so the next skill isn't
    swallowed by its animation. POTIONS are instant and work during an attack or skill animation
    (user), so they are interleaved, potion first: potion, skill, potion, skill ... -- the first
    inside the settle wait (while the attack finishes), each next one inside the gap after a skill
    (the waits keep their length). Potions left over follow quickly; with no skill due, potions are
    pressed without stopping at all. Returns the keys pressed, in order."""
    pots = [k for keys, potion in due if potion for k in keys]
    skills = [k for keys, potion in due if not potion for k in keys]
    order = []

    def potion(k):
        kb.safe_press(k); sleep(potion_press); kb.safe_release(k); sleep(potion_gap)
        order.append(k)
    if skills:
        kb.safe_release_all()
        used = 0.0
        if pots:                                       # a potion while the attack finishes
            potion(pots.pop(0)); used = potion_press + potion_gap
        sleep(max(0.0, settle - used))
        for k in skills:
            kb.safe_press(k); sleep(press); kb.safe_release(k)
            order.append(k)
            used = 0.0
            if pots:                                   # a potion during this skill's animation
                potion(pots.pop(0)); used = potion_press + potion_gap
            sleep(max(0.0, gap - used))
    for k in pots:                                     # left over (or no skill due): quick
        potion(k)
    return order


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
