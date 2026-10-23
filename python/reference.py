"""Independent, deliberately slow mathematical oracles; never benchmarked."""
from fractions import Fraction
from math import gcd, lcm


def count_bit_hits(alpha, beta, j, desired, lower, upper):
    return sum(((beta + alpha*z) >> j) & 1 == desired for z in range(lower, upper+1))


def witnesses(alpha, beta, j, desired, lower, upper):
    return [z for z in range(lower, upper+1) if ((beta + alpha*z) >> j) & 1 == desired]


def integer_directions(matrix, columns):
    """Exact rational nullspace with cleared denominators, not a full lattice basis."""
    rows = [[Fraction(v) for v in r] for r in matrix]
    pivots, rank = [], 0
    for col in range(columns):
        pivot = next((i for i in range(rank, len(rows)) if rows[i][col]), None)
        if pivot is None:
            continue
        rows[rank], rows[pivot] = rows[pivot], rows[rank]
        scale = rows[rank][col]
        rows[rank] = [x / scale for x in rows[rank]]
        for i in range(len(rows)):
            if i != rank:
                scale = rows[i][col]
                rows[i] = [a-scale*b for a, b in zip(rows[i], rows[rank])]
        pivots.append(col)
        rank += 1
    out = []
    for free in set(range(columns)) - set(pivots):
        vector = [Fraction(int(i == free)) for i in range(columns)]
        for i, p in enumerate(pivots):
            vector[p] = -rows[i][free]
        denominator = lcm(*(q.denominator for q in vector))
        out.append([int(q*denominator) for q in vector])
    return out
