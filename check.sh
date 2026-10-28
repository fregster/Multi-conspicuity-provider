#!/usr/bin/env bash
# Build and test gates: the pre-push hook, CI, or ./check.sh by hand. Linting
# and formatting run on commit (.pre-commit-config.yaml). Everything here is
# discovered from the tracked files, so a new script, Python service or test is
# checked without editing CI - and a file type with no checker fails until one
# is added (or it's listed as needing none).
# Needs: git, docker.
set -euo pipefail
cd "${CHECK_DIR:-$(dirname "$0")}" # CHECK_DIR: test_check.sh points it at a fixture repo

fail=0
bad() {
    echo "FAIL: $*" >&2
    fail=1
}
files() { git ls-files --cached --others --exclude-standard "$@"; }

# 1. Every file type must have a checker (or be listed as needing none).
while read -r f; do
    case $f in
        *.sh | *.py | */Dockerfile | *.md | *.html | *.cjs) ;; # linted on commit, tested below
        *.yml | *.yaml | *.toml) ;;                            # yamllint/actionlint/check-toml on commit
        .editorconfig | .shellcheckrc) ;;                      # tool configs, read by the linters
        # No checker: env template, ignore list, config templates, systemd/udev/journald units.
        .env.example | .gitignore | *.template | *.service | *.timer | *.rules | *.conf) ;;
        *) bad "$f: no checker for this file type - add one, or list it here as needing none" ;;
    esac
done < <(files)

# 2. Every Python directory has a test.
while read -r d; do
    compgen -G "$d/test_*.py" > /dev/null || bad "$d: Python without a test_*.py"
done < <(files '*.py' | xargs -n1 dirname | sort -u)

# 3. Python tests must cover 100% of lines and branches (pyproject.toml) inside
#    an image: their service's own, so they test the Python and packages it ships.
pytest_cov() {
    local image=$1 dir=$2 test=$3 user
    user=$(docker inspect -f '{{.Config.User}}' "$image")
    printf 'FROM %s\nUSER root\nRUN pip install --no-cache-dir coverage==7.17.0\nUSER %s\n' "$image" "${user:-root}" |
        docker build -q -t "$image-cov" - > /dev/null
    docker run --rm -v "$PWD:/repo:ro" -w "/repo/$dir" -e COVERAGE_FILE=/tmp/.coverage --entrypoint sh "$image-cov" \
        -c "coverage run --rcfile=/repo/pyproject.toml $test && coverage report --rcfile=/repo/pyproject.toml"
}

# 4. Build every image, and run its directory's Python tests in it.
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
        pytest_cov "$tag" "$d" "$(basename "$t")" || bad "$t"
    done
done < <(files '*Dockerfile')

# 5. Every other test, each from its own directory: test_*.py outside image
#    directories in the stock Python image, test_*.sh via shell_test.
# shell_test DIR TEST: runs in a kcov image on a copy of the repo (tests may write
# .env there), first plainly - each script under its own shebang, so dash for
# #!/bin/sh - then under kcov with SH=bash: kcov only traces bash, so tests start
# sh scripts as "${SH:-sh}". kcov measures lines only - shell has no branch
# coverage - and an empty case arm, "pattern) ;;", has no line to run.
kcov_image=mcp-check-kcov
printf 'FROM debian:trixie-slim\nRUN apt-get update && apt-get install -y --no-install-recommends kcov git gettext-base && rm -rf /var/lib/apt/lists/*\n' |
    docker build -q -t "$kcov_image" - > /dev/null
rm -rf .kcov
mkdir .kcov
shell_test() {
    local dir=$1 test=$2
    files | COPYFILE_DISABLE=1 tar --no-xattrs -cf - -T - |
        docker run --rm -i --user "$(id -u):$(id -g)" -v "$PWD/.kcov:/out" "$kcov_image" bash -c \
            "mkdir /tmp/repo && tar -x -C /tmp/repo && cd /tmp/repo/$dir && ./$test && SH=bash kcov --include-path=/tmp/repo --exclude-line=') ;;' /out ./$test"
}
while read -r t; do
    d=$(dirname "$t")
    [[ $t == *.py && -f $d/Dockerfile ]] && continue
    echo "--- $t"
    case $t in
        test_sbs_leak.sh) ./test_sbs_leak.sh || bad "$t" ;; # drives Docker itself: on the host, no script under test
        *.sh) shell_test "$d" "$(basename "$t")" || bad "$t" ;;
        *) pytest_cov python:3.13-alpine "$d" "$(basename "$t")" || bad "$t" ;;
    esac
done < <(files '*test_*.sh' '*test_*.py')

# 6. Every shell script (bar tests) is run by a test, every line of it.
#    Report: .kcov/index.html.
xml=.kcov/kcov-merged/cobertura.xml
while read -r f; do
    [[ $(basename "$f") == test_* ]] && continue
    grep -qs "filename=\"$f\"" "$xml" || bad "$f: no test_*.sh runs it"
done < <(files '*.sh')
uncovered=$(awk -F'"' '/<class /{f=$4} /<line / && /hits="0"/ && f ~ /\.sh$/ && f !~ /(^|\/)test_/ {print f ":" $2}' "$xml" 2> /dev/null || true)
[[ -z $uncovered ]] || bad "shell lines no test runs (file:line): ${uncovered//$'\n'/ }"

((fail == 0)) && echo "check.sh: all gates passed"
exit "$fail"
