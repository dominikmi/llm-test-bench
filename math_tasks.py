"""Ten practical math tasks implemented with only the Python standard library.

Each task is chosen to exercise a distinct practical domain where accuracy and
speed matter: floating-point summation, number theory, modular arithmetic,
multi-operand integer arithmetic, linear algebra, numerical root finding,
probabilistic estimation, computational geometry, numerical integration, and
recurrence relations.  Implementations avoid third-party libraries and rely on
built-in / stdlib primitives that are already optimized in C.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Sequence
from functools import reduce
from typing import TypeAlias

Number: TypeAlias = int | float
Matrix: TypeAlias = list[list[Number]]
Point: TypeAlias = tuple[float, float]


def _truncate(value: object, max_len: int = 80) -> str:
    """Return a short string representation for console output."""
    text = repr(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _timed(label: str, fn: Callable[[], object]) -> object:
    """Run *fn*, print elapsed time and truncated result, then return result."""
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    print(f"{label}: {_truncate(result)} (elapsed {elapsed:.6f}s)")
    return result


# ---------------------------------------------------------------------------
# 1. Accurate floating-point summation
# ---------------------------------------------------------------------------
def accurate_sum(values: Sequence[float]) -> float:
    """Return the correctly rounded sum of *values* using ``math.fsum``.

    ``math.fsum`` tracks multiple intermediate partial sums, which eliminates the
    catastrophic cancellation that a plain ``sum()`` can produce when values
    differ widely in magnitude.
    """
    return math.fsum(values)


# ---------------------------------------------------------------------------
# 2. Sieve of Eratosthenes
# ---------------------------------------------------------------------------
def prime_sieve(limit: int) -> list[int]:
    """Return all prime numbers <= *limit*.

    Uses a ``bytearray`` as a compact bitmap and slice assignment to mark
    multiples without a Python-level inner loop.
    """
    if limit < 2:
        return []
    sieve = bytearray(b"\x01") * (limit + 1)
    sieve[0:2] = b"\x00\x00"
    bound = math.isqrt(limit) + 1
    for p in range(2, bound):
        if sieve[p]:
            start = p * p
            step = p
            count = (limit - start) // step + 1
            sieve[start : limit + 1 : step] = b"\x00" * count
    return [i for i, is_prime in enumerate(sieve) if is_prime]


# ---------------------------------------------------------------------------
# 3. Modular exponentiation
# ---------------------------------------------------------------------------
def modular_pow(base: int, exp: int, mod: int) -> int:
    """Return (base ** exp) % mod.

    Python's three-argument ``pow`` uses exponentiation by squaring in C and
    is the fastest available native implementation.
    """
    return pow(base, exp, mod)


# ---------------------------------------------------------------------------
# 4. GCD and LCM for many integers
# ---------------------------------------------------------------------------
def gcd_lcm(numbers: Sequence[int]) -> tuple[int, int]:
    """Return (gcd, lcm) of a sequence of positive integers.

    GCD is built pairwise with ``math.gcd``; LCM uses ``a // gcd(a, b) * b`` to
    avoid intermediate overflow where possible.
    """
    if not numbers:
        raise ValueError("sequence must be non-empty")
    g = reduce(math.gcd, numbers)

    def _lcm(a: int, b: int) -> int:
        return a // math.gcd(a, b) * b

    l = reduce(_lcm, numbers)
    return g, l


# ---------------------------------------------------------------------------
# 5. Matrix multiplication
# ---------------------------------------------------------------------------
def matrix_multiply(a: Matrix, b: Matrix) -> Matrix:
    """Multiply two matrices.

    Transposes the right operand so the inner loop iterates over contiguous
    tuples, which is much faster in pure Python than repeated ``b[k][j]``
    indexing.
    """
    if not a or not a[0] or not b or not b[0]:
        raise ValueError("matrices must be non-empty")
    if len(a[0]) != len(b):
        raise ValueError("incompatible matrix dimensions")
    b_t = list(zip(*b))
    return [
        [sum(x * y for x, y in zip(row, col)) for col in b_t]
        for row in a
    ]


# ---------------------------------------------------------------------------
# 6. Newton-Raphson square root
# ---------------------------------------------------------------------------
def newton_sqrt(value: float, eps: float = 1e-12) -> float:
    """Compute sqrt(*value*) with Newton-Raphson iteration.

    Converges quadratically and uses only basic arithmetic, making it a useful
    demonstration of numerical root finding.
    """
    if value < 0:
        raise ValueError("square root of negative number")
    if value == 0:
        return 0.0
    guess = value
    while True:
        next_guess = 0.5 * (guess + value / guess)
        if abs(next_guess - guess) <= eps:
            return next_guess
        guess = next_guess


# ---------------------------------------------------------------------------
# 7. Monte Carlo pi
# ---------------------------------------------------------------------------
def monte_carlo_pi(samples: int, seed: int = 42) -> float:
    """Estimate pi by sampling random points in the unit square.

    The ratio of points that fall inside the quarter-unit-circle converges to
    ``pi / 4``.  Using a local ``Random`` instance keeps the run reproducible.
    """
    if samples <= 0:
        raise ValueError("samples must be positive")
    rng = random.Random(seed)
    inside = 0
    for _ in range(samples):
        x = rng.random()
        y = rng.random()
        if x * x + y * y <= 1.0:
            inside += 1
    return 4.0 * inside / samples


# ---------------------------------------------------------------------------
# 8. Convex hull (Andrew's monotone chain)
# ---------------------------------------------------------------------------
def _cross(o: Point, a: Point, b: Point) -> float:
    """2-D cross product of OA x OB."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points: Sequence[Point]) -> list[Point]:
    """Compute the convex hull of 2-D points.

    Andrew's monotone chain runs in O(n log n) because of the initial sort;
    the two scans are linear.  The cross-product test removes points that would
    create non-left turns.
    """
    pts = sorted(set(points))
    if len(pts) <= 1:
        return list(pts)

    lower: list[Point] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------
