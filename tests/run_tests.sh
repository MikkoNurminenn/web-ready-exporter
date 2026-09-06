#!/usr/bin/env bash
# Usage: tests/run_tests.sh [path/to/blender]
# Runs the in-Blender test suite, then checks the CLI exit codes.
set -euo pipefail
B="${1:-blender}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
OUT="${WRE_TEST_OUT:-$(mktemp -d)}"
mkdir -p "$OUT"

echo "== Blender: $("$B" --version | head -1)"
echo "== 1/3 in-Blender test suite"
"$B" -b --python "$HERE/test_export.py" --python-exit-code 1 -- "$OUT/suite"

echo "== 2/3 CLI: strict run on the trap scene must exit non-zero"
"$B" -b --python "$HERE/trap_scene.py" --python-exit-code 1 -- "$OUT/trap.blend" | grep TRAP_SCENE_OK
set +e
strict_out="$("$B" -b "$OUT/trap.blend" --python "$ROOT/wre_cli.py" -- --out "$OUT/strict.glb" --strict 2>&1)"
strict_code=$?
set -e
echo "$strict_out" | grep -E "WRE_(OK|FAIL)" || true
if [ "$strict_code" -eq 0 ]; then
  echo "ERROR: strict run exited 0 but should have failed"; exit 1
fi
echo "   strict run exited $strict_code as expected"

echo "== 3/3 CLI: normal run must exit 0 and print WRE_OK"
"$B" -b "$OUT/trap.blend" --python "$ROOT/wre_cli.py" -- --out "$OUT/cli/model.glb" --tris 50000 --lods 0.5 | grep -E "^\[WRE\]|WRE_OK"
test -f "$OUT/cli/model.glb" && test -f "$OUT/cli/model_lod1.glb" && test -f "$OUT/cli/model.report.md"
echo "== all green (outputs in $OUT)"
