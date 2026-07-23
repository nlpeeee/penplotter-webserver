"""SQLite persistence for PCP material profiles and cutter calibrations."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid


DB_PATH = os.environ.get("WEBPLOT_DB", "jobs.db")
UNPROFILED_ID = "unprofiled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS material_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    notes TEXT NOT NULL DEFAULT '',
    roll_width_mm REAL NOT NULL DEFAULT 1200,
    edge_margin_mm REAL NOT NULL DEFAULT 5,
    copy_spacing_mm REAL NOT NULL DEFAULT 5,
    suggested_pressure TEXT NOT NULL DEFAULT '',
    suggested_speed TEXT NOT NULL DEFAULT '',
    weed_settings_json TEXT NOT NULL DEFAULT '{}',
    blade_offset_mm REAL NOT NULL DEFAULT 0.25,
    blade_offset_enabled INTEGER NOT NULL DEFAULT 0,
    overcut_mm REAL NOT NULL DEFAULT 1,
    overcut_enabled INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS cutter_calibrations (
    serial_port TEXT NOT NULL,
    device TEXT NOT NULL,
    measured_x_mm REAL NOT NULL,
    measured_y_mm REAL NOT NULL,
    factor_x REAL NOT NULL,
    factor_y REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (serial_port, device)
);
"""


def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    connection = _connect()
    try:
        connection.executescript(_SCHEMA)
        connection.execute(
            """
            INSERT OR IGNORE INTO material_profiles
                (id, name, notes, verified)
            VALUES (?, 'Unprofiled', 'Permanent default with no material-specific settings.', 0)
            """,
            (UNPROFILED_ID,),
        )
        connection.commit()
    finally:
        connection.close()


def _profile(row):
    if row is None:
        return None
    result = dict(row)
    result["weed_settings"] = json.loads(result.pop("weed_settings_json") or "{}")
    for field in ("blade_offset_enabled", "overcut_enabled", "verified"):
        result[field] = bool(result[field])
    result["deletable"] = result["id"] != UNPROFILED_ID
    return result


def list_profiles():
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT * FROM material_profiles ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, name",
            (UNPROFILED_ID,),
        ).fetchall()
        return [_profile(row) for row in rows]
    finally:
        connection.close()


def get_profile(profile_id):
    connection = _connect()
    try:
        return _profile(connection.execute(
            "SELECT * FROM material_profiles WHERE id=?", (profile_id,)
        ).fetchone())
    finally:
        connection.close()


_PROFILE_FIELDS = (
    "name", "notes", "roll_width_mm", "edge_margin_mm", "copy_spacing_mm",
    "suggested_pressure", "suggested_speed", "blade_offset_mm",
    "blade_offset_enabled", "overcut_mm", "overcut_enabled",
)


def _normalise_profile(values, existing=None):
    existing = existing or {}
    name = str(values.get("name", existing.get("name", ""))).strip()
    if not name or len(name) > 100:
        raise ValueError("Profile name must contain 1 to 100 characters.")
    result = {
        "name": name,
        "notes": str(values.get("notes", existing.get("notes", "")))[:2000],
        "roll_width_mm": float(values.get("roll_width_mm", existing.get("roll_width_mm", 1200))),
        "edge_margin_mm": float(values.get("edge_margin_mm", existing.get("edge_margin_mm", 5))),
        "copy_spacing_mm": float(values.get("copy_spacing_mm", existing.get("copy_spacing_mm", 5))),
        "suggested_pressure": str(values.get(
            "suggested_pressure", existing.get("suggested_pressure", "")
        ))[:100],
        "suggested_speed": str(values.get(
            "suggested_speed", existing.get("suggested_speed", "")
        ))[:100],
        "blade_offset_mm": float(values.get(
            "blade_offset_mm", existing.get("blade_offset_mm", 0.25)
        )),
        "blade_offset_enabled": bool(values.get(
            "blade_offset_enabled", existing.get("blade_offset_enabled", False)
        )),
        "overcut_mm": float(values.get("overcut_mm", existing.get("overcut_mm", 1))),
        "overcut_enabled": bool(values.get(
            "overcut_enabled", existing.get("overcut_enabled", False)
        )),
        "weed_settings": values.get(
            "weed_settings", existing.get("weed_settings", {})
        ) or {},
    }
    if not 0.1 <= result["roll_width_mm"] <= 1200:
        raise ValueError("Profile roll width must be between 0.1 and 1200 mm.")
    if not 0 <= result["edge_margin_mm"] <= 100:
        raise ValueError("Profile edge margin must be between 0 and 100 mm.")
    if not 0 <= result["copy_spacing_mm"] <= 100:
        raise ValueError("Profile copy spacing must be between 0 and 100 mm.")
    if not 0.01 <= result["blade_offset_mm"] <= 5:
        raise ValueError("Profile blade offset must be between 0.01 and 5 mm.")
    if not 0.01 <= result["overcut_mm"] <= 10:
        raise ValueError("Profile overcut must be between 0.01 and 10 mm.")
    if not isinstance(result["weed_settings"], dict):
        raise ValueError("Profile weed settings must be an object.")
    return result


