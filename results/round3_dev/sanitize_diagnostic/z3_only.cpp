#include <z3++.h>
int main() {
  { z3::context ctx; z3::solver solver(ctx,z3::solver::simple{});
    auto x=ctx.int_const("x");solver.add(x==ctx.int_val("184467440737095516170"));
    solver.check(); }
  Z3_finalize_memory();
}
