#!/usr/bin/env bash
# Code-standard and test gates, run by CI and locally (./check.sh). Everything
# is discovered from the tracked files, so a new script, Python service or
# test is checked without editing CI - and a file type with no checker here
# fails until one is added (or it's listed as needing none).
# Needs: git, shellcheck, ruff (CI pins the version), python3, docker.
set -euo pipefail
cd "$(dirname "$0")"

fail=0
bad() {
    echo "FAIL: $*" >&2
    fail=1
}
files() { git ls-files --cached --others --exclude-standard "$@"; }

# 1. Every file type must have a checker (or be listed as needing none).
while read -r f; do
    case $f in
        *.sh | *.py | */Dockerfile) ;; # checked below (hadolint in CI)
        .github/workflows/*.yml | docker-compose.yml) ;; # actionlint / compose config in CI
        # No checker: docs, static assets, tool configs, systemd/udev/journald units.
        *.md | *.html | *.toml | .hadolint.yaml | .github/dependabot.yml) ;;
        .env.example | .gitignore | *.template | *.service | *.timer | *.rules | *.conf) ;;
        *) bad "$f: no checker for this file type - add one to check.sh/CI, or list it there as needing none" ;;
    esac
done < <(files)

# 2. Every Python directory follows the house pattern: its own ruff.toml and a test.
while read -r d; do
    [[ -f $d/ruff.toml ]] || bad "$d: Python without ruff.toml"
    compgen -G "$d/test_*.py" > /dev/null || bad "$d: Python without a test_*.py"
done < <(files '*.py' | xargs -n1 dirname | sort -u)

# 3. Linters.
files '*.sh' | xargs shellcheck || bad shellcheck
ruff check . || bad "ruff check"
ruff format --check . || bad "ruff format"

# 4. Build every image; its directory's Python tests run inside it (the Python
#    and packages it ships).
while read -r df; do
    d=$(dirname "$df")
    tag=check-${d//\//-}
    echo "--- build $d"
    docker build -q "$d" -t "$tag" > /dev/null || {
        bad "build $d"
        continue
    }
    for t in "$d"/test_*.py; do
        [[ -e $t ]] || continue
        echo "--- $t (in image)"
        docker run --rm -v "$PWD/$d:/t:ro" -w /t --entrypoint python3 "$tag" "$(basename "$t")" || bad "$t"
    done
done < <(files '*Dockerfile')

# 5. Every other test - test_*.sh, and test_*.py outside image directories - on
#    the host, each from its own directory.
while read -r t; do
    d=$(dirname "$t")
    [[ $t == *.py && -f $d/Dockerfile ]] && continue
    echo "--- $t"
    case $t in
        *.sh) (cd "$d" && "./$(basename "$t")") || bad "$t" ;;
        *.py) (cd "$d" && python3 "$(basename "$t")") || bad "$t" ;;
    esac
done < <(files '*test_*.sh' '*test_*.py')

((fail == 0)) && echo "check.sh: all gates passed"
exit "$fail"
