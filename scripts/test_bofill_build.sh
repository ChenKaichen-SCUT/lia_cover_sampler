#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
kind=${1:-release}
build_dir="$PWD/build/bofill/$kind"
export HIGHDIV_ROOT=/tmp/lia-highdiv-frozen
export LIA_BINARY="$build_dir/lia_cover_sampler" LIA_AB_BINARY="$build_dir/ab_sampler"
export LIA_PREPARATION_PROBE="$build_dir/preparation_probe" LIA_COVERAGE_BLOCK_PROBE="$build_dir/coverage_block_probe"
export LIA_RESIDUAL_ONLINE_PROBE="$build_dir/residual_online_probe" LIA_MACRO_ONLINE_PROBE="$build_dir/macro_online_probe"
export LIA_MACRO_RETRY_PROBE="$build_dir/macro_retry_probe" LIA_MACRO_DIAGNOSTIC_PROBE="$build_dir/macro_diagnostic_probe"
export LIA_BOFILL_BINARY="$build_dir/bofill_sampler"
if [[ "$kind" == sanitize ]]; then
    export ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:fast_unwind_on_malloc=0
    export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
    export LSAN_OPTIONS="suppressions=$PWD/results/round3_dev/sanitize_diagnostic/lsan_external_z3.supp"
fi
ctest --test-dir "$build_dir" --output-on-failure
/tmp/lia-highdiv-frozen/venv/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v
