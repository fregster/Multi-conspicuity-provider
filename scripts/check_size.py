#!/usr/bin/env python3
"""Size caps: no file over MAX_FILE substantive lines, no shell function over
MAX_FUNC. Blank and `#` comment lines don't count. Run: check_size.py FILE...
"""

import re
import sys
from pathlib import Path

MAX_FILE = 300
MAX_FUNC = 50
# `name() {` or `function name {`; shfmt puts the closing `}` at the same indent.
FUNC_START = re.compile(r"^(\s*)(?:function\s+(\w+)(?:\s*\(\))?|(\w+)\s*\(\))\s*\{")


def substantive(lines: list[str]) -> list[str]:
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def shell_functions(lines: list[str]) -> list[tuple[str, int]]:
    """(name, substantive line count) of each top-level shell function."""
    found: list[tuple[str, int]] = []
    name, end, count = "", "", 0
    for line in substantive(lines):
        if not name and (m := FUNC_START.match(line)):
            name, end, count = m.group(2) or m.group(3), m.group(1) + "}", 0
        if name:
            count += 1
            if line.rstrip() == end or (count == 1 and line.rstrip().endswith("}")):
                found.append((name, count))
                name = ""
    return found


def problems(path: str) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    out = []
    if (n := len(substantive(lines))) > MAX_FILE:
        out.append(f"{path}: {n} lines (max {MAX_FILE}) - split it by responsibility")
    if path.endswith(".sh"):
        out += [
            f"{path}: function {fn} is {n} lines (max {MAX_FUNC})" for fn, n in shell_functions(lines) if n > MAX_FUNC
        ]
    return out


def main(paths: list[str]) -> int:
    found = [p for path in paths for p in problems(path)]
    for p in found:
        print(p, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
