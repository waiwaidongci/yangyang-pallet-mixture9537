"""Deterministic multistart EM, candidate diagnostics and BIC selection."""

from dataclasses import dataclass
from math import log
from random import Random

from .mixture import EmptyComponent, Fit, fit_em

INITIALIZATION_SEED = 6149
RANDOM_STARTS = 2


@dataclass(frozen=True)
class Candidate:
    size: int
    parameters: int
    attempts: int
    converged_starts: int
    fit: Fit | None
    bic: float | None


def _initializations(values: list[float], distinct: list[float], size: int):
    n = len(values)
    quantile_centers = tuple(values[min(n - 1, (2 * i + 1) * n // (2 * size))] for i in range(size))
    if size == 1:
        return (quantile_centers,)
    random = Random(INITIALIZATION_SEED + size)
    starts = [quantile_centers]
    for _ in range(RANDOM_STARTS):
        starts.append(tuple(sorted(random.sample(distinct, size))))
    return tuple(dict.fromkeys(starts))


def select_model(values: list[float], max_components: int) -> tuple[Candidate, list[Candidate]]:
    values = sorted(values)
    distinct = sorted(set(values))
    candidates = []
    for size in range(1, max_components + 1):
        parameters = 3 * size - 1
        starts = _initializations(values, distinct, size) if size <= len(distinct) else ()
        fitted = []
        for means in starts:
            try:
                fitted.append(fit_em(values, means))
            except EmptyComponent:
                continue
        converged = [fit for fit in fitted if fit.converged]
        best = max(converged or fitted, key=lambda fit: fit.log_likelihood, default=None)
        bic = parameters * log(len(values)) - 2 * best.log_likelihood if converged else None
        candidates.append(Candidate(size, parameters, len(starts), len(converged), best, bic))
    selected = min((item for item in candidates if item.bic is not None), key=lambda item: (item.bic, item.size))
    return selected, candidates
