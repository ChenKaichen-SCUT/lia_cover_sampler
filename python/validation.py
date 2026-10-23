"""Typed validation against the original declarations, independent of C++ evaluation."""
import json
import re
from pathlib import Path
import z3


def original_formula(path, ctx=None):
    return z3.And(z3.parse_smt2_file(str(path), ctx=ctx))


def nodes_preorder(root):
    nodes, seen = [], set()
    stack = [root]
    while stack:
        e = stack.pop()
        if e.get_id() in seen:
            continue
        seen.add(e.get_id())
        nodes.append(e)
        stack.extend(reversed(e.children()))
    return nodes


def variables(root):
    return sorted((e for e in nodes_preorder(root)
                   if z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED),
                  key=lambda e: (str(e.decl().name()), z3.is_bool(e)))


def decode_assignment(assignment, declarations):
    expected = {(str(e.decl().name()), str(e.sort())): e for e in declarations}
    supplied = {}
    for item in assignment:
        if set(item) != {"name", "sort", "value"}:
            raise ValueError("malformed typed assignment")
        key = item["name"], item["sort"]
        if key not in expected or key in supplied:
            raise ValueError("unknown or duplicate declaration")
        v = item["value"]
        if key[1] == "Int":
            if type(v) is not str or not re.fullmatch(r"-?(0|[1-9][0-9]*)", v):
                raise ValueError("Int must be an exact decimal string")
            supplied[key] = int(v)
        elif key[1] == "Bool":
            if type(v) is not bool:
                raise ValueError("Bool must be a JSON Boolean")
            supplied[key] = v
        else:
            raise ValueError("unsupported sort")
    if supplied.keys() != expected.keys():
        raise ValueError("missing input variables")
    return supplied


def verify(root, assignment, timeout_ms=5000, declarations=None):
    decls = declarations if declarations is not None else variables(root)
    values = decode_assignment(assignment, decls)
    substitutions = []
    for e in decls:
        v = values[str(e.decl().name()), str(e.sort())]
        term = z3.BoolVal(v, ctx=root.ctx) if z3.is_bool(e) else z3.IntVal(v, ctx=root.ctx)
        substitutions.append((e, term))
    # Substitute using the ORIGINAL sorted AST declarations, never same-name Int().
    concrete = z3.simplify(z3.substitute(root, *substitutions))
    if z3.is_true(concrete):
        return "valid"
    if z3.is_false(concrete):
        return "invalid"
    solver = z3.Solver(ctx=root.ctx)
    solver.set(timeout=timeout_ms)
    solver.add(root)
    solver.add(*(a == b for a, b in substitutions))
    result = solver.check()
    return "valid" if result == z3.sat else "invalid" if result == z3.unsat else "unknown"


def validate_file(formula, models):
    root = original_formula(formula)
    seen, counts = set(), {"valid": 0, "invalid": 0, "unknown": 0, "duplicate": 0, "malformed": 0}
    for line in Path(models).read_text().splitlines():
        try:
            row = json.loads(line)
            values = decode_assignment(row["assignment"], variables(root))
            key = tuple(sorted(values.items()))
            counts["duplicate"] += int(key in seen)
            seen.add(key)
            counts[verify(root, row["assignment"])] += 1
        except (ValueError, KeyError, TypeError):
            counts["malformed"] += 1
    return counts


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("formula")
    p.add_argument("models")
    a = p.parse_args()
    result = validate_file(a.formula, a.models)
    print(json.dumps(result, indent=2))
    raise SystemExit(int(any(result[k] for k in ("invalid", "unknown", "duplicate", "malformed"))))
