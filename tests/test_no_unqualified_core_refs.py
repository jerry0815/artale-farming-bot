"""Guard against the f-string qualifier bug: a module split out of core.py must reference
core state/functions as `core.<name>`, never as a bare name. A bare reference to a core-only
name is a latent NameError (it only fires when that code path runs live -- exactly the
free-market-escape crash in farm_water). This catches every such miss statically.

For each split module, collect every name BOUND anywhere in the file (module- or function-
level imports, assignments, defs, params). Any Name load that is NOT bound in the file, is
not a builtin, and IS a core.py module-level name = an unqualified core reference = a bug.
"""
import ast
import builtins

import pytest

# Modules extracted from core.py that must route engine calls through core.<name>.
SPLIT_MODULES = [
    "farm_water", "farm_dragon", "farm_general",
    "safety", "exp_tracking", "capture", "charloc", "focus_util", "swim", "approach", "char_config",
]


def _core_module_names():
    mod = ast.parse(open("core.py", encoding="utf-8").read())
    names = set()
    for n in mod.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.Assign):
            for t in ast.walk(n):
                if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                    names.add(t.id)
    for n in ast.walk(mod):
        if isinstance(n, ast.Global):
            names.update(n.names)
    return names


def _names_bound_anywhere(tree):
    """Every name bound somewhere in the file, in any scope."""
    bound = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            bound.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(n.name)
            args = n.args
            for a in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                bound.add(a.arg)
            if args.vararg:
                bound.add(args.vararg.arg)
            if args.kwarg:
                bound.add(args.kwarg.arg)
        elif isinstance(n, ast.ClassDef):
            bound.add(n.name)
    return bound


@pytest.mark.parametrize("mod_name", SPLIT_MODULES)
def test_no_unqualified_core_references(mod_name):
    core_names = _core_module_names()
    tree = ast.parse(open(mod_name + ".py", encoding="utf-8").read())
    bound = _names_bound_anywhere(tree)
    builtin_names = set(dir(builtins))
    bad = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            nm = n.id
            if nm in core_names and nm not in bound and nm not in builtin_names and nm != "core":
                bad.setdefault(nm, n.lineno)
    assert not bad, (f"{mod_name}.py references core-only names without a `core.` prefix "
                     f"(latent NameError): {bad}")
