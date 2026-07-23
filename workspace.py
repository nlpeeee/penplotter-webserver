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

    temporary = tempfile.NamedTemporaryFile(
        prefix=".hpgl-conversion-", suffix=".hpgl", dir=os.path.dirname(output_file), delete=False
    )
    temporary_file = temporary.name
    try:
        temporary.write(hpgl_bytes(transformed))
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
    return output_file, metadata
