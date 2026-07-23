"""Canonical millimetre geometry for cutter preview and HPGL output."""

from __future__ import annotations

import math
import os
import tempfile
import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Sequence, Tuple

from preview import PreviewError, parse_hpgl


Point = Tuple[float, float]
Path = List[Point]
MM_PER_SVG_UNIT = 25.4 / 96.0
HPGL_UNITS_PER_MM = 40.0
MAX_ROLL_WIDTH_MM = 1200.0
MAX_FEED_LENGTH_MM = 20000.0


class WorkspaceError(ValueError):
    """Geometry or transformation parameters are unsuitable for cutting."""


@dataclass(frozen=True)
class Transform:
    target_width_mm: float
    target_height_mm: float
    roll_width_mm: float = MAX_ROLL_WIDTH_MM
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    rotation: int = 0
    mirror_x: bool = False
    mirror_y: bool = False


@dataclass(frozen=True)
class Preparation:
    enabled: bool = True
    remove_duplicates: bool = True
    inside_first: bool = True
    minimize_travel: bool = True
    merge_enabled: bool = False
    merge_tolerance_mm: float = 0.05
    simplify_enabled: bool = False
    simplify_tolerance_mm: float = 0.05


@dataclass(frozen=True)
class Layout:
    automatic: bool = True
    edge_margin_mm: float = 5.0
    spacing_mm: float = 5.0
    allow_rotation: bool = False


@dataclass(frozen=True)
class CuttingAids:
    weed_enabled: bool = False
    weed_border_mode: str = "layout"
    weed_margin_mm: float = 5.0
    weed_horizontal: bool = False
    weed_vertical: bool = False
    overcut_enabled: bool = False
    overcut_mm: float = 1.0
    blade_compensation_enabled: bool = False
    blade_offset_mm: float = 0.25


def _bounds(paths: Sequence[Sequence[Point]]) -> Tuple[float, float, float, float]:
    points = [point for path in paths for point in path]
    if not points:
        raise WorkspaceError("No cut paths were found.")
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _normalise(paths: Sequence[Sequence[Point]]) -> Tuple[List[Path], float, float]:
    min_x, min_y, max_x, max_y = _bounds(paths)
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        raise WorkspaceError("The cut geometry has no measurable width or height.")
    return (
        [[(x - min_x, y - min_y) for x, y in path] for path in paths],
        width,
        height,
    )


def _travel_paths(paths: Sequence[Sequence[Point]]) -> List[Path]:
    travels: List[Path] = []
    current = (0.0, 0.0)
    for path in paths:
        if not path:
            continue
        if path[0] != current:
            travels.append([current, path[0]])
        current = path[-1]
    return travels


def _json_paths(paths: Sequence[Sequence[Point]]) -> List[List[List[float]]]:
    return [
        [[round(x, 4), round(y, 4)] for x, y in path]
        for path in paths
    ]


@lru_cache(maxsize=24)
def _load_svg_paths_cached(filename: str, mtime_ns: int, size: int):
    """Flatten SVG once per file revision and cache immutable path tuples."""
    try:
        import vpype

        document = vpype.read_multilayer_svg(
            os.path.abspath(filename), quantization=vpype.convert_length("0.1mm")
        )
    except Exception as exc:
        raise WorkspaceError("Could not read the SVG geometry: " + str(exc)) from exc

    paths: List[Path] = []
    for collection in document.layers.values():
        for line in collection:
            path = [
                (float(point.real) * MM_PER_SVG_UNIT, float(point.imag) * MM_PER_SVG_UNIT)
                for point in line
            ]
            if len(path) > 1:
                paths.append(path)
    if not paths:
        raise WorkspaceError("No vector cut lines were found in this SVG file.")
    normalised, _width, _height = _normalise(paths)
    return tuple(tuple(point for point in path) for path in normalised)


def load_svg_paths(filename: str) -> List[Path]:
    """Return a defensive copy of cached, normalized SVG paths in millimetres."""
    absolute = os.path.abspath(filename)
    try:
        stat = os.stat(absolute)
    except OSError as exc:
        raise WorkspaceError("The SVG file could not be read.") from exc
    cached = _load_svg_paths_cached(absolute, stat.st_mtime_ns, stat.st_size)
    return [list(path) for path in cached]


def load_hpgl_paths(filename: str) -> Tuple[List[Path], List[str]]:
    """Return HPGL paths in millimetres without altering their coordinates."""
    try:
        with open(filename, "rb") as source:
            paths, warnings = parse_hpgl(source.read())
    except OSError as exc:
        raise WorkspaceError("The HPGL file could not be read.") from exc
    except PreviewError as exc:
        raise WorkspaceError(str(exc)) from exc
    if not paths:
        raise WorkspaceError("No pen-down cut lines were found in this HPGL file.")
    return (
        [[(x / HPGL_UNITS_PER_MM, y / HPGL_UNITS_PER_MM) for x, y in path] for path in paths],
        warnings,
    )


def workspace_payload(filename: str) -> dict:
    """Build the browser workspace payload for an SVG or HPGL file."""
    extension = os.path.splitext(filename)[1].lower()
    warnings: List[str] = []
    if extension == ".svg":
        paths = load_svg_paths(filename)
        source_type = "svg"
        read_only = False
    elif extension == ".hpgl":
        paths, warnings = load_hpgl_paths(filename)
        source_type = "hpgl"
        read_only = True
    else:
        raise WorkspaceError("Only SVG and HPGL files can be previewed.")

    min_x, min_y, max_x, max_y = _bounds(paths)
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        raise WorkspaceError("The cut geometry has no measurable width or height.")
    return {
        "source_type": source_type,
        "read_only": read_only,
        "width_mm": round(width, 4),
        "height_mm": round(height, 4),
        "bounds": {
            "min_x": round(min_x, 4), "min_y": round(min_y, 4),
            "max_x": round(max_x, 4), "max_y": round(max_y, 4),
        },
        "cut_paths": _json_paths(paths),
        "travel_paths": _json_paths(_travel_paths(paths)),
        "path_count": len(paths),
        "point_count": sum(len(path) for path in paths),
        "warnings": warnings,
        "units": "mm",
    }


