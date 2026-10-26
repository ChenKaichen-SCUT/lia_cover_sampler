#!/usr/bin/env python3
from __future__ import annotations
import abc
import argparse
import itertools
import functools
import fractions
import operator
import sys
import typing as typ
import collections
from fractions import Fraction

import z3

import sys

sys.setrecursionlimit(20000000)

PARSER = argparse.ArgumentParser(
    description="Calculate coverage from samples file and smt2 file"
)
PARSER.add_argument(
    "-s",
    "--samples",
    metavar="FILE",
    type=open,
    required=True,
    help="File to load samples from",
)
PARSER.add_argument(
    "-f",
    "--formula",
    metavar="FILE",
    type=open,
    required=True,
    help="Formula file (smt2)",
)
PARSER.add_argument(
    "--use-c-api",
    action="store_true",
    help="Use Z3 C API calls to load samples, if applicable",
)
PARSER.add_argument(
    "-m", "--metric", required=True, choices=["satisfies", "wire_coverage"]
)
PARSER.add_argument(
    "-l",
    "--limit",
    metavar="LIMIT",
    type=int,
    default=0,
    help="Limit number of samples proccessed (0 for no limit)",
)

PARSER.add_argument(
    "-p", "--print-first", action="store_true", help="Print the first satisfying sample"
)
CTX = z3.Context()


def prod(*args):
    if len(args) == 1:
        args = args[0]
    return functools.reduce(operator.mul, args, 1)


class Metric(abc.ABC):
    def __init__(self, formula: str):
        self._solver = z3.Solver(ctx=CTX)
        self._total = 0
        self._satisfies = 0
        self._solver.from_string(formula)

    @abc.abstractmethod
    def count_sample(self, sample: list[tuple[str, int]]) -> bool:
        pass

    @property
    def result(self) -> fractions.Fraction:
        return fractions.Fraction(self._satisfies, self._total)


class SatisfiesMetric(Metric):
    def __init__(self, formula: str, use_c_api: bool = False):
        super().__init__(formula)
        self._intsort = z3.IntSort(ctx=CTX)
        self._use_c_api = use_c_api
        # self._vars = {str(var): var for var in z3util.get_vars(g.as_expr())}

    def _add_sample_via_c_api(self, sample: list[tuple[str, int]]):
        for var, value in sample:
            numeral = z3.Z3_mk_numeral(CTX.ref(), str(value), self._intsort.ast)
            z3.Z3_inc_ref(CTX.ref(), numeral)
            symbol = z3.Z3_mk_string_symbol(CTX.ref(), var)
            const = z3.Z3_mk_const(CTX.ref(), symbol, self._intsort.ast)
            # z3.Z3_inc_ref(CTX.ref(), const)
            eq = z3.Z3_mk_eq(CTX.ref(), const, numeral)
            # z3.Z3_inc_ref(CTX.ref(), const)
            z3.Z3_solver_assert(CTX.ref(), self._solver.solver, eq)
            # Now they are Z3's?
            z3.Z3_dec_ref(CTX.ref(), numeral)
            # z3.Z3_dec_ref(CTX.ref(), const)
            # z3.Z3_dec_ref(CTX.ref(), eq)

    def _add_sample_via_smtlib(self, sample: list[tuple[str, int]]):
        self._solver.from_string(
            "".join(
                f"(declare-fun {var} () Int)\n(assert (= {var} {value}))" # f"(assert (= {var} {value}))"
                for var, value in sample
            )
        )

    def _add_sample(self, sample: list[tuple[str, int]]):
        if self._use_c_api:
            self._add_sample_via_c_api(sample)
        else:
            self._add_sample_via_smtlib(sample)

    def _check_sample(self, sample: list[tuple[str, int]]) -> z3.CheckSatResult:
        # Easiest way to do this seems to just ask Z3...
        # Hope this isn't **too** costly.
        self._solver.push()
        self._add_sample(sample)
        r = self._solver.check()
        self._solver.pop()
        return r

    def count_sample(self, sample: list[tuple[str, int]]) -> bool:
        result = self._check_sample(sample)
        if result == z3.sat:
            self._satisfies += 1
        self._total += 1
        return result == z3.sat