def create_profile(values):
    profile = _normalise_profile({
        **values,
        # New profiles must begin with physical compensation disabled.
        "blade_offset_enabled": False,
        "overcut_enabled": False,
    })
    profile_id = str(uuid.uuid4())
    connection = _connect()
    try:
        connection.execute(
            """
            INSERT INTO material_profiles (
                id, name, notes, roll_width_mm, edge_margin_mm, copy_spacing_mm,
                suggested_pressure, suggested_speed, weed_settings_json,
                blade_offset_mm, blade_offset_enabled, overcut_mm,
                overcut_enabled, verified
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0)
            """,
            (
                profile_id, profile["name"], profile["notes"],
                profile["roll_width_mm"], profile["edge_margin_mm"],
                profile["copy_spacing_mm"], profile["suggested_pressure"],
                profile["suggested_speed"],
                json.dumps(profile["weed_settings"], sort_keys=True),
                profile["blade_offset_mm"], profile["overcut_mm"],
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_profile(profile_id)


def update_profile(profile_id, values):
    existing = get_profile(profile_id)
    if existing is None:
        return None
    profile = _normalise_profile(values, existing)
    if profile_id == UNPROFILED_ID:
        profile["name"] = "Unprofiled"
        profile["blade_offset_enabled"] = False
        profile["overcut_enabled"] = False
    connection = _connect()
    try:
        connection.execute(
            """
            UPDATE material_profiles SET
                name=?, notes=?, roll_width_mm=?, edge_margin_mm=?,
                copy_spacing_mm=?, suggested_pressure=?, suggested_speed=?,
                weed_settings_json=?, blade_offset_mm=?,
                blade_offset_enabled=?, overcut_mm=?, overcut_enabled=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                profile["name"], profile["notes"], profile["roll_width_mm"],
                profile["edge_margin_mm"], profile["copy_spacing_mm"],
                profile["suggested_pressure"], profile["suggested_speed"],
                json.dumps(profile["weed_settings"], sort_keys=True),
                profile["blade_offset_mm"], int(profile["blade_offset_enabled"]),
                profile["overcut_mm"], int(profile["overcut_enabled"]), profile_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_profile(profile_id)


def mark_profile_verified(profile_id, test_cut_accepted):
    if profile_id == UNPROFILED_ID:
        raise ValueError("The Unprofiled default cannot be verified.")
    if test_cut_accepted is not True:
        raise ValueError("Accept the completed physical test cut before verification.")
    connection = _connect()
    try:
        changed = connection.execute(
            """
            UPDATE material_profiles
            SET verified=1, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (profile_id,),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return get_profile(profile_id) if changed else None


def delete_profile(profile_id):
    if profile_id == UNPROFILED_ID:
        raise ValueError("The Unprofiled default cannot be deleted.")
    connection = _connect()
    try:
        changed = connection.execute(
            "DELETE FROM material_profiles WHERE id=?", (profile_id,)
        ).rowcount
        connection.commit()
        return bool(changed)
    finally:
        connection.close()


def export_profiles():
    return {"format": "pcp-material-profiles", "version": 1, "profiles": list_profiles()}


def import_profiles(document):
    if (
        not isinstance(document, dict)
        or document.get("format") != "pcp-material-profiles"
        or document.get("version") != 1
        or not isinstance(document.get("profiles"), list)
    ):
        raise ValueError("This is not a PCP material-profile export.")
    imported = []
    for values in document["profiles"]:
        if not isinstance(values, dict) or values.get("id") == UNPROFILED_ID:
            continue
        profile = create_profile(values)
        profile = update_profile(profile["id"], values)
        if values.get("verified") is True:
            profile = mark_profile_verified(profile["id"], True)
        imported.append(profile)
    return imported


def _calibration(row):
    if row is None:
        return None
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    result["accepted"] = bool(result["accepted"])
    result["large_correction"] = (
        abs(result["factor_x"] - 1) > 0.02 or abs(result["factor_y"] - 1) > 0.02
    )
    return result


def list_calibrations():
    connection = _connect()
    try:
        return [_calibration(row) for row in connection.execute(
            "SELECT * FROM cutter_calibrations ORDER BY serial_port, device"
        ).fetchall()]
    finally:
        connection.close()


def get_calibration(serial_port, device):
    connection = _connect()
    try:
        return _calibration(connection.execute(
            "SELECT * FROM cutter_calibrations WHERE serial_port=? AND device=?",
            (serial_port, device),
        ).fetchone())
    finally:
        connection.close()


def calibration_candidate(measured_x_mm, measured_y_mm):
    measured_x = float(measured_x_mm)
    measured_y = float(measured_y_mm)
    if measured_x <= 0 or measured_y <= 0:
        raise ValueError("Measured calibration dimensions must be greater than zero.")
    factor_x = 100.0 / measured_x
    factor_y = 100.0 / measured_y
    if not 0.90 <= factor_x <= 1.10 or not 0.90 <= factor_y <= 1.10:
        raise ValueError("Calibration factors must remain between 0.90 and 1.10.")
    return {
        "measured_x_mm": measured_x,
        "measured_y_mm": measured_y,
        "factor_x": factor_x,
        "factor_y": factor_y,
        "large_correction": abs(factor_x - 1) > 0.02 or abs(factor_y - 1) > 0.02,
    }


def save_calibration(
    serial_port, device, measured_x_mm, measured_y_mm,
    enabled=False, confirm_large_correction=False,
):
    serial_port = str(serial_port).strip()
    device = str(device).strip()
    if not serial_port or not device or len(serial_port) > 500 or len(device) > 100:
        raise ValueError("A stable serial port and cutter device are required.")
    candidate = calibration_candidate(measured_x_mm, measured_y_mm)
    if candidate["large_correction"] and not confirm_large_correction:
        raise ValueError("A correction over 2% requires additional confirmation.")
    connection = _connect()
    try:
        connection.execute(
            """
            INSERT INTO cutter_calibrations (
                serial_port, device, measured_x_mm, measured_y_mm,
                factor_x, factor_y, enabled, accepted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(serial_port, device) DO UPDATE SET
                measured_x_mm=excluded.measured_x_mm,
                measured_y_mm=excluded.measured_y_mm,
                factor_x=excluded.factor_x, factor_y=excluded.factor_y,
                enabled=excluded.enabled, accepted=1,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                serial_port, device, candidate["measured_x_mm"],
                candidate["measured_y_mm"], candidate["factor_x"],
                candidate["factor_y"], int(bool(enabled)),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_calibration(serial_port, device)


def set_calibration_enabled(serial_port, device, enabled):
    connection = _connect()
    try:
        changed = connection.execute(
            """
            UPDATE cutter_calibrations
            SET enabled=?, updated_at=CURRENT_TIMESTAMP
            WHERE serial_port=? AND device=? AND accepted=1
            """,
            (int(bool(enabled)), serial_port, device),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    return get_calibration(serial_port, device) if changed else None
