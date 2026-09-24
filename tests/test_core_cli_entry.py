"""`python core.py <cmd>` must start. Run as a script, core.py is module `__main__`, so an
extracted module's `import core` would load core.py a SECOND time and hit a circular import
(exp_tracking/safety/... re-exported at core's end). core.py aliases itself as `core` when run
as a script; this guards the CLI entry (record-route, runnav, run_train.bat) that normal
`import core` tests never exercise."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_core_script_entry_imports_cleanly():
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "core.py", "help"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "ImportError" not in out and "Traceback" not in out, out[-2000:]
    assert out.count("[notify] Discord webhook") <= 1     # core.py loaded once, not twice