class ManualSatisfiesMetric(Metric):
    def __init__(self, formula: str, statistics: typ.Optional[NodeStatistics] = None):
        super().__init__(formula)
        self._statistics = statistics
        expr = z3.And(self._solver.assertions())
        self._evaluator = self._build_evaluator(expr)

    def count_sample(self, sample: list[tuple[str, int]]):
        model = dict(sample)
        res = self._evaluator(model)
        if res:
            self._satisfies += 1
        self._total += 1
        return res

    @property
    def result(self) -> fractions.Fraction:
        if self._statistics:
            return self._statistics.result
        return super().result

    def _build_evaluator(self, expr: z3.Expr):
        return self._build_bool(expr)

    def _build_bool(self, expr):
        assert z3.is_bool(expr)
        if self._statistics:
            self._statistics.register_node(expr.get_id(), "bool")

        if z3.is_and(expr):
            return self._build_nary(expr, all, self._build_bool)
        elif z3.is_or(expr):
            return self._build_nary(expr, any, self._build_bool)
        elif z3.is_not(expr):
            return self._build_unary_not(expr)
        elif z3.is_le(expr):
            return self._build_binary(expr, operator.le, self._build_int)
        elif z3.is_lt(expr):
            return self._build_binary(expr, operator.lt, self._build_int)
        elif z3.is_gt(expr):
            return self._build_binary(expr, operator.gt, self._build_int)
        elif z3.is_ge(expr):
            return self._build_binary(expr, operator.ge, self._build_int)
        elif z3.is_eq(expr):
            if z3.is_bool(expr.children()[0]):
                sub_op = self._build_bool
            elif z3.is_int(expr.children()[0]):
                sub_op = self._build_int
            elif z3.is_array(expr.children()[0]):
                sub_op = self._build_array
            else:
                raise NotImplementedError(f"What is this? {expr}")
            return self._build_binary(expr, operator.eq, sub_op)
        elif z3.is_true(expr) or z3.is_false(expr):
            return self._build_leaf_literal(expr, bool(expr))
        elif z3.is_const(expr):
            return self._build_leaf_symbol(expr)
        raise Exception(f"Unhandled: {expr}")

    def _build_int(self, expr):
        assert z3.is_int(expr)
        if self._statistics:
            self._statistics.register_node(expr.get_id(), "int")

        if z3.is_add(expr):
            return self._build_nary(expr, sum, self._build_int)
        elif z3.is_sub(expr):
            return self._build_binary(expr, operator.sub, self._build_int)
        elif z3.is_mul(expr):
            return self._build_nary(expr, prod, self._build_int)
        elif z3.is_app_of(expr, z3.Z3_OP_UMINUS):
            return self._build_unary_minus(expr)
        elif z3.is_int_value(expr):
            return self._build_leaf_literal(expr, expr.as_long())
        elif z3.is_const(expr):
            return self._build_leaf_symbol(expr)
        elif z3.is_select(expr):
            return self._build_array_select(expr)
        elif z3.is_app_of(expr, z3.Z3_OP_ITE):
            return self._build_ite(expr)
        raise Exception(f"Unhandled: {expr}")

    def _build_ite(self, expr):
        node_id = expr.get_id()
        predicate = self._build_bool(expr.arg(0))

        if z3.is_int(expr):
            op = self._build_int
        elif z3.is_bool(expr):
            op = self._build_bool
        elif z3.is_array(expr):
            op = self._build_array
        else:
            raise Exception(f"Unhandled ite: {expr}")

        t_side = op(expr.arg(1))
        f_side = op(expr.arg(2))

        def e(model):
            if predicate(model):
                value = t_side(model)
            else:
                value = f_side(model)
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_array(self, expr):
        assert z3.is_array(expr)
        node_id = expr.get_id()
        if self._statistics:
            self._statistics.register_node(node_id, "array")

        if z3.is_const(expr):
            return self._build_array_leaf_symbol(expr)
        elif z3.is_store(expr):
            return self._build_array_store(expr)
        elif z3.is_app_of(expr, z3.Z3_OP_ITE):
            return self._build_ite(expr)
        raise Exception(f"Unhandled array: {expr}")

    def _build_array_leaf_symbol(self, expr):
        name = expr.decl().name()
        node_id = expr.get_id()

        def e(model):
            value = model.get(name, collections.defaultdict(int))
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_array_store(self, expr):
        node_id = expr.get_id()
        array = self._build_array(expr.arg(0))
        index = self._build_int(expr.arg(1))
        store = self._build_int(expr.arg(2))

        def e(model):
            old_value = array(model)
            value = collections.defaultdict(old_value.default_factory, old_value)
            value[index(model)] = store(model)
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_array_select(self, expr):
        node_id = expr.get_id()
        array = self._build_array(expr.arg(0))
        index = self._build_int(expr.arg(1))

        def e(model):
            value = array(model)[index(model)]
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_nary(self, expr, op, subtype):
        children = [subtype(subexpr) for subexpr in expr.children()]
        node_id = expr.get_id()

        def e(model):
            value = op(child(model) for child in children)
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_binary(self, expr, op, subtype):
        left, right = [subtype(subexpr) for subexpr in expr.children()]
        node_id = expr.get_id()

        def e(model):
            value = op(left(model), right(model))
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_unary_not(self, expr):
        child = self._build_bool(expr.arg(0))
        node_id = expr.get_id()

        def e(model):
            value = not child(model)
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_unary_minus(self, expr):
        child = self._build_int(expr.arg(0))
        node_id = expr.get_id()

        def e(model):
            value = -child(model)
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_leaf_symbol(self, expr):
        name = expr.decl().name()
        node_id = expr.get_id()

        def e(model):
            value = model.get(name, 0)
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e

    def _build_leaf_literal(self, expr, value):
        node_id = expr.get_id()

        def e(model):
            if self._statistics:
                self._statistics.evaluate_node(node_id, value)
            return value

        return e


