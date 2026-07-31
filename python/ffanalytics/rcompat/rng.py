"""R's default random number generator, reproduced exactly.

``score_dst_pts_allowed()`` (``R/calc_projections.R:181-216``) calls
``set.seed(1L)`` and then ``rnorm()`` to simulate a season of games when
projecting season-long DST points allowed.  NumPy cannot reproduce those draws:
R seeds Mersenne-Twister through its own scrambler and generates normals by
*inversion*, not Box-Muller or the ziggurat.

This module implements R's chain end to end -- ``RNG_Init`` scrambling,
``MT_genrand``, and ``norm_rand``'s inversion via Wichura's AS241 ``qnorm`` --
so seeded output matches R bit for bit.  Ported from R's ``src/main/RNG.c``
and ``src/nmath/qnorm.c``.
"""

from __future__ import annotations

__all__ = ["RRandom", "qnorm"]

_N = 624
_M = 397
_MATRIX_A = 0x9908B0DF
_UPPER_MASK = 0x80000000
_LOWER_MASK = 0x7FFFFFFF
_TEMPERING_MASK_B = 0x9D2C5680
_TEMPERING_MASK_C = 0xEFC60000

_UINT32 = 0xFFFFFFFF
_I2_32M1 = 2.328306437080797e-10  # 1 / (2^32 - 1)
_BIG = 134217728.0  # 2^27


def qnorm(p: float, mean: float = 0.0, sd: float = 1.0) -> float:
    """Quantile function of the normal distribution (R's ``qnorm``).

    Algorithm AS 241 (Wichura 1988), the same routine R uses, so that
    inversion-based ``rnorm`` matches R exactly.  SciPy's ``ndtri`` is a
    different implementation and can disagree in the last ulp, which is enough
    to change a rounded simulated score.
    """
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")

    q = p - 0.5
    if abs(q) <= 0.425:
        r = 0.180625 - q * q
        num = (
            (
                (
                    (
                        (
                            ((r * 2509.0809287301226727 + 33430.575583588128105) * r
                             + 67265.770927008700853) * r
                            + 45921.953931549871457
                        ) * r
                        + 13731.693765509461125
                    ) * r
                    + 1971.5909503065514427
                ) * r
                + 133.14166789178437745
            ) * r
            + 3.387132872796366608
        )
        den = (
            (
                (
                    (
                        (
                            ((r * 5226.495278852854561 + 28729.085735721942674) * r
                             + 39307.89580009271061) * r
                            + 21213.794301586595867
                        ) * r
                        + 5394.1960214247511077
                    ) * r
                    + 687.1870074920579083
                ) * r
                + 42.313330701600911252
            ) * r
            + 1.0
        )
        return mean + sd * (q * num / den)

    import math

    r = (1.0 - p) if q > 0 else p
    r = math.sqrt(-math.log(r))

    if r <= 5.0:
        r -= 1.6
        num = (
            (
                (
                    (
                        (
                            (
                                (r * 7.7454501427834140764e-4 + 0.0227238449892691845833) * r
                                + 0.24178072517745061177
                            ) * r
                            + 1.27045825245236838258
                        ) * r
                        + 3.64784832476320460504
                    ) * r
                    + 5.7694972214606914055
                ) * r
                + 4.6303378461565452959
            ) * r
            + 1.42343711074968357734
        )
        den = (
            (
                (
                    (
                        (
                            (
                                (r * 1.05075007164441684324e-9 + 5.475938084995344946e-4) * r
                                + 0.0151986665636164571966
                            ) * r
                            + 0.14810397642748007459
                        ) * r
                        + 0.68976733498510000455
                    ) * r
                    + 1.6763848301838038494
                ) * r
                + 2.05319162663775882187
            ) * r
            + 1.0
        )
    else:
        r -= 5.0
        num = (
            (
                (
                    (
                        (
                            (
                                (r * 2.01033439929228813265e-7 + 2.71155556874348757815e-5) * r
                                + 0.0012426609473880784386
                            ) * r
                            + 0.026532189526576123093
                        ) * r
                        + 0.29656057182850489123
                    ) * r
                    + 1.7848265399172913358
                ) * r
                + 5.4637849111641143699
            ) * r
            + 6.6579046435011037772
        )
        den = (
            (
                (
                    (
                        (
                            (
                                (r * 2.04426310338993978564e-15 + 1.4215117583164458887e-7) * r
                                + 1.8463183175100546818e-5
                            ) * r
                            + 7.868691311456132591e-4
                        ) * r
                        + 0.0148753612908506148525
                    ) * r
                    + 0.13692988092273580531
                ) * r
                + 0.59983220655588793769
            ) * r
            + 1.0
        )

    value = num / den
    if q < 0.0:
        value = -value
    return mean + sd * value


class RRandom:
    """R's Mersenne-Twister stream, seeded the way ``set.seed()`` seeds it."""

    __slots__ = ("_mt", "_mti")

    def __init__(self, seed: int) -> None:
        self.set_seed(seed)

    def set_seed(self, seed: int) -> None:
        """Equivalent to R's ``set.seed(seed)`` with the default RNG kind."""
        seed &= _UINT32
        # R's RNG_Init scrambles the user seed 50 times first ...
        for _ in range(50):
            seed = (69069 * seed + 1) & _UINT32
        # ... then fills all 625 words of i_seed (i_seed[0] is the position).
        state = []
        for _ in range(_N + 1):
            seed = (69069 * seed + 1) & _UINT32
            state.append(seed)
        self._mt = state[1:]
        # FixupSeeds forces a full regeneration on the first draw.
        self._mti = _N

    def _genrand(self) -> float:
        mt, mti = self._mt, self._mti

        if mti >= _N:
            for kk in range(_N - _M):
                y = (mt[kk] & _UPPER_MASK) | (mt[kk + 1] & _LOWER_MASK)
                mt[kk] = mt[kk + _M] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            for kk in range(_N - _M, _N - 1):
                y = (mt[kk] & _UPPER_MASK) | (mt[kk + 1] & _LOWER_MASK)
                mt[kk] = mt[kk + (_M - _N)] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            y = (mt[_N - 1] & _UPPER_MASK) | (mt[0] & _LOWER_MASK)
            mt[_N - 1] = mt[_M - 1] ^ (y >> 1) ^ (_MATRIX_A if y & 1 else 0)
            mti = 0

        y = mt[mti]
        mti += 1
        y ^= y >> 11
        y ^= (y << 7) & _TEMPERING_MASK_B
        y ^= (y << 15) & _TEMPERING_MASK_C
        y ^= y >> 18
        y &= _UINT32

        self._mti = mti
        return y * 2.3283064365386963e-10

    def unif_rand(self) -> float:
        """One draw from ``runif(1)``, including R's ``fixup`` clamping."""
        x = self._genrand()
        if x <= 0.0:
            return 0.5 * _I2_32M1
        if 1.0 - x <= 0.0:
            return 1.0 - 0.5 * _I2_32M1
        return x

    def norm_rand(self) -> float:
        """One draw from ``rnorm(1)`` using R's INVERSION method."""
        u1 = self.unif_rand()
        u1 = int(_BIG * u1) + self.unif_rand()
        return qnorm(u1 / _BIG)

    def runif(self, n: int, minimum: float = 0.0, maximum: float = 1.0) -> list[float]:
        span = maximum - minimum
        return [minimum + span * self.unif_rand() for _ in range(n)]

    def rnorm(self, n: int, mean: float = 0.0, sd: float = 1.0) -> list[float]:
        return [mean + sd * self.norm_rand() for _ in range(n)]
