"""Geometry-only measurements for reconstructed visible target regions."""

from __future__ import annotations

import itertools
import math
from typing import Sequence


Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]


def _dot(first: Vec3, second: Vec3) -> float:
    return sum(lhs * rhs for lhs, rhs in zip(first, second))


def _cross(first: Vec3, second: Vec3) -> Vec3:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _normalize(vector: Vec3) -> Vec3 | None:
    length = math.sqrt(_dot(vector, vector))
    if not math.isfinite(length) or length <= 1.0e-15:
        return None
    return tuple(value / length for value in vector)  # type: ignore[return-value]


def _projection_basis(vertices: Sequence[Vec3]) -> tuple[Vec3, Vec3, Vec3] | None:
    center = _normalize(
        tuple(sum(vertex[axis] for vertex in vertices) for axis in range(3))
    )
    if center is None:
        return None
    reference = min(
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        key=lambda axis: abs(_dot(center, axis)),
    )
    first = _normalize(_cross(reference, center))
    if first is None:
        return None
    second = _cross(center, first)
    return center, first, second


def _project(direction: Vec3, basis: tuple[Vec3, Vec3, Vec3]) -> Vec2 | None:
    center, first, second = basis
    denominator = _dot(direction, center)
    if denominator <= 1.0e-12:
        return None
    return (
        _dot(direction, first) / denominator,
        _dot(direction, second) / denominator,
    )


def _polygon_area(vertices: Sequence[Vec2]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(vertices, vertices[1:] + vertices[:1])
    )


def _edge_constraints(vertices: Sequence[Vec2]) -> tuple[tuple[float, float, float], ...]:
    orientation = 1.0 if _polygon_area(vertices) > 0.0 else -1.0
    constraints = []
    for first, second in zip(vertices, vertices[1:] + vertices[:1]):
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        length = math.hypot(dx, dy)
        if length <= 1.0e-15:
            continue
        nx = orientation * -dy / length
        ny = orientation * dx / length
        constraints.append((nx, ny, nx * first[0] + ny * first[1]))
    return tuple(constraints)


def _minimum_margin(point: Vec2, constraints: Sequence[tuple[float, float, float]]) -> float:
    return min(nx * point[0] + ny * point[1] - offset for nx, ny, offset in constraints)


def _solve_three(rows: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float] | None:
    matrix = [list(row) for row in rows]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) <= 1.0e-14:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        scale = matrix[column][column]
        for index in range(column, 4):
            matrix[column][index] /= scale
        for row in range(3):
            if row == column:
                continue
            factor = matrix[row][column]
            for index in range(column, 4):
                matrix[row][index] -= factor * matrix[column][index]
    return matrix[0][3], matrix[1][3], matrix[2][3]


def _polygon_inradius(vertices: Sequence[Vec2]) -> float:
    constraints = _edge_constraints(vertices)
    if len(constraints) < 3:
        return math.nan
    centroid = (
        sum(point[0] for point in vertices) / len(vertices),
        sum(point[1] for point in vertices) / len(vertices),
    )
    best = max(0.0, _minimum_margin(centroid, constraints))
    for active in itertools.combinations(constraints, 3):
        solution = _solve_three(
            tuple((nx, ny, -1.0, offset) for nx, ny, offset in active)
        )
        if solution is None:
            continue
        x, y, radius = solution
        if radius < -1.0e-10:
            continue
        margin = _minimum_margin((x, y), constraints)
        if margin + 1.0e-9 >= radius:
            best = max(best, margin)
    return best


def boundary_clearance_ratio(
    direction: Vec3,
    components: Sequence[Sequence[Vec3]],
) -> float:
    """Return component-wise boundary clearance normalized by global inradius.

    Components are gnomonically projected so spherical great-circle boundaries
    remain straight. Overlapping components are evaluated independently; taking
    the best containing component is a conservative approximation of union
    clearance without constructing a polygon union.
    """

    normalized_direction = _normalize(direction)
    if normalized_direction is None:
        return math.nan
    maximum_radius = 0.0
    containing_clearance = -math.inf
    for raw_vertices in components:
        normalized_vertices = tuple(
            vertex
            for raw in raw_vertices
            if (vertex := _normalize(tuple(raw))) is not None
        )
        if len(normalized_vertices) < 3:
            continue
        basis = _projection_basis(normalized_vertices)
        if basis is None:
            continue
        polygon = tuple(_project(vertex, basis) for vertex in normalized_vertices)
        point = _project(normalized_direction, basis)
        if point is None or any(vertex is None for vertex in polygon):
            continue
        projected = tuple(vertex for vertex in polygon if vertex is not None)
        constraints = _edge_constraints(projected)
        if len(constraints) < 3:
            continue
        radius = _polygon_inradius(projected)
        if math.isfinite(radius):
            maximum_radius = max(maximum_radius, radius)
        clearance = _minimum_margin(point, constraints)
        if clearance >= -1.0e-9:
            containing_clearance = max(containing_clearance, max(0.0, clearance))
    if maximum_radius <= 0.0 or not math.isfinite(containing_clearance):
        return math.nan
    return min(1.0, max(0.0, containing_clearance / maximum_radius))
