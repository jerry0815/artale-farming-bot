"""label_mobs per-map + configurable-class CLI: --dir retargets the dataset, --classes sets
the label classes, and the served page reflects them (so one labeler works for any map)."""
import os
import pytest

label_mobs = pytest.importorskip("label_mobs")


def test_apply_cli_dir_retargets_dataset():
    label_mobs._apply_cli(["--dir", os.path.join("datasets", "flat_sweep")])
    assert label_mobs.IMG_DIR == os.path.join("datasets", "flat_sweep", "images")
    assert label_mobs.LBL_DIR == os.path.join("datasets", "flat_sweep", "labels")


def test_apply_cli_classes_sets_names_and_colors():
    label_mobs._apply_cli(["--classes", "mob"])
    assert label_mobs.CLASSES == ["mob"]
    assert len(label_mobs.COLORS) == 1


def test_apply_cli_multi_classes():
    label_mobs._apply_cli(["--classes", "a,b,c"])
    assert label_mobs.CLASSES == ["a", "b", "c"] and len(label_mobs.COLORS) == 3


def test_render_page_injects_classes():
    label_mobs._apply_cli(["--classes", "mob"])
    p = label_mobs.render_page()
    assert 'const CLASSES=["mob"]' in p


def test_defaults_unchanged_without_flags():
    # reset to defaults, no flags -> original fish/goby/player set
    label_mobs._apply_cli(["--classes", "fishhouse,goby,player"])
    label_mobs._apply_cli([])                       # no flags -> leaves current values
    assert label_mobs.CLASSES == ["fishhouse", "goby", "player"]