def _finite_number(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceError(f"{label} must be a number in millimetres.") from exc
    if not math.isfinite(number):
        raise WorkspaceError(f"{label} must be a finite number.")
    return number


def parse_transform(values) -> Transform:
    """Validate form-like transform values and return a strongly typed transform."""
    width = _finite_number(values.get("target_width_mm"), "Width")
    height = _finite_number(values.get("target_height_mm"), "Height")
    roll_width = _finite_number(values.get("roll_width_mm", MAX_ROLL_WIDTH_MM), "Roll width")
    offset_x = _finite_number(values.get("offset_x_mm", 0), "X offset")
    offset_y = _finite_number(values.get("offset_y_mm", 0), "Y offset")
    try:
        rotation = int(values.get("rotation", 0))
    except (TypeError, ValueError) as exc:
        raise WorkspaceError("Rotation must be 0, 90, 180, or 270 degrees.") from exc
    if rotation not in (0, 90, 180, 270):
        raise WorkspaceError("Rotation must be 0, 90, 180, or 270 degrees.")
    if width <= 0 or height <= 0:
        raise WorkspaceError("Width and height must be greater than zero.")
    if roll_width <= 0 or roll_width > MAX_ROLL_WIDTH_MM:
        raise WorkspaceError("Roll width must be between 0.1 and 1200 mm.")
    if offset_x < 0 or offset_y < 0:
        raise WorkspaceError("X and Y offsets cannot be negative.")
    if offset_y > MAX_FEED_LENGTH_MM:
        raise WorkspaceError("Y offset exceeds the 20000 mm safety limit.")

    def truthy(name: str) -> bool:
        return str(values.get(name, "")).lower() in {"1", "true", "yes", "on"}

    return Transform(
        target_width_mm=width,
        target_height_mm=height,
        roll_width_mm=roll_width,
        offset_x_mm=offset_x,
        offset_y_mm=offset_y,
        rotation=rotation,
        mirror_x=truthy("mirror_x"),
        mirror_y=truthy("mirror_y"),
    )


def _truthy(value) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


def parse_preparation(values=None) -> Preparation:
    """Validate cut-preparation controls with conservative defaults."""
    values = values or {}
    merge_tolerance = _finite_number(values.get("merge_tolerance_mm", 0.05), "Merge tolerance")
    simplify_tolerance = _finite_number(
        values.get("simplify_tolerance_mm", 0.05), "Simplification tolerance"
    )
    if not 0.001 <= merge_tolerance <= 1.0:
        raise WorkspaceError("Merge tolerance must be between 0.001 and 1 mm.")
    if not 0.001 <= simplify_tolerance <= 1.0:
        raise WorkspaceError("Simplification tolerance must be between 0.001 and 1 mm.")
    return Preparation(
        enabled=not str(values.get("enabled", "true")).lower() in {"0", "false", "no", "off"},
        remove_duplicates=not str(values.get("remove_duplicates", "true")).lower()
        in {"0", "false", "no", "off"},
        inside_first=not str(values.get("inside_first", "true")).lower()
        in {"0", "false", "no", "off"},
        minimize_travel=not str(values.get("minimize_travel", "true")).lower()
        in {"0", "false", "no", "off"},
        merge_enabled=_truthy(values.get("merge_enabled", False)),
        merge_tolerance_mm=merge_tolerance,
        simplify_enabled=_truthy(values.get("simplify_enabled", False)),
        simplify_tolerance_mm=simplify_tolerance,
    )


def parse_layout(values=None) -> Layout:
    values = values or {}
    margin = _finite_number(values.get("edge_margin_mm", 5), "Edge margin")
    spacing = _finite_number(values.get("spacing_mm", 5), "Copy spacing")
    if not 0 <= margin <= 100:
        raise WorkspaceError("Edge margin must be between 0 and 100 mm.")
    if not 0 <= spacing <= 100:
        raise WorkspaceError("Copy spacing must be between 0 and 100 mm.")
    return Layout(
        automatic=not str(values.get("automatic", "true")).lower()
        in {"0", "false", "no", "off"},
        edge_margin_mm=margin,
        spacing_mm=spacing,
        allow_rotation=_truthy(values.get("allow_rotation", False)),
    )


def parse_cutting_aids(values=None) -> CuttingAids:
    """Validate disabled-by-default vinyl-cutting aids."""
    values = values or {}
    border_mode = str(values.get("weed_border_mode", "layout")).lower()
    if border_mode not in {"copy", "layout"}:
        raise WorkspaceError("Weed border mode must be copy or layout.")
    weed_margin = _finite_number(values.get("weed_margin_mm", 5), "Weed margin")
    overcut = _finite_number(values.get("overcut_mm", 1), "Overcut")
    blade_offset = _finite_number(values.get("blade_offset_mm", 0.25), "Blade offset")
    if not 0 <= weed_margin <= 100:
        raise WorkspaceError("Weed margin must be between 0 and 100 mm.")
    if not 0.01 <= overcut <= 10:
        raise WorkspaceError("Overcut must be between 0.01 and 10 mm.")
    if not 0.01 <= blade_offset <= 5:
        raise WorkspaceError("Blade offset must be between 0.01 and 5 mm.")
    return CuttingAids(
        weed_enabled=_truthy(values.get("weed_enabled", False)),
        weed_border_mode=border_mode,
        weed_margin_mm=weed_margin,
        weed_horizontal=_truthy(values.get("weed_horizontal", False)),
        weed_vertical=_truthy(values.get("weed_vertical", False)),
        overcut_enabled=_truthy(values.get("overcut_enabled", False)),
        overcut_mm=overcut,
        blade_compensation_enabled=_truthy(
            values.get("blade_compensation_enabled", False)
        ),
        blade_offset_mm=blade_offset,
    )


def transform_paths(paths: Sequence[Sequence[Point]], transform: Transform) -> Tuple[List[Path], dict]:
    """Apply a proportional browser-compatible transform to normalized paths."""
    normalised, source_width, source_height = _normalise(paths)
    scale = min(
        transform.target_width_mm / source_width,
        transform.target_height_mm / source_height,
    )
    scaled_width = source_width * scale
    scaled_height = source_height * scale

    def apply(point: Point) -> Point:
        x, y = point[0] * scale, point[1] * scale
        if transform.mirror_x:
            x = scaled_width - x
        if transform.mirror_y:
            y = scaled_height - y
        if transform.rotation == 90:
            x, y = scaled_height - y, x
        elif transform.rotation == 180:
            x, y = scaled_width - x, scaled_height - y
        elif transform.rotation == 270:
            x, y = y, scaled_width - x
        return x + transform.offset_x_mm, y + transform.offset_y_mm

    output = [[apply(point) for point in path] for path in normalised]
    min_x, min_y, max_x, max_y = _bounds(output)
    out_of_bounds = min_x < -1e-6 or min_y < -1e-6 or max_x > transform.roll_width_mm + 1e-6
    if max_y > MAX_FEED_LENGTH_MM + 1e-6:
        out_of_bounds = True
    metadata = {
        "width_mm": max_x - min_x,
        "height_mm": max_y - min_y,
        "min_x_mm": min_x,
        "min_y_mm": min_y,
        "max_x_mm": max_x,
        "max_y_mm": max_y,
        "roll_length_mm": max(max_y + 20.0, 20.0),
        "out_of_bounds": out_of_bounds,
    }
    return output, metadata


def hpgl_bytes(paths: Sequence[Sequence[Point]]) -> bytes:
    """Serialize ordered millimetre paths as payload-only absolute HPGL."""
    commands = ["IN", "SP1", "PA"]
    for path in paths:
        if len(path) < 2:
            continue
        coordinates = [
            (int(round(x * HPGL_UNITS_PER_MM)), int(round(y * HPGL_UNITS_PER_MM)))
            for x, y in path
        ]
        commands.append(f"PU{coordinates[0][0]},{coordinates[0][1]}")
        commands.append("PD" + ",".join(f"{x},{y}" for x, y in coordinates[1:]))
    commands.extend(["PU", "SP0"])
    return (";".join(commands) + ";").encode("ascii")


def _path_length(path: Sequence[Point]) -> float:
    return sum(
        math.hypot(path[index][0] - path[index - 1][0], path[index][1] - path[index - 1][1])
        for index in range(1, len(path))
    )


def _path_stats(paths: Sequence[Sequence[Point]]) -> dict:
    current = (0.0, 0.0)
    travel = 0.0
    for path in paths:
        if not path:
            continue
        travel += math.hypot(path[0][0] - current[0], path[0][1] - current[1])
        current = path[-1]
    return {
        "path_count": len(paths),
        "point_count": sum(len(path) for path in paths),
        "cut_length_mm": round(sum(_path_length(path) for path in paths), 3),
        "travel_length_mm": round(travel, 3),
        "hpgl_bytes": len(hpgl_bytes(paths)),
    }


def _quantized_paths(paths: Sequence[Sequence[Point]], collapse: bool = True) -> List[Path]:
    output: List[Path] = []
    for path in paths:
        converted: Path = []
        for x, y in path:
            if not math.isfinite(x) or not math.isfinite(y):
                raise WorkspaceError("Cut geometry contains a non-finite coordinate.")
            point = (
                round(x * HPGL_UNITS_PER_MM) / HPGL_UNITS_PER_MM,
                round(y * HPGL_UNITS_PER_MM) / HPGL_UNITS_PER_MM,
            )
            if not collapse or not converted or point != converted[-1]:
                converted.append(point)
        if len(converted) > 1:
            output.append(converted)
    return output


def _minimum_rotation(points):
    if not points:
        return ()
    smallest = min(points)
    candidates = []
    for index, point in enumerate(points):
        if point == smallest:
            candidates.append(tuple(points[index:] + points[:index]))
    return min(candidates)


def _canonical_path(path: Sequence[Point]):
    units = [(round(x * HPGL_UNITS_PER_MM), round(y * HPGL_UNITS_PER_MM)) for x, y in path]
    if len(units) > 3 and units[0] == units[-1]:
        ring = units[:-1]
        forward = _minimum_rotation(ring)
        reverse = _minimum_rotation(list(reversed(ring)))
        return ("closed", min(forward, reverse))
    forward = tuple(units)
    return ("open", min(forward, tuple(reversed(units))))


def _optional_filters(paths: Sequence[Sequence[Point]], preparation: Preparation) -> List[Path]:
    output = [list(path) for path in paths]
    if preparation.simplify_enabled:
        try:
            from shapely.geometry import LineString

            simplified = []
            for path in output:
                closed = len(path) > 3 and path[0] == path[-1]
                line = LineString(path).simplify(
                    preparation.simplify_tolerance_mm, preserve_topology=True
                )
                coordinates = [(float(x), float(y)) for x, y in line.coords]
                if closed and coordinates and coordinates[0] != coordinates[-1]:
                    coordinates.append(coordinates[0])
                if len(coordinates) > 1:
                    simplified.append(coordinates)
            output = simplified
        except Exception as exc:
            raise WorkspaceError("Could not simplify the cut paths: " + str(exc)) from exc
    if preparation.merge_enabled:
        try:
            import vpype

            collection = vpype.LineCollection(
                [[complex(x, y) for x, y in path] for path in output]
            )
            collection.merge(preparation.merge_tolerance_mm, flip=False)
            output = [
                [(float(point.real), float(point.imag)) for point in line]
                for line in collection
                if len(line) > 1
            ]
        except Exception as exc:
            raise WorkspaceError("Could not merge the cut paths: " + str(exc)) from exc
    return output


def _nesting_depths(paths: Sequence[Sequence[Point]]):
    """Return containment depths for valid closed contours, or None for open paths."""
    try:
        from shapely.geometry import Polygon
    except ImportError as exc:
        raise WorkspaceError("Shapely is required for inside-first cut ordering.") from exc

    polygons = {}
    for index, path in enumerate(paths):
        if len(path) > 3 and path[0] == path[-1]:
            polygon = Polygon(path)
            if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
                polygons[index] = polygon
    depths = [None] * len(paths)
    for index, polygon in polygons.items():
        point = polygon.representative_point()
        depths[index] = sum(
            1
            for other_index, other in polygons.items()
            if other_index != index and other.area > polygon.area and other.contains(point)
        )
    return depths


def _nearest_order(indices, paths, current):
    remaining = list(indices)
    output = []
    while remaining:
        next_index = min(
            remaining,
            key=lambda index: (
                math.hypot(paths[index][0][0] - current[0], paths[index][0][1] - current[1]),
                index,
            ),
        )
        remaining.remove(next_index)
        output.append(next_index)
        current = paths[next_index][-1]
    return output, current


def _ordered_paths(paths: Sequence[Sequence[Point]], preparation: Preparation) -> List[Path]:
    if not preparation.inside_first and not preparation.minimize_travel:
        return [list(path) for path in paths]
    depths = _nesting_depths(paths) if preparation.inside_first else [0] * len(paths)
    groups = []
    open_indices = [index for index, depth in enumerate(depths) if depth is None]
    if open_indices:
        groups.append(open_indices)
    closed_depths = sorted({depth for depth in depths if depth is not None}, reverse=True)
    for depth in closed_depths:
        groups.append([index for index, item_depth in enumerate(depths) if item_depth == depth])

    order = []
    current = (0.0, 0.0)
    for group in groups:
        if preparation.minimize_travel:
            ordered, current = _nearest_order(group, paths, current)
        else:
            ordered = group
            if ordered:
                current = paths[ordered[-1]][-1]
        order.extend(ordered)
    return [list(paths[index]) for index in order]


def prepare_paths(paths: Sequence[Sequence[Point]], preparation: Preparation) -> Tuple[List[Path], dict]:
    """Prepare transformed geometry and return exact quantized tool paths plus diagnostics."""
    source_point_count = sum(len(path) for path in paths)
    if source_point_count > 1_000_000:
        raise WorkspaceError("Cut geometry exceeds the 1,000,000 point safety limit.")

    before = _quantized_paths(paths, collapse=False)
    warnings = []
    if not preparation.enabled:
        final = before
    else:
        filtered = _optional_filters(paths, preparation)
        cleaned = _quantized_paths(filtered, collapse=True)
        duplicate_count = 0
        if preparation.remove_duplicates:
            unique = []
            seen = set()
            for path in cleaned:
                key = _canonical_path(path)
                if key in seen:
                    duplicate_count += 1
                    continue
                seen.add(key)
                unique.append(path)
            cleaned = unique
        if duplicate_count:
            warnings.append({
                "code": "duplicates_removed",
                "severity": "warning",
                "message": f"Removed {duplicate_count} duplicate cut path"
                + ("" if duplicate_count == 1 else "s") + ".",
                "count": duplicate_count,
            })
        final = _ordered_paths(cleaned, preparation)

    open_count = sum(1 for path in final if not (len(path) > 3 and path[0] == path[-1]))
    if open_count:
        warnings.append({
            "code": "open_contours",
            "severity": "warning",
            "message": f"{open_count} open contour" + ("" if open_count == 1 else "s")
            + " will be cut as open lines.",
            "count": open_count,
        })
    tiny_count = 0
    for path in final:
        if len(path) > 3 and path[0] == path[-1]:
            min_x, min_y, max_x, max_y = _bounds([path])
            if max_x - min_x < 1.0 or max_y - min_y < 1.0:
                tiny_count += 1
    if tiny_count:
        warnings.append({
            "code": "tiny_contours",
            "severity": "warning",
            "message": f"{tiny_count} closed contour" + ("" if tiny_count == 1 else "s")
            + " measure less than 1 mm in one dimension.",
            "count": tiny_count,
        })
    final_points = sum(len(path) for path in final)
    if final_points > 100_000:
        warnings.append({
            "code": "high_point_count",
            "severity": "warning",
            "message": f"The prepared cut contains {final_points:,} points.",
            "count": final_points,
        })

    payload = hpgl_bytes(final)
    return final, {
        "before": _path_stats(before),
        "after": _path_stats(final),
        "warnings": warnings,
        "geometry_hash": hashlib.sha256(payload).hexdigest(),
        "preparation": {
            "enabled": preparation.enabled,
            "remove_duplicates": preparation.remove_duplicates,
            "inside_first": preparation.inside_first,
            "minimize_travel": preparation.minimize_travel,
            "merge_enabled": preparation.merge_enabled,
            "merge_tolerance_mm": preparation.merge_tolerance_mm,
            "simplify_enabled": preparation.simplify_enabled,
            "simplify_tolerance_mm": preparation.simplify_tolerance_mm,
        },
    }


def build_svg_preview(filename: str, transform: Transform, preparation: Preparation) -> Tuple[List[Path], dict]:
    """Build the exact prepared SVG paths used by both preview and generation."""
    source = load_svg_paths(filename)
    intended, metadata = transform_paths(source, transform)
    prepared, diagnostics = prepare_paths(intended, preparation)
    min_x, min_y, max_x, max_y = _bounds(prepared)
    metadata.update({
        "width_mm": max_x - min_x,
        "height_mm": max_y - min_y,
        "min_x_mm": min_x,
        "min_y_mm": min_y,
        "max_x_mm": max_x,
        "max_y_mm": max_y,
        "roll_length_mm": max(max_y + 20.0, 20.0),
        "out_of_bounds": (
            min_x < -1e-6 or min_y < -1e-6
            or max_x > transform.roll_width_mm + 1e-6
            or max_y > MAX_FEED_LENGTH_MM + 1e-6
        ),
        "intended_paths": _json_paths(intended),
        "cut_paths": _json_paths(prepared),
        "travel_paths": _json_paths(_travel_paths(prepared)),
        **diagnostics,
    })
    return prepared, metadata


def _rotate_copy(paths: Sequence[Sequence[Point]], rotation: int) -> Tuple[List[Path], float, float]:
    normalised, width, height = _normalise(paths)
    if rotation == 90:
        return [[(height - y, x) for x, y in path] for path in normalised], height, width
    return normalised, width, height


def _translated(paths: Sequence[Sequence[Point]], x: float, y: float) -> List[Path]:
    return [[(px + x, py + y) for px, py in path] for path in paths]


def _rectangles_overlap(left, right) -> bool:
    epsilon = 1e-6
    return (
        left["x"] < right["x"] + right["width"] - epsilon
        and right["x"] < left["x"] + left["width"] - epsilon
        and left["y"] < right["y"] + right["height"] - epsilon
        and right["y"] < left["y"] + left["height"] - epsilon
    )


def _rectangle_path(min_x: float, min_y: float, max_x: float, max_y: float) -> Path:
    return [
        (min_x, min_y), (max_x, min_y), (max_x, max_y),
        (min_x, max_y), (min_x, min_y),
    ]


def _merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _free_segments(start: float, end: float, blocked) -> List[Tuple[float, float]]:
    segments = []
    cursor = start
    for block_start, block_end in _merge_intervals(blocked):
        block_start = max(start, block_start)
        block_end = min(end, block_end)
        if block_start > cursor + 1e-6:
            segments.append((cursor, block_start))
        cursor = max(cursor, block_end)
    if end > cursor + 1e-6:
        segments.append((cursor, end))
    return segments


def _gap_centres(instances, axis: str) -> List[float]:
    leading = "x" if axis == "x" else "y"
    size = "width" if axis == "x" else "height"
    intervals = sorted((item[leading], item[leading] + item[size]) for item in instances)
    centres = []
    for index, (_start, end) in enumerate(intervals[:-1]):
        next_start = intervals[index + 1][0]
        if next_start > end + 1e-6:
            centre = (end + next_start) / 2.0
            if all(abs(centre - value) > 1e-6 for value in centres):
                centres.append(centre)
    return centres


def _weed_geometry(instances, aids: CuttingAids):
    if not aids.weed_enabled or not instances:
        return [], [], []
    margin = aids.weed_margin_mm
    layout_min_x = min(item["x"] for item in instances) - margin
    layout_min_y = min(item["y"] for item in instances) - margin
    layout_max_x = max(item["x"] + item["width"] for item in instances) + margin
    layout_max_y = max(item["y"] + item["height"] for item in instances) + margin
    protected = [
        {
            "x": item["x"], "y": item["y"],
            "width": item["width"], "height": item["height"],
        }
        for item in instances
    ]
    lines: List[Path] = []
    if aids.weed_vertical:
        for x in _gap_centres(instances, "x"):
            blocked = [
                (rect["y"], rect["y"] + rect["height"])
                for rect in protected
                if rect["x"] - 1e-6 <= x <= rect["x"] + rect["width"] + 1e-6
            ]
            lines.extend(
                [[(x, start), (x, end)] for start, end in
                 _free_segments(layout_min_y, layout_max_y, blocked)]
            )
    if aids.weed_horizontal:
        for y in _gap_centres(instances, "y"):
            blocked = [
                (rect["x"], rect["x"] + rect["width"])
                for rect in protected
                if rect["y"] - 1e-6 <= y <= rect["y"] + rect["height"] + 1e-6
            ]
            lines.extend(
                [[(start, y), (end, y)] for start, end in
                 _free_segments(layout_min_x, layout_max_x, blocked)]
            )

    warnings = []
    if aids.weed_border_mode == "copy":
        borders = [
            _rectangle_path(
                item["x"] - margin,
                item["y"] - margin,
                item["x"] + item["width"] + margin,
                item["y"] + item["height"] + margin,
            )
            for item in instances
        ]
        expanded = [
            {
                "x": item["x"] - margin,
                "y": item["y"] - margin,
                "width": item["width"] + 2 * margin,
                "height": item["height"] + 2 * margin,
            }
            for item in instances
        ]
        conflicts = set()
        for index, border in enumerate(expanded):
            for other_index, other in enumerate(protected):
                if index != other_index and _rectangles_overlap(border, other):
                    conflicts.add(index)
                    conflicts.add(other_index)
        if conflicts:
            warnings.append({
                "code": "weed_border_collision",
                "severity": "error",
                "message": (
                    f"{len(conflicts)} copy weed borders enter another design. "
                    "Increase copy spacing or reduce the weed margin."
                ),
                "count": len(conflicts),
            })
    else:
        borders = [_rectangle_path(
            layout_min_x, layout_min_y, layout_max_x, layout_max_y
        )]
    return lines, borders, warnings


def _is_closed(path: Sequence[Point]) -> bool:
    return (
        len(path) > 2
        and math.hypot(path[0][0] - path[-1][0], path[0][1] - path[-1][1])
        <= 1e-6
    )


def _overcut_path(path: Sequence[Point], distance: float) -> Path:
    if not _is_closed(path) or distance <= 0:
        return list(path)
    output = list(path)
    remaining = distance
    segments = list(zip(path[:-1], path[1:]))
    if not segments:
        return output
    cycles = 0
    while remaining > 1e-9 and cycles < 100:
        progressed = False
        for start, end in segments:
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            if length <= 1e-9:
                continue
            progressed = True
            if remaining >= length:
                output.append(end)
                remaining -= length
            else:
                ratio = remaining / length
                output.append((
                    start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio,
                ))
                remaining = 0
                break
        if not progressed:
            break
        cycles += 1
    return output


def _unit_vector(start: Point, end: Point) -> Point:
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length <= 1e-9:
        raise WorkspaceError("Blade compensation encountered a zero-length segment.")
    return (end[0] - start[0]) / length, (end[1] - start[1]) / length


def _pivot_arc(vertex: Point, incoming: Point, outgoing: Point, offset: float) -> Path:
    start_angle = math.atan2(incoming[1], incoming[0])
    end_angle = math.atan2(outgoing[1], outgoing[0])
    delta = (end_angle - start_angle + math.pi) % (2 * math.pi) - math.pi
    steps = max(1, int(math.ceil(abs(delta) / math.radians(12))))
    return [
        (
            vertex[0] + offset * math.cos(start_angle + delta * step / steps),
            vertex[1] + offset * math.sin(start_angle + delta * step / steps),
        )
        for step in range(1, steps + 1)
    ]


def _blade_compensated_path(path: Sequence[Point], offset: float) -> Path:
    points = []
    for point in path:
        if not points or math.hypot(
            point[0] - points[-1][0], point[1] - points[-1][1]
        ) > 1e-9:
            points.append(point)
    if len(points) < 2:
        raise WorkspaceError("Blade compensation needs at least two distinct points.")
    closed = _is_closed(points)
    unique = points[:-1] if closed else points
    if closed and len(unique) < 3:
        raise WorkspaceError("Blade compensation needs at least three contour points.")
    if closed:
        directions = [
            _unit_vector(unique[index], unique[(index + 1) % len(unique)])
            for index in range(len(unique))
        ]
        first = directions[0]
        output = [(unique[0][0] + offset * first[0], unique[0][1] + offset * first[1])]
        for index in range(1, len(unique) + 1):
            vertex = unique[index % len(unique)]
            incoming = directions[(index - 1) % len(unique)]
            outgoing = directions[index % len(unique)]
            output.append((
                vertex[0] + offset * incoming[0],
                vertex[1] + offset * incoming[1],
            ))
            output.extend(_pivot_arc(vertex, incoming, outgoing, offset))
        return output

    directions = [
        _unit_vector(points[index], points[index + 1])
        for index in range(len(points) - 1)
    ]
    output = [(
        points[0][0] + offset * directions[0][0],
        points[0][1] + offset * directions[0][1],
    )]
    for index in range(1, len(points) - 1):
        incoming, outgoing = directions[index - 1], directions[index]
        output.append((
            points[index][0] + offset * incoming[0],
            points[index][1] + offset * incoming[1],
        ))
        output.extend(_pivot_arc(points[index], incoming, outgoing, offset))
    last = directions[-1]
    output.append((
        points[-1][0] + offset * last[0],
        points[-1][1] + offset * last[1],
    ))
    return output


def _compensation_is_valid(
    paths: Sequence[Sequence[Point]], allow_trailing_overcut: bool = False
) -> bool:
    try:
        from shapely.geometry import LineString
        checked = []
        for path in paths:
            candidate = list(path)
            if allow_trailing_overcut and len(candidate) > 3:
                for index in range(2, len(candidate)):
                    if math.hypot(
                        candidate[index][0] - candidate[0][0],
                        candidate[index][1] - candidate[0][1],
                    ) <= 1e-6:
                        candidate = candidate[:index + 1]
                        break
            checked.append(LineString(candidate))
        return all(line.is_valid and line.is_simple for line in checked)
    except Exception as exc:
        raise WorkspaceError(
            "Could not validate the compensated carriage path: " + str(exc)
        ) from exc


def build_manifest_preview(
    items,
    roll_width_mm: float,
    layout: Layout,
    preparation: Preparation,
    cutting_aids: CuttingAids | None = None,
) -> Tuple[List[Path], dict]:
    """Compose SVG copies on roll media and prepare one canonical cutter path."""
    cutting_aids = cutting_aids or CuttingAids()
    if not items:
        raise WorkspaceError("At least one SVG design is required.")
    if not 0.1 <= roll_width_mm <= MAX_ROLL_WIDTH_MM:
        raise WorkspaceError("Roll width must be between 0.1 and 1200 mm.")

    base_items = []
    total_copies = 0
    for item_index, item in enumerate(items):
        copies_value = _finite_number(item.get("copies", 1), "Copy count")
        if copies_value != int(copies_value):
            raise WorkspaceError("Copy count must be a whole number.")
        copies = int(copies_value)
        if not 1 <= copies <= 500:
            raise WorkspaceError("Each design must have between 1 and 500 copies.")
        total_copies += copies
        if total_copies > 500:
            raise WorkspaceError("A workspace cannot contain more than 500 copies.")
        source = load_svg_paths(item["filepath"])
        transform = item["transform"]
        base_transform = Transform(
            target_width_mm=transform.target_width_mm,
            target_height_mm=transform.target_height_mm,
            roll_width_mm=MAX_ROLL_WIDTH_MM,
            rotation=transform.rotation,
            mirror_x=transform.mirror_x,
            mirror_y=transform.mirror_y,
        )
        transformed, _metadata = transform_paths(source, base_transform)
        transformed, width, height = _rotate_copy(transformed, 0)
        base_items.append({
            "item_index": item_index,
            "filename": item["filename"],
            "copies": copies,
            "paths": transformed,
            "width": width,
            "height": height,
            "placements": item.get("placements") or [],
        })

    instances = []
    intended = []
    x = layout.edge_margin_mm
    y = layout.edge_margin_mm
    row_height = 0.0
    right_edge = roll_width_mm - layout.edge_margin_mm
    for item in base_items:
        for copy_index in range(item["copies"]):
            extra_rotation = 0
            paths = item["paths"]
            width, height = item["width"], item["height"]
            if layout.automatic:
                remaining = right_edge - x
                if (
                    layout.allow_rotation and height <= remaining
                    and (width > remaining or height < width)
                ):
                    paths, width, height = _rotate_copy(item["paths"], 90)
                    extra_rotation = 90
                if x > layout.edge_margin_mm and x + width > right_edge + 1e-6:
                    x = layout.edge_margin_mm
                    y += row_height + layout.spacing_mm
                    row_height = 0.0
                    paths, width, height = _rotate_copy(item["paths"], 0)
                    extra_rotation = 0
                    if layout.allow_rotation and height < width and x + height <= right_edge + 1e-6:
                        paths, width, height = _rotate_copy(item["paths"], 90)
                        extra_rotation = 90
                place_x, place_y = x, y
                x += width + layout.spacing_mm
                row_height = max(row_height, height)
            else:
                if copy_index >= len(item["placements"]):
                    raise WorkspaceError(
                        f"Manual layout is missing placement for {item['filename']} copy {copy_index + 1}."
                    )
                placement = item["placements"][copy_index]
                if not isinstance(placement, dict):
                    raise WorkspaceError("Every manual copy placement must be an object.")
                place_x = _finite_number(placement.get("x_mm"), "Copy X position")
                place_y = _finite_number(placement.get("y_mm"), "Copy Y position")
                if place_x < 0 or place_y < 0:
                    raise WorkspaceError("Copy positions cannot be negative.")
                extra_rotation = int(placement.get("rotation", 0))
                if extra_rotation not in (0, 90):
                    raise WorkspaceError("Copy layout rotation must be 0 or 90 degrees.")
                paths, width, height = _rotate_copy(item["paths"], extra_rotation)

            instance_id = f"item-{item['item_index']}-copy-{copy_index}"
            placed = _translated(paths, place_x, place_y)
            intended.extend(placed)
            instances.append({
                "instance_id": instance_id,
                "item_index": item["item_index"],
                "copy_index": copy_index,
                "filename": item["filename"],
                "x": round(place_x, 4),
                "y": round(place_y, 4),
                "width": round(width, 4),
                "height": round(height, 4),
                "rotation": extra_rotation,
            })

    collisions = set()
    for index, instance in enumerate(instances):
        for other in instances[index + 1:]:
            if _rectangles_overlap(instance, other):
                collisions.add(instance["instance_id"])
                collisions.add(other["instance_id"])

    artwork, diagnostics = prepare_paths(intended, preparation)
    weed_lines, weed_borders, aid_warnings = _weed_geometry(instances, cutting_aids)
    contour_paths = artwork + weed_lines + weed_borders
    path_roles = (
        ["artwork"] * len(artwork)
        + ["weed_line"] * len(weed_lines)
        + ["weed_border"] * len(weed_borders)
    )
    if cutting_aids.overcut_enabled:
        contour_paths = [
            _overcut_path(path, cutting_aids.overcut_mm) for path in contour_paths
        ]
    compensated_paths: List[Path] = []
    compensation_valid = True
    if cutting_aids.blade_compensation_enabled:
        compensated_paths = [
            _blade_compensated_path(path, cutting_aids.blade_offset_mm)
            for path in contour_paths
        ]
        compensation_valid = _compensation_is_valid(
            compensated_paths,
            allow_trailing_overcut=cutting_aids.overcut_enabled,
        )
        emitted_paths = compensated_paths
        if not compensation_valid:
            aid_warnings.append({
                "code": "invalid_blade_compensation",
                "severity": "error",
                "message": (
                    "Blade compensation produced a self-intersecting carriage path. "
                    "Reduce the blade offset or disable compensation."
                ),
            })
    else:
        emitted_paths = contour_paths
    point_count = sum(len(path) for path in emitted_paths)
    if point_count > 1_000_000:
        raise WorkspaceError("Prepared cutting aids exceed the 1,000,000 point safety limit.")
    min_x, min_y, max_x, max_y = _bounds(emitted_paths)
    out_of_bounds = (
        min_x < -1e-6 or min_y < -1e-6
        or max_x > roll_width_mm + 1e-6
        or max_y > MAX_FEED_LENGTH_MM + 1e-6
    )
    warnings = list(diagnostics["warnings"]) + aid_warnings
    if point_count > 100_000 and not any(
        warning.get("code") == "high_point_count" for warning in warnings
    ):
        warnings.append({
            "code": "high_point_count",
            "severity": "warning",
            "message": f"The emitted cutter path contains {point_count:,} points.",
            "count": point_count,
        })
    if collisions:
        warnings.append({
            "code": "layout_collisions",
            "severity": "error",
            "message": f"{len(collisions)} arranged copies overlap.",
            "count": len(collisions),
        })
    if out_of_bounds:
        warnings.append({
            "code": "layout_out_of_bounds",
            "severity": "error",
            "message": "The arranged cut path is outside the loaded roll.",
        })
    aid_error = any(warning.get("severity") == "error" for warning in aid_warnings)
    roll_length = max(max_y + 20.0, 20.0)
    emitted_hpgl = hpgl_bytes(emitted_paths)
    diagnostics.update({
        "manifest_version": 1,
        "source_type": "svg",
        "read_only": False,
        "valid": not out_of_bounds and not collisions and not aid_error,
        "out_of_bounds": out_of_bounds,
        "width_mm": max_x - min_x,
        "height_mm": max_y - min_y,
        "min_x_mm": min_x,
        "min_y_mm": min_y,
        "max_x_mm": max_x,
        "max_y_mm": max_y,
        "roll_width_mm": roll_width_mm,
        "roll_length_mm": roll_length,
        "media_area_mm2": round(roll_width_mm * roll_length, 2),
        "design_area_mm2": round(sum(i["width"] * i["height"] for i in instances), 2),
        "original_paths": _json_paths(intended),
        "intended_paths": _json_paths(contour_paths),
        "compensated_paths": _json_paths(compensated_paths),
        "weed_paths": _json_paths(weed_lines),
        "weed_border_paths": _json_paths(weed_borders),
        "cut_paths": _json_paths(emitted_paths),
        "travel_paths": _json_paths(_travel_paths(emitted_paths)),
        "path_roles": path_roles,
        "instances": instances,
        "collisions": sorted(collisions),
        "warnings": warnings,
        "after": _path_stats(emitted_paths),
        "geometry_hash": hashlib.sha256(emitted_hpgl).hexdigest(),
        "cutting_aids": {
            "weed_enabled": cutting_aids.weed_enabled,
            "weed_border_mode": cutting_aids.weed_border_mode,
            "weed_margin_mm": cutting_aids.weed_margin_mm,
            "weed_horizontal": cutting_aids.weed_horizontal,
            "weed_vertical": cutting_aids.weed_vertical,
            "overcut_enabled": cutting_aids.overcut_enabled,
            "overcut_mm": cutting_aids.overcut_mm,
            "blade_compensation_enabled": cutting_aids.blade_compensation_enabled,
            "blade_offset_mm": cutting_aids.blade_offset_mm,
            "compensation_valid": compensation_valid,
        },
        "layout": {
            "automatic": layout.automatic,
            "edge_margin_mm": layout.edge_margin_mm,
            "spacing_mm": layout.spacing_mm,
            "allow_rotation": layout.allow_rotation,
        },
    })
    return emitted_paths, diagnostics


def convert_manifest(
    items,
    roll_width_mm: float,
    layout: Layout,
    preparation: Preparation,
    output_directory: str,
    cutting_aids: CuttingAids | None = None,
    expected_geometry_hash: str | None = None,
) -> Tuple[str, dict]:
    paths, metadata = build_manifest_preview(
        items, roll_width_mm, layout, preparation, cutting_aids
    )
    if not metadata["valid"]:
        raise WorkspaceError("The arranged workspace has collisions or is outside the loaded roll.")
    if expected_geometry_hash and expected_geometry_hash != metadata["geometry_hash"]:
        raise WorkspaceError("The cut geometry changed after preview. Refresh the preview and try again.")
    stem = os.path.splitext(items[0]["filename"])[0] if len(items) == 1 else "pcp-layout"
    output_file = os.path.join(
        output_directory,
        f"{stem}_{metadata['width_mm']:.1f}x{metadata['height_mm']:.1f}mm_"
        f"{metadata['geometry_hash'][:8]}.hpgl",
    )
    _atomic_write_hpgl(output_file, paths)
    return output_file, metadata


def _atomic_write_hpgl(output_file: str, paths: Sequence[Sequence[Point]]) -> None:
    temporary = tempfile.NamedTemporaryFile(
        prefix=".hpgl-conversion-", suffix=".hpgl",
        dir=os.path.dirname(output_file), delete=False
    )
    temporary_file = temporary.name
    try:
        temporary.write(hpgl_bytes(paths))
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary_file, output_file)
    except OSError as exc:
        raise WorkspaceError("Conversion failed: " + str(exc)) from exc
    finally:
        if not temporary.closed:
            temporary.close()
        if os.path.exists(temporary_file):
            os.unlink(temporary_file)


