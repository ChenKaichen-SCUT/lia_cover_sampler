#!/usr/bin/env bash
set -eu
variant=${1:-release}
root=$(cd "$(dirname "$0")/.." && pwd)
bin="$root/build/bofill_diagnosis/$variant"
logs="$root/results/bofill_fix_validation/regression"
export HIGHDIV_ROOT=/tmp/lia-highdiv-frozen
export LIA_BINARY="$bin/lia_cover_sampler" LIA_AB_BINARY="$bin/ab_sampler" LIA_PREPARATION_PROBE="$bin/preparation_probe"
export LIA_COVERAGE_BLOCK_PROBE="$bin/coverage_block_probe" LIA_RESIDUAL_ONLINE_PROBE="$bin/residual_online_probe"
export LIA_MACRO_ONLINE_PROBE="$bin/macro_online_probe" LIA_MACRO_RETRY_PROBE="$bin/macro_retry_probe" LIA_MACRO_DIAGNOSTIC_PROBE="$bin/macro_diagnostic_probe"
export LIA_BOFILL_BINARY="$bin/bofill_sampler" LIA_BOFILL_FIRST_SAT_PROBE="$bin/bofill_first_sat_probe"
if [ "$variant" = sanitize ]; then
 export ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:fast_unwind_on_malloc=0
 export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
 export LSAN_OPTIONS="suppressions=$root/results/round3_dev/sanitize_diagnostic/lsan_external_z3.supp"
fi
ctest --test-dir "$bin" --output-on-failure > "$logs/${variant}_cpp.log" 2>&1
/tmp/lia-highdiv-frozen/venv/bin/python3 -m unittest discover -s "$root/tests" -p 'test_*.py' -v > "$logs/${variant}_python.log" 2>&1
