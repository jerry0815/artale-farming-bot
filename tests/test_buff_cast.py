"""Potion vs skill buffs (user): skills (d, g) need the attack root to clear and a gap between
casts; potions (1, 3) are instant -- no settle, no gap, no stopping."""
import core  # noqa: F401  (char_config is loaded through core)
import char_config


class _KB:
    def __init__(self):
        self.ev = []
    def safe_press(self, k): self.ev.append(("press", k))
    def safe_release(self, k): self.ev.append(("release", k))
    def safe_release_all(self): self.ev.append(("release_all", None))


def test_buff_plan_marks_potion_groups():
    cfg = {"buff_groups": [{"keys": ["d"], "interval_secs": 300},
                           {"keys": ["1"], "interval_secs": 600, "potion": True},
                           {"keys": [], "interval_secs": 60}]}
    plan = char_config.buff_plan(cfg, (240, 300))
    assert plan == [(["d"], 300.0, False), (["1"], 600.0, True)]
    assert char_config.parse_buff_groups(cfg, (240, 300)) == [(["d"], 300.0), (["1"], 600.0)]


def test_potions_only_do_not_stop_her_or_wait():
    kb, slept = _KB(), []
    char_config.cast_buffs([(["1"], True), (["3"], True)], kb, sleep=slept.append,
                           settle=0.6, gap=0.7)
    assert ("release_all", None) not in kb.ev              # keeps walking / attacking
    assert [k for e, k in kb.ev if e == "press"] == ["1", "3"]
    assert 0.6 not in slept and 0.7 not in slept           # no settle, no skill gap
    assert sum(slept) < 0.4


def test_potions_go_into_the_gaps_between_skills():
    # user: a potion works during a skill's animation -> skill, potion, skill, potion ...
    kb, slept = _KB(), []
    char_config.cast_buffs([(["d"], False), (["1"], True), (["g"], False), (["3"], True)], kb,
                           sleep=slept.append, settle=0.6, gap=0.7)
    presses = [k for e, k in kb.ev if e == "press"]
    # user: potion FIRST -- it works while the attack finishes, filling the settle wait
    assert presses == ["1", "d", "3", "g"]
    assert kb.ev.index(("release_all", None)) < kb.ev.index(("press", "d"))  # stop for skills
    # the potions ride inside the settle / skill gap: no longer than the skills alone
    assert sum(slept) <= 0.6 + 2 * (0.3 + 0.7) + 1e-9


def test_extra_potions_follow_the_last_skill_quickly():
    kb, slept = _KB(), []
    char_config.cast_buffs([(["d"], False), (["1", "3"], True)], kb, sleep=slept.append,
                           settle=0.6, gap=0.7)
    assert [k for e, k in kb.ev if e == "press"] == ["1", "d", "3"]
