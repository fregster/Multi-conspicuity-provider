#!/usr/bin/env bash
# Checks check.sh's gates against a fixture repo (CHECK_DIR), with docker stubbed:
# it logs each call, fails builds/runs whose arguments mention "bad"/"fail", and
# each kcov run drops $tmp/cobertura.xml in as the merged coverage report.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fx=$tmp/repo
mkdir -p "$tmp/bin" "$fx"/{py,svc,img,bad,tool,sh}
# shellcheck disable=SC2016 # $1/$*/$a belong to the stub
printf '#!/bin/sh
echo "docker $*" >> "%s/calls"
case "$1" in
    inspect) echo 65534 ;;
    build)
        for last; do :; done
        [ "$last" != - ] || cat > /dev/null # Dockerfile on stdin
        case "$3" in *bad*) exit 1 ;; esac ;;
    run)
        [ "$3" != -i ] || cat > /dev/null
        for a; do case $a in *:/out) mkdir -p "${a%%:/out}/kcov-merged" && cp "%s/cobertura.xml" "${a%%:/out}/kcov-merged/" ;; esac; done
        case "$*" in *fail*) exit 1 ;; esac ;;
esac\n' "$tmp" "$tmp" > "$tmp/bin/docker"
chmod +x "$tmp/bin/docker"
git -C "$fx" init -q
echo .kcov/ > "$fx/.gitignore" # as in the real repo
cover() {                      # cobertura.xml (kcov's layout: one element per line) with a class per FILE:HITS
    {
        echo '<coverage><packages><package><classes>'
        for fh in "$@"; do printf '<class name="x" filename="%s">\n<line number="3" hits="%s"/>\n</class>\n' "${fh%:*}" "${fh##*:}"; done
        echo '</classes></package></packages></coverage>'
    } > "$tmp/cobertura.xml"
}
run() { (CHECK_DIR=$fx PATH="$tmp/bin:$PATH" ./check.sh 2>&1); }
has() { grep -qxF -- "$2" <<< "$1" || {
    echo "FAIL: no line '$2' in: $1" >&2
    exit 1
}; }

# Everything wrong at once.
cd "$fx"
touch a.weird py/x.py svc/Dockerfile svc/s.py svc/test_fail.py img/Dockerfile bad/Dockerfile \
    tool/t.py tool/test_t.py sh/tool.sh sh/untested.sh sh/test_ok.sh sh/test_fail.sh
printf '#!/bin/sh\nexit 1\n' > test_sbs_leak.sh
chmod +x test_sbs_leak.sh
cover sh/tool.sh:0
cd - > /dev/null
out=$(run) && exit 1
for want in "FAIL: a.weird: no checker for this file type - add one, or list it here as needing none" \
    "FAIL: py: Python without a test_*.py" "FAIL: build bad" "FAIL: svc/test_fail.py" \
    "FAIL: sh/test_fail.sh" "FAIL: test_sbs_leak.sh" "FAIL: sh/untested.sh: no test_*.sh runs it" \
    "FAIL: shell lines no test runs (file:line): sh/tool.sh:3"; do
    has "$out" "$want"
done
grep -q "^docker run .*python:3.13-alpine-cov .*test_t.py" "$tmp/calls" # outside an image dir: stock Python
grep -q "^docker run .*check-svc-cov .*test_fail.py" "$tmp/calls"       # in its service's image
grep -q "SH=bash kcov --include-path=/tmp/repo .* /out ./test_ok.sh" "$tmp/calls"

# All fixed.
cd "$fx"
rm -r a.weird bad svc/test_fail.py sh/test_fail.sh sh/untested.sh
touch py/test_x.py svc/test_s.py
printf '#!/bin/sh\n' > test_sbs_leak.sh
cover sh/tool.sh:1
cd - > /dev/null
out=$(run)
has "$out" "check.sh: all gates passed"
echo ok