class NodeStatistics(abc.ABC):
    def __init__(self):
        self._storage: typ.MutableMapping[int, tuple[typ.Any, typ.Any, str]] = {}
        self._totals: typ.Optional[typ.MutableMapping[int, int]] = None

    @abc.abstractmethod
    def register_node(self, node_id: int, sort: str):
        pass

    @abc.abstractmethod
    def evaluate_node(self, node_id: int, value: typ.Any):
        pass

    def set_totals(self, totals: typ.MutableMapping[int, typ.Any]):
        self._totals = totals

    @property
    @abc.abstractmethod
    def result(self) -> fractions.Fraction:
        pass


class WireCoverageStatistics(NodeStatistics):
    MASK = 2**64 - 1

    def register_node(self, node_id: int, sort: str):
        if sort == "bool":
            self._storage[node_id] = (False, False, sort)
        elif sort == "int":
            self._storage[node_id] = (0, 0, sort)
        elif sort == "array":
            self._storage[node_id] = (None, None, sort)  # TODO: ???
        else:
            raise Exception(f"Unhandled: {sort}")

    def evaluate_node(self, node_id: int, value: typ.Any):
        old_true, old_false, sort = self._storage[node_id]
        if sort == "bool":
            self._storage[node_id] = (old_true or value, old_false or not value, sort)
        elif sort == "int":
            self._storage[node_id] = (
                old_true | (value & self.MASK),
                old_false | ((value & self.MASK) ^ self.MASK),
                sort,
            )
        elif sort == "array":
            pass  # TODO: ???
        else:
            raise Exception(f"Unhandled: {sort}")

    def node_total(self, node_id: int, sort: str) -> int:
        if self._totals:
            return self._totals[node_id]

        if sort == "bool":
            return 1
        elif sort == "int":
            return bin(self.MASK).count("1")
        elif sort == "array":
            return 0  # TODO: ???
        else:
            raise ValueError(f"Unhandled: {sort}")

    def node_count(self, true_count, false_count, sort: str) -> int:
        if sort == "bool":
            return 1 if true_count and false_count else 0
        elif sort == "int":
            return bin(true_count & false_count).count("1")
        elif sort == "array":
            return 0
        else:
            raise ValueError(f"Unhandled: {sort}")

    @property
    def result(self) -> fractions.Fraction:
        count = 0
        total = 0
        for key, (true_count, false_count, sort) in self._storage.items():
            total += self.node_total(key, sort)
            count += self.node_count(true_count, false_count, sort)

        percentage = float(Fraction(count, total) * 100)
        
        return f"{round(percentage, 2)}%"

    @classmethod
    def union_totals(
        cls, *statistics: WireCoverageStatistics
    ) -> typ.MutableMapping[int, int]:
        ret: typ.MutableMapping[int, int] = {}
        if not statistics:
            raise ValueError("Needs at least one statistics")
        for key in statistics[0]._storage:
            sort = statistics[0]._storage[key][2]
            if sort == "bool":
                ret[key] = (
                    1
                    if any(
                        s._storage[key][0] and s._storage[key][1] for s in statistics
                    )
                    else 0
                )
            elif sort == "int":
                v = 0
                for s in statistics:
                    t, f, _ = s._storage[key]
                    v |= t & f
                ret[key] = bin(v).count("1")
            elif sort == "array":
                ret[key] = 0
            else:
                raise ValueError(f"Unhandled: {sort}")
        return ret