def convert_svg(
    filename: str,
    transform: Transform,
    preparation: Preparation | None = None,
    expected_geometry_hash: str | None = None,
) -> Tuple[str, dict]:
    """Transform an SVG and atomically publish direct 40-unit/mm HPGL."""
    if os.path.splitext(filename)[1].lower() != ".svg":
        raise WorkspaceError("Only SVG files can be converted.")
    if not os.path.isfile(filename):
        raise FileNotFoundError("The selected SVG file does not exist.")
    preparation = preparation or Preparation()
    transformed, metadata = build_svg_preview(filename, transform, preparation)
    if metadata["out_of_bounds"]:
        raise WorkspaceError("The transformed cut path is outside the loaded roll.")
    if expected_geometry_hash and expected_geometry_hash != metadata["geometry_hash"]:
        raise WorkspaceError("The cut geometry changed after preview. Refresh the preview and try again.")

    stem = os.path.splitext(filename)[0]
    size = f"{metadata['width_mm']:.1f}".rstrip("0").rstrip(".")
    size += "x" + f"{metadata['height_mm']:.1f}".rstrip("0").rstrip(".") + "mm"
    suffix = ""
    if transform.rotation:
        suffix += f"_r{transform.rotation}"
    if transform.mirror_x:
        suffix += "_mx"
    if transform.mirror_y:
        suffix += "_my"
    if transform.offset_x_mm or transform.offset_y_mm:
        suffix += f"_at{transform.offset_x_mm:g}x{transform.offset_y_mm:g}"
    output_file = f"{stem}_{size}{suffix}.hpgl"

    _atomic_write_hpgl(output_file, transformed)
    return output_file, metadata
