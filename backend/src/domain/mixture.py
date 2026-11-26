"""One-dimensional Gaussian mixture fitted by maximum-likelihood EM."""

from collections import Counter
from dataclasses import dataclass
from math import exp, log, pi

VARIANCE_FLOOR = 0.25 ** 2
MAX_ITERATIONS = 180
CONVERGENCE_PER_POINT = 1e-7


@dataclass(frozen=True)
class Component:
    weight: float
    mean: float
    variance: float


@dataclass(frozen=True)
class Fit:
    components: tuple[Component, ...]
    log_likelihood: float
    iterations: int
    converged: bool


class EmptyComponent(ValueError):
    """A numerical component has too little mass to update its moments."""


def evaluate(components: tuple[Component, ...], value: float) -> tuple[float, tuple[float, ...]]:
    return _posterior(_terms(components), value)


def _terms(components):
    return tuple((component.mean, 0.5 / component.variance,
                  log(component.weight) - 0.5 * log(2 * pi * component.variance))
                 for component in components)


def _posterior(terms, value):
    scores = [log_normalizer - half_precision * (value - mean) ** 2
              for mean, half_precision, log_normalizer in terms]
    peak = max(scores)
    scaled = [exp(score - peak) for score in scores]
    total = sum(scaled)
    return peak + log(total), tuple(score / total for score in scaled)


def _expectation(counts, components):
    terms = _terms(components)
    likelihood = 0.0
    assigned = []
    for value, count in counts:
        density, posterior = _posterior(terms, value)
        likelihood += count * density
        assigned.append((value, tuple(count * weight for weight in posterior)))
    return likelihood, assigned


def _maximize(assigned, size, n):
    components = []
    for index in range(size):
        mass = sum(weights[index] for _, weights in assigned)
        if mass <= 1e-10:
            raise EmptyComponent('成分的有效样本量过小。')
        center = sum(value * weights[index] for value, weights in assigned) / mass
        variance = sum(weights[index] * (value - center) ** 2 for value, weights in assigned) / mass
        components.append(Component(mass / n, center, max(variance, VARIANCE_FLOOR)))
    return tuple(components)


def fit_em(values: list[float], initial_means: tuple[float, ...]) -> Fit:
    n, size = len(values), len(initial_means)
    counts = sorted(Counter(values).items())
    center = sum(values) / n
    variance = sum((value - center) ** 2 for value in values) / n
    spread = max(variance / size ** 2, VARIANCE_FLOOR)
    components = tuple(Component(1 / size, mean, spread) for mean in initial_means)
    likelihood, assigned = _expectation(counts, components)
    for iteration in range(1, MAX_ITERATIONS + 1):
        components = _maximize(assigned, size, n)
        updated, assigned = _expectation(counts, components)
        if abs(updated - likelihood) / n <= CONVERGENCE_PER_POINT:
            return Fit(tuple(sorted(components, key=lambda item: item.mean)), updated, iteration, True)
        likelihood = updated
    return Fit(tuple(sorted(components, key=lambda item: item.mean)), likelihood, MAX_ITERATIONS, False)
