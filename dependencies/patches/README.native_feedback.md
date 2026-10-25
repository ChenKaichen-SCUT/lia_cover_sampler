# Native single-decision callback

Apply `z3_soft_phase.patch` unchanged, then `z3_native_feedback_phase.patch`, to
the Z3 subtree of upstream `f2c6e1756b13281d96cbf90a8bd940129e56cce4`.
`scripts/build_native_feedback_dependency.py` creates a separate source,
object and installation tree; no historical library is overwritten.

The callback observes the existing chosen decision atom and phase. Return
0 to skip, 1 to keep, 2 to flip, or 3 to stop this query. One selection per
installed query, including Keep; only the first 32 normal decisions are
inspected. The counters are not backtrackable. The interface never adds
assertions, changes the decision variable or requests a search. AST arguments
are borrowed; retain a strong reference before storing an expression.

Run the interface-only regression independently of sampler integration:

```bash
python3 scripts/build_native_feedback_dependency.py --jobs 12
g++ -std=c++20 -O2 tests/native_phase_hook_test.cpp -I dependencies/z3-native-feedback-phase/install/include -L dependencies/z3-native-feedback-phase/install/lib -Wl,-rpath,"$PWD/dependencies/z3-native-feedback-phase/install/lib" -lz3 -pthread -o /tmp/native_phase_hook_test
/tmp/native_phase_hook_test
```

The test covers Keep/disabled trajectory equality under a fixed operation
limit, all 16 models with Flip, propagation, special interfaces, single-point
limits across backtracking/restarts, Unknown and external cancellation.
Project-wide Release and ASan/UBSan gates are required before performance runs.
