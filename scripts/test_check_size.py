"""Run: python3 test_check_size.py"""

import runpy
import sys
import tempfile
from pathlib import Path

import check_size as cs

assert cs.substantive(["a", "", "  # c", "b # not a comment line"]) == ["a", "b # not a comment line"]

SH = """\
set -eu
small() { echo hi; }
big() {
    if x; then
        echo {
    fi
}
function other {
  echo a
  }
}
"""
assert cs.shell_functions(SH.splitlines()) == [("small", 1), ("big", 5), ("other", 4)]


def check(name: str, text: str) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_text(text, encoding="utf-8")
        return [line.replace(str(p), name) for line in cs.problems(str(p))]


ok_func = "f() {\n" + "    x\n" * (cs.MAX_FUNC - 2) + "}\n"
long_func = "f() {\n" + "    x\n" * (cs.MAX_FUNC - 1) + "}\n"
assert check("a.sh", ok_func) == []
assert check("a.sh", long_func) == [f"a.sh: function f is {cs.MAX_FUNC + 1} lines (max {cs.MAX_FUNC})"]
assert check("a.py", long_func) == []  # function cap is shell-only

assert check("a.py", "x\n" * cs.MAX_FILE + "#\n\n") == []
assert check("a.py", "x\n" * (cs.MAX_FILE + 1)) == [
    f"a.py: {cs.MAX_FILE + 1} lines (max {cs.MAX_FILE}) - split it by responsibility"
]

with tempfile.TemporaryDirectory() as d:
    good, bad = Path(d) / "g.py", Path(d) / "b.py"
    good.write_text("x\n", encoding="utf-8")
    bad.write_text("x\n" * (cs.MAX_FILE + 1), encoding="utf-8")
    assert cs.main([str(good)]) == 0
    assert cs.main([str(good), str(bad)]) == 1
    sys.argv = ["check_size.py", str(good)]
    code = None
    try:
        runpy.run_path(cs.__file__, run_name="__main__")
    except SystemExit as e:
        code = e.code
    assert code == 0
print("ok")
