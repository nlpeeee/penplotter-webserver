"""Canonical millimetre geometry for cutter preview and HPGL output."""

from __future__ import annotations

import math
import os
import tempfile
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


def convert_svg(filename: str, transform: Transform) -> Tuple[str, dict]:
    """Transform an SVG and atomically publish direct 40-unit/mm HPGL."""
    if os.path.splitext(filename)[1].lower() != ".svg":
        raise WorkspaceError("Only SVG files can be converted.")
    if not os.path.isfile(filename):
        raise FileNotFoundError("The selected SVG file does not exist.")
    paths = load_svg_paths(filename)
    transformed, metadata = transform_paths(paths, transform)
    if metadata["out_of_bounds"]:
        raise WorkspaceError("The transformed cut path is outside the loaded roll.")

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