def _load_formula(f: typ.TextIO) -> str:
    return f.read()


def _apply_metric(
    metric: Metric,
    samples: typ.Iterator[list[tuple[str, int]]],
    limit: int = 0,
    print_first: bool = False,
):
    if limit > 0:
        samples = itertools.islice(samples, limit)
    first = True
    for sample in samples:
        sat = metric.count_sample(sample)
        if sat and print_first and first:
            first = False
            print(sample)


def parse_samples(f: typ.TextIO) -> typ.Iterator[list[tuple[str, int]]]:
    def parse_int(value):
        return int(value.strip("()").replace(" ", ""))

    def parse_array(value):
        splitted = value.strip("[],").split(",")
        default = int(splitted[1])
        assert len(splitted) - 2 == int(splitted[0])
        return collections.defaultdict(
            lambda: default, (x.split("->") for x in splitted[2:])
        )

    def to_tuple(sample):
        var, value = sample.split(":")
        if not value:
            raise ValueError("value empty, error !")
        return var, parse_array(value) if value[0] == "[" else parse_int(value)

    for line in f:
        p = line.split(" ", maxsplit=1)[1].strip("; \n").split(";")
        yield [to_tuple(x) for x in p]


def calc_metric(
    _formula: typ.TextIO,
    _samples: typ.TextIO,
    _metric: str,
    _use_c_api=False,
    limit=0,
    print_first=False,
) -> fractions.Fraction:
    formula = _load_formula(_formula)
    samples = parse_samples(_samples)

    if _metric == "satisfies":
        metric: Metric = SatisfiesMetric(formula, use_c_api=_use_c_api)
    elif _metric == "wire_coverage":
        metric = ManualSatisfiesMetric(formula, statistics=WireCoverageStatistics())

    _apply_metric(metric, samples, limit, print_first)
    return metric.result


def main():
    args = PARSER.parse_args(sys.argv[1:])

    print(
        calc_metric(
            args.formula,
            args.samples,
            args.metric,
            _use_c_api=args.use_c_api,
            limit=args.limit,
            print_first=args.print_first,
        )
    )


if __name__ == "__main__":
    main()