# 9. Numerical integration (Simpson's rule)
# ---------------------------------------------------------------------------
def simpson_integration(
    f: Callable[[float], float], a: float, b: float, n: int = 1000
) -> float:
    """Approximate the definite integral of *f* over [a, b] with Simpson's rule.

    Global error is O(h^4).  The implementation builds a list of weighted terms
    and then uses ``math.fsum`` for a correctly rounded final sum.
    """
    if n % 2:
        n += 1
    h = (b - a) / n
    terms: list[float] = [f(a), f(b)]
    for i in range(1, n):
        x = a + i * h
        weight = 4 if i % 2 else 2
        terms.append(weight * f(x))
    return math.fsum(terms) * h / 3.0


# ---------------------------------------------------------------------------
# 10. Fast Fibonacci via matrix exponentiation
# ---------------------------------------------------------------------------
def _mat_mul_2x2(a: Matrix, b: Matrix) -> Matrix:
    """Multiply two 2x2 integer matrices."""
    return [
        [
            a[0][0] * b[0][0] + a[0][1] * b[1][0],
            a[0][0] * b[0][1] + a[0][1] * b[1][1],
        ],
        [
            a[1][0] * b[0][0] + a[1][1] * b[1][0],
            a[1][0] * b[0][1] + a[1][1] * b[1][1],
        ],
    ]


def _mat_pow_2x2(matrix: Matrix, power: int) -> Matrix:
    """Raise a 2x2 matrix to *power* by binary exponentiation."""
    if power < 0:
        raise ValueError("power must be non-negative")
    result: Matrix = [[1, 0], [0, 1]]
    base = [row[:] for row in matrix]
    while power:
        if power & 1:
            result = _mat_mul_2x2(result, base)
        base = _mat_mul_2x2(base, base)
        power >>= 1
    return result


