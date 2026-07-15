#!/usr/bin/env bash
#
# smoke_installed.sh — exercise the INSTALLED build, the way a real user's bin/gpi assembles it:
# a real wheel in a throwaway isolated environment, NOT the editable dev copy on sys.path.
#
# This is the "test the installed version cleanly" gate. It catches two failure classes that the
# dev venv is structurally blind to (its conftest + editable .pth put the source tree on the import
# path, so a file that isn't in the wheel still imports):
#   1. packaging gaps  — a module/data file present in the checkout but missing from the wheel;
#   2. version drift    — it prints the built version, so a stale marketplace install is obvious.
#
# It is FREE: doctor + --check-inputs + --dry-run make no API calls and spend nothing. --dry-run
# runs preflight_imports, which imports every pipeline step module from the wheel — so a packaging
# gap fails here, at $0, instead of mid-run on a user's machine.
#
# Usage:  scripts/smoke_installed.sh
set -euo pipefail

REPO="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
    echo "smoke: uv is required — https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 127
fi

WHEEL_DIR="$(mktemp -d)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$WHEEL_DIR" "$SCRATCH"' EXIT

fail() { echo; echo "SMOKE FAILED: $*" >&2; exit 1; }

echo "== building a real wheel from $REPO (as bin/gpi does) =="
uv build --wheel -o "$WHEEL_DIR" "$REPO" >/dev/null
WHEEL="$(ls "$WHEEL_DIR"/*.whl | head -1)"
[ -n "${WHEEL:-}" ] || fail "uv build produced no wheel"
echo "   wheel: $(basename "$WHEEL")"

# The wheel's console script, in an ephemeral env with NO editable project and NO source tree.
# PYTHONSAFEPATH=1 keeps the caller's cwd off sys.path so `import gpi` can only resolve to the wheel.
run_wheel() { PYTHONSAFEPATH=1 uv run --isolated --no-project --with "$WHEEL" gpi "$@"; }

# A self-contained demo config: absolute input paths (so cwd is irrelevant) + output into SCRATCH
# (so a --dry-run never writes into the repo's runs/). The demo dataset ships in the clone.
CONFIG="$SCRATCH/smoke_config.yaml"
sed -e "s|examples/brain_endothelial_demo/|$REPO/examples/brain_endothelial_demo/|g" \
    -e "s|^output_dir:.*|output_dir: $SCRATCH/out|" \
    "$REPO/configs/example_generic.yaml" > "$CONFIG"

echo
echo "== 1/4  gpi doctor — from a scratch dir, pure wheel, no source anywhere near cwd =="
DOCTOR_OUT="$(cd "$SCRATCH" && run_wheel doctor 2>&1)" || true   # non-zero on a missing cred is OK
echo "$DOCTOR_OUT"
grep -q "Gene Program Interpreter v" <<<"$DOCTOR_OUT" \
    || fail "doctor did not print its version banner — the package failed to import from the wheel"

echo
echo "== 2/4  gpi --check-inputs on the shipped demo dataset =="
( cd "$SCRATCH" && run_wheel --check-inputs --config "$CONFIG" ) \
    || fail "--check-inputs failed on the installed build"

echo
echo "== 3/4  gpi --dry-run (plan only; imports every step module from the wheel; no spend) =="
( cd "$SCRATCH" && run_wheel --dry-run --config "$CONFIG" >/dev/null ) \
    || fail "--dry-run failed — likely a packaging gap (a step module missing from the wheel)"
echo "   dry-run plan produced; all step modules imported from the wheel"

echo
echo "== 4/4  bin/gpi doctor — the exact launcher path a user runs (fresh, uncached) =="
# bin/gpi runs `uv tool run --from <path>`, which CACHES the built env. When source changes but the
# version string does NOT, uv reuses the STALE cached build — the same mechanism behind a stale
# marketplace install. UV_NO_CACHE=1 forces a from-scratch build, so this step reflects exactly what
# a brand-new user with an empty cache gets. (It is the reliable escape hatch when a `bin/gpi` run
# looks stale after a same-version source change: `UV_NO_CACHE=1 bin/gpi ...`.)
LAUNCH_OUT="$(cd "$SCRATCH" && UV_NO_CACHE=1 "$REPO/bin/gpi" doctor 2>&1)" || true
echo "$LAUNCH_OUT"
grep -q "Gene Program Interpreter v" <<<"$LAUNCH_OUT" \
    || fail "bin/gpi doctor did not print its version banner (launcher failed to build current source)"

VERSION="$(run_wheel --version 2>/dev/null | awk '{print $NF}')"
echo
echo "======================================================================"
echo " SMOKE OK — the installed build works end-to-end (no spend)."
echo " gpi version: ${VERSION:-unknown}"
echo " If a user reports old behavior, compare this to their \`gpi doctor\` version."
echo "======================================================================"
