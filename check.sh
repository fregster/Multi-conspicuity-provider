#!/usr/bin/env bash
# Build and test gates: the pre-push hook, CI, or ./check.sh by hand. Linting
# and formatting run on commit (.pre-commit-config.yaml). Everything here is
# discovered from the tracked files, so a new script, Python service or test is
# checked without editing CI - and a file type with no checker fails until one
# is added (or it's listed as needing none).
# Needs: git, docker.
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

# 3. Python tests run under coverage (branch, settings in pyproject.toml) inside
#    an image: their service's own, so they test the Python and packages it ships.
pytest_cov() {
    local image=$1 dir=$2 test=$3 user
    user=$(docker inspect -f '{{.Config.User}}' "$image")
    printf 'FROM %s\nUSER root\nRUN pip install --no-cache-dir coverage==7.13.4\nUSER %s\n' "$image" "${user:-root}" |
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

# 5. Every other test, each from its own directory: test_*.sh on the host,
#    test_*.py outside image directories in the stock Python image.
while read -r t; do
    d=$(dirname "$t")
    [[ $t == *.py && -f $d/Dockerfile ]] && continue
    echo "--- $t"
    case $t in
        *.sh) (cd "$d" && "./$(basename "$t")") || bad "$t" ;;
        *) pytest_cov python:3.13-alpine "$d" "$(basename "$t")" || bad "$t" ;;
    esac
done < <(files '*test_*.sh' '*test_*.py')

((fail == 0)) && echo "check.sh: all gates passed"
exit "$fail"