def fibonacci(n: int) -> int:
    """Return the n-th Fibonacci number in O(log n) time.

    Uses the identity::

        [[1, 1], [1, 0]]^n = [[F(n+1), F(n)], [F(n), F(n-1)]]

    computed with binary matrix exponentiation.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if n < 2:
        return n
    matrix = [[1, 1], [1, 0]]
    result = _mat_pow_2x2(matrix, n - 1)
    return result[0][0]


# ---------------------------------------------------------------------------
# Verification harness
# ---------------------------------------------------------------------------
def _verify() -> None:
    """Quick sanity checks for every function."""
    assert accurate_sum([1e100, 1.0, -1e100]) == 1.0
    assert prime_sieve(10) == [2, 3, 5, 7]
    assert modular_pow(2, 10, 1000) == 24
    assert gcd_lcm([4, 6, 8]) == (2, 24)
    assert matrix_multiply([[1, 2], [3, 4]], [[5, 6], [7, 8]]) == [
        [19, 22],
        [43, 50],
    ]
    assert abs(newton_sqrt(2.0) - math.sqrt(2.0)) < 1e-12
    assert fibonacci(10) == 55
    hull = convex_hull([(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)])
    assert len(hull) == 4
    assert abs(simpson_integration(lambda x: x * x, 0.0, 1.0, 1000) - 1.0 / 3.0) < 1e-10
    pi_est = monte_carlo_pi(200_000)
    assert 3.1 < pi_est < 3.2


# ---------------------------------------------------------------------------
# Demonstration / benchmark runner
# ---------------------------------------------------------------------------
def main() -> None:
    """Run each task on a representative input and report timing."""
    print("=" * 60)
    print("Native Python math tasks")
    print("=" * 60)

    _verify()
    print("Sanity checks passed.")
    print()

    # 1. Accurate summation
    values = [1e100, 1.0, -1e100] * 1000 + [0.1] * 10
    fsum = _timed("1. accurate_sum(3k mixed-magnitude floats)", lambda: accurate_sum(values))
    print(f"    naive sum: {sum(values):.6f}  |  fsum: {fsum:.6f}")

    # 2. Prime sieve
    prime_count = _timed("2. prime_sieve(1_000_000)", lambda: len(prime_sieve(1_000_000)))
    print(f"    primes <= 1_000_000: {prime_count}")

    # 3. Modular exponentiation
    mod_result = _timed(
        "3. modular_pow(2, 1_000_000, 1_000_000_007)",
        lambda: modular_pow(2, 1_000_000, 1_000_000_007),
    )
    print(f"    2^1_000_000 mod 1_000_000_007 = {mod_result}")

    # 4. GCD / LCM
    g, l = _timed("4. gcd_lcm(range(1, 51))", lambda: gcd_lcm(list(range(1, 51))))
    print(f"    gcd={g}, lcm has {len(str(l))} digits")

    # 5. Matrix multiplication
    size = 64
    a = [[i + j for j in range(size)] for i in range(size)]
    b = [[i - j for j in range(size)] for i in range(size)]
    c = _timed(f"5. matrix_multiply({size}x{size})", lambda: matrix_multiply(a, b))
    print(f"    C[0][0]={c[0][0]}, C[{size // 2}][{size // 2}]={c[size // 2][size // 2]}")

    # 6. Newton's square root
    sqrt2 = _timed("6. newton_sqrt(2.0)", lambda: newton_sqrt(2.0))
    print(f"    math.sqrt(2)={math.sqrt(2.0):.15f}, newton={sqrt2:.15f}")

    # 7. Monte Carlo pi
    pi_est = _timed("7. monte_carlo_pi(2_000_000)", lambda: monte_carlo_pi(2_000_000))
    print(f"    math.pi={math.pi:.10f}, estimate={pi_est:.10f}")

    # 8. Convex hull
    rng = random.Random(42)
    pts = [(rng.uniform(-100, 100), rng.uniform(-100, 100)) for _ in range(10_000)]
    hull = _timed("8. convex_hull(10_000 points)", lambda: convex_hull(pts))
    print(f"    hull has {len(hull)} points")

    # 9. Numerical integration
    integral = _timed(
        "9. simpson_integration(exp(-x^2), 0, 2, n=100_000)",
        lambda: simpson_integration(lambda x: math.exp(-x * x), 0.0, 2.0, n=100_000),
    )
    print(f"    estimated integral={integral:.12f}")

    # 10. Fibonacci
    fib_n = _timed("10. fibonacci(1_000_000)", lambda: fibonacci(1_000_000))
    print(f"    F(1_000_000) has {len(str(fib_n))} digits")


if __name__ == "__main__":
    main()
