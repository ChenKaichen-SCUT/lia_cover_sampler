#!/usr/bin/env bash
set -eo pipefail
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
frozen_commit=f2c6e1756b13281d96cbf90a8bd940129e56cce4
dependency_dir="$HIGHDIV_ROOT"
if [[ -z "$dependency_dir" ]]; then dependency_dir="$project_dir/dependencies/HighDiv"; fi
if [[ ! -d "$dependency_dir" ]]; then
  git clone https://github.com/laigroup/HighDiv.git "$dependency_dir"
  git -C "$dependency_dir" checkout --detach "$frozen_commit"
fi
if [[ -d "$dependency_dir/.git" ]]; then
  test "$(git -C "$dependency_dir" rev-parse HEAD)" = "$frozen_commit"
fi
z3_prefix="$Z3_PREFIX"
if [[ -z "$z3_prefix" ]]; then z3_prefix="$dependency_dir/venv"; fi
if [[ ! -f "$z3_prefix/lib/libz3.so" ]]; then
  bash "$dependency_dir/scripts/build.sh" --jobs 12
fi
cmake -S "$project_dir" -B "$project_dir/build" -DCMAKE_BUILD_TYPE=Release -DZ3_PREFIX="$z3_prefix"
cmake --build "$project_dir/build" -j 12
ldd "$project_dir/build/lia_cover_sampler"
ctest --test-dir "$project_dir/build" --output-on-failure
HIGHDIV_ROOT="$dependency_dir" "$z3_prefix/bin/python3" -m unittest discover -s "$project_dir/tests" -v
