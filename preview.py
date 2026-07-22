"""Generate browser-safe cut-line previews for uploaded SVG and HPGL files.

The HPGL renderer intentionally has no vpype dependency.  Previewing must still
work on the plotter host when vpype is unavailable or when its HPGL plug-in is
not installed.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Tuple


Point = Tuple[float, float]
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_COMMAND_RE = re.compile(r"[A-Za-z]{2}")


class PreviewError(ValueError):
    """The uploaded file does not contain previewable cut geometry."""


@dataclass
class PreviewResult:
    svg: bytes
    path_count: int
    width_units: float
    height_units: float
    warnings: List[str] = field(default_factory=list)


def _commands(data: bytes) -> Iterable[Tuple[str, str]]:
    """Yield HPGL commands while tolerating whitespace and device prologues."""
    text = data.decode("latin-1", errors="ignore").replace("\x00", "")
    position = 0
    while position < len(text):
        match = _COMMAND_RE.search(text, position)
        if not match:
            return
        command = match.group(0).upper()
        argument_start = match.end()

        # Labels may contain semicolons and command-looking text.  They end at
        # ETX by default.  Labels do not produce cutter geometry, so skip them.
        if command == "LB":
            end = text.find("\x03", argument_start)
            position = len(text) if end == -1 else end + 1
            continue

        semicolon = text.find(";", argument_start)
        next_command = _COMMAND_RE.search(text, argument_start)
        if semicolon == -1 and not next_command:
            end = len(text)
        elif semicolon == -1:
            end = next_command.start()
        elif next_command and next_command.start() < semicolon:
            end = next_command.start()
        else:
            end = semicolon

        yield command, text[argument_start:end]
        position = end + 1 if end < len(text) and text[end] == ";" else end


def _numbers(argument: str) -> List[float]:
    return [float(value) for value in _NUMBER_RE.findall(argument)]


def _pairs(values: Sequence[float]) -> Iterable[Point]:
    for index in range(0, len(values) - 1, 2):
        yield values[index], values[index + 1]


def _arc_points(start: Point, center: Point, sweep: float, chord: float = 5.0) -> List[Point]:
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    if radius == 0 or sweep == 0:
        return []
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    steps = max(1, int(math.ceil(abs(sweep) / max(0.5, abs(chord)))))
    return [
        (
            center[0] + radius * math.cos(start_angle + math.radians(sweep) * step / steps),
            center[1] + radius * math.sin(start_angle + math.radians(sweep) * step / steps),
        )
        for step in range(1, steps + 1)
    ]


def parse_hpgl(data: bytes) -> Tuple[List[List[Point]], List[str]]:
    """Return pen-down polylines and warnings from common HPGL/2 commands."""
    paths: List[List[Point]] = []
    current: Point = (0.0, 0.0)
    pen_down = False
    absolute = True
    active_path: List[Point] = []
    unsupported = set()

    def finish_path() -> None:
        nonlocal active_path
        if len(active_path) > 1:
            paths.append(active_path)
        active_path = []

    def move_to(point: Point, draw: bool = None) -> None:
        nonlocal current, active_path
        should_draw = pen_down if draw is None else draw
        if should_draw and point != current:
            if not active_path:
                active_path = [current]
            elif active_path[-1] != current:
                finish_path()
                active_path = [current]
            active_path.append(point)
        elif not should_draw:
            finish_path()
        current = point

    def coordinate(point: Point, relative: bool = None) -> Point:
        use_relative = (not absolute) if relative is None else relative
        if use_relative:
            return current[0] + point[0], current[1] + point[1]
        return point

    for command, argument in _commands(data):
        values = _numbers(argument)

        if command == "IN":
            finish_path()
            current, pen_down, absolute = (0.0, 0.0), False, True
        elif command == "DF":
            finish_path()
            pen_down, absolute = False, True
        elif command in {"PU", "PD"}:
            if command == "PU":
                finish_path()
                pen_down = False
            else:
                pen_down = True
            for point in _pairs(values):
                move_to(coordinate(point))
        elif command in {"PA", "PR"}:
            absolute = command == "PA"
            for point in _pairs(values):
                move_to(coordinate(point))
        elif command in {"AA", "AR"} and len(values) >= 3:
            center = coordinate((values[0], values[1]), relative=(command == "AR"))
            chord = values[3] if len(values) >= 4 else 5.0
            for point in _arc_points(current, center, values[2], chord):
                move_to(point)
        elif command == "CI" and values:
            radius = abs(values[0])
            chord = values[1] if len(values) >= 2 else 5.0
            circle_start = (current[0] + radius, current[1])
            finish_path()
            circle = [circle_start]
            circle.extend(_arc_points(circle_start, current, 360.0, chord))
            if len(circle) > 1:
                paths.append(circle)
        elif command in {"EA", "ER"} and len(values) >= 2:
            finish_path()
            opposite = coordinate((values[0], values[1]), relative=(command == "ER"))
            x1, y1 = current
            x2, y2 = opposite
            paths.append([(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)])
        elif command in {"BZ", "BR"}:
            relative = command == "BR"
            for index in range(0, len(values) - 5, 6):
                origin = current
                controls = []
                for offset in (0, 2, 4):
                    point = (values[index + offset], values[index + offset + 1])
                    controls.append((origin[0] + point[0], origin[1] + point[1]) if relative else point)
                c1, c2, end = controls
                for step in range(1, 21):
                    t = step / 20.0
                    u = 1.0 - t
                    point = (
                        u ** 3 * origin[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * end[0],
                        u ** 3 * origin[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * end[1],
                    )
                    move_to(point)
        elif command in {"SP", "VS", "FS", "LT", "PW", "WU", "DT"}:
            continue
        elif command in {"RA", "RR", "WG", "EW", "PM", "FP", "EP", "PE", "RO", "IP", "SC", "IW"}:
            unsupported.add(command)

    finish_path()
    warnings = []
    if unsupported:
        warnings.append("Preview may be incomplete; these HPGL commands are not shown: " + ", ".join(sorted(unsupported)))
    return paths, warnings


def _format_number(value: float) -> str:
    rounded = round(value, 3)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:g}"


def hpgl_preview(data: bytes) -> PreviewResult:
    paths, warnings = parse_hpgl(data)
    if not paths:
        raise PreviewError("No pen-down cut lines were found in this HPGL file.")

    points = [point for path in paths for point in path]
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    width = max_x - min_x
    height = max_y - min_y
    extent = max(width, height, 1.0)
    margin = max(extent * 0.04, 5.0)
    view_x = min_x - margin
    view_y = -max_y - margin
    view_width = max(width, 1.0) + 2 * margin
    view_height = max(height, 1.0) + 2 * margin
    stroke_width = max(extent / 700.0, 0.8)

    path_elements = []
    for path in paths:
        commands = [f"M {_format_number(path[0][0])} {_format_number(-path[0][1])}"]
        commands.extend(f"L {_format_number(x)} {_format_number(-y)}" for x, y in path[1:])
        path_elements.append(f'<path d="{" ".join(commands)}"/>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_format_number(view_x)} {_format_number(view_y)} {_format_number(view_width)} {_format_number(view_height)}" preserveAspectRatio="xMidYMid meet">
  <rect x="{_format_number(view_x)}" y="{_format_number(view_y)}" width="{_format_number(view_width)}" height="{_format_number(view_height)}" fill="#f8fafc"/>
  <g fill="none" stroke="#e11d48" stroke-width="{_format_number(stroke_width)}" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke">
    {''.join(path_elements)}
  </g>
</svg>'''.encode("utf-8")
    return PreviewResult(svg, len(paths), width, height, warnings)


def svg_preview(data: bytes) -> PreviewResult:
    """Add a cut-line presentation to an SVG without changing its geometry."""
    text = data.decode("utf-8-sig", errors="replace")
    if not re.search(r"<svg(?:\s|>)", text, flags=re.IGNORECASE):
        raise PreviewError("This file is not a valid SVG document.")

    # SVG loaded through an <img> is already script-isolated.  Removing scripts
    # and inline event handlers also makes the generated response safe if the UI
    # changes how previews are embedded in the future.
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*')", "", text, flags=re.IGNORECASE)
    style = """<style>
svg { background: #f8fafc; }
path, line, polyline, polygon, rect, circle, ellipse, use {
  fill: none !important; stroke: #e11d48 !important; stroke-width: 1.25px !important;
  stroke-linecap: round !important; stroke-linejoin: round !important;
  vector-effect: non-scaling-stroke;
}
text, image, foreignObject { display: none !important; }
</style>"""
    text, count = re.subn(r"(<svg\b[^>]*>)", r"\1" + style, text, count=1, flags=re.IGNORECASE)
    if count != 1:
        raise PreviewError("This file is not a valid SVG document.")

    geometry_count = len(re.findall(r"<(?:path|line|polyline|polygon|rect|circle|ellipse|use)\b", text, re.IGNORECASE))
    if geometry_count == 0:
        raise PreviewError("No vector cut lines were found in this SVG file.")
    return PreviewResult(text.encode("utf-8"), geometry_count, 0.0, 0.0)
