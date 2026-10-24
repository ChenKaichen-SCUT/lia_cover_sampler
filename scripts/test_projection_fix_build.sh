#!/bin/bash
set -e
cd "$(dirname "$0")/.."
variant=$1
export HIGHDIV_ROOT=/tmp/lia-highdiv-frozen
build_dir="$PWD/build/round11/$variant"
export LIA_PROJECTION_FIXED_PROBE="$build_dir/projection_fixed_probe" LIA_PROJECTION_COMPONENT_PROBE="$build_dir/projection_component_probe"
export LIA_BINARY="$build_dir/lia_cover_sampler" LIA_AB_BINARY="$build_dir/ab_sampler" LIA_PREPARATION_PROBE="$build_dir/preparation_probe" LIA_COVERAGE_BLOCK_PROBE="$build_dir/coverage_block_probe" LIA_RESIDUAL_ONLINE_PROBE="$build_dir/residual_online_probe" LIA_MACRO_ONLINE_PROBE="$build_dir/macro_online_probe" LIA_MACRO_RETRY_PROBE="$build_dir/macro_retry_probe" LIA_MACRO_DIAGNOSTIC_PROBE="$build_dir/macro_diagnostic_probe" LIA_PROJECTION_ONLINE_PROBE="$build_dir/projection_online_probe"
if [ "$variant" = sanitize ]; then
 export ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:fast_unwind_on_malloc=0
 export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
 export LSAN_OPTIONS="suppressions=$PWD/results/round3_dev/sanitize_diagnostic/lsan_external_z3.supp"
fi
ctest --test-dir "$build_dir" --output-on-failure > "results/round11_projection/validation/$variant-ctest.log" 2>&1
/tmp/lia-highdiv-frozen/venv/bin/python3 -m unittest discover -s tests -v > "results/round11_projection/validation/$variant-python.log" 2>&1
