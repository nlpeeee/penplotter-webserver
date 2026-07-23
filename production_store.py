"""SQLite persistence for PCP material profiles and cutter calibrations."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import tempfile
import uuid


DB_PATH = os.environ.get("WEBPLOT_DB", "jobs.db")
PROJECTS_ROOT = os.environ.get("PCP_PROJECTS_DIR", "projects")
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
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    material_profile_id TEXT,
    deleted_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS project_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, sha256)
);
CREATE TABLE IF NOT EXISTS project_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL,
    manifest_json TEXT NOT NULL,
    hpgl_path TEXT NOT NULL,
    thumbnail_path TEXT NOT NULL,
    geometry_hash TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, revision_number)
);
CREATE TABLE IF NOT EXISTS project_drafts (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    manifest_json TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _connect():
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
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


def _project(row):
    if row is None:
        return None
    result = dict(row)
    result["tags"] = json.loads(result.pop("tags_json") or "[]")
    result["deleted"] = result["deleted_at"] is not None
    return result


def _revision(row):
    if row is None:
        return None
    result = dict(row)
    result["manifest"] = json.loads(result.pop("manifest_json"))
    return result


def _project_directory(project_id):
    root = os.path.abspath(PROJECTS_ROOT)
    directory = os.path.abspath(os.path.join(root, project_id))
    if os.path.commonpath((root, directory)) != root:
        raise ValueError("Invalid project directory.")
    return directory


def _normalise_project_values(values, existing=None):
    existing = existing or {}
    name = str(values.get("name", existing.get("name", ""))).strip()
    if not name or len(name) > 150:
        raise ValueError("Project name must contain 1 to 150 characters.")
    tags = values.get("tags", existing.get("tags", [])) or []
    if not isinstance(tags, list) or len(tags) > 50:
        raise ValueError("Project tags must be a list of at most 50 values.")
    tags = [str(tag).strip()[:50] for tag in tags if str(tag).strip()]
    return {
        "name": name,
        "notes": str(values.get("notes", existing.get("notes", "")))[:5000],
        "tags": tags,
        "material_profile_id": values.get(
            "material_profile_id", existing.get("material_profile_id")
        ) or UNPROFILED_ID,
    }


def _atomic_bytes(destination, data):
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        prefix=".pcp-", suffix=".tmp", dir=os.path.dirname(destination), delete=False
    )
    try:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary.name, destination)
    finally:
        if not temporary.closed:
            temporary.close()
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)


def _asset_for_source(connection, project_id, source_path, original_filename):
    with open(source_path, "rb") as source:
        data = source.read()
    digest = hashlib.sha256(data).hexdigest()
    existing = connection.execute(
        "SELECT * FROM project_assets WHERE project_id=? AND sha256=?",
        (project_id, digest),
    ).fetchone()
    if existing:
        existing_asset = dict(existing)
        recreated = None
        if not os.path.isfile(existing_asset["stored_path"]):
            _atomic_bytes(existing_asset["stored_path"], data)
            recreated = existing_asset["stored_path"]
        return existing_asset, recreated
    extension = os.path.splitext(original_filename)[1].lower()
    if extension not in {".svg", ".hpgl"}:
        raise ValueError("Project assets must be SVG or HPGL files.")
    stored_path = os.path.join(
        _project_directory(project_id), "assets", digest + extension
    )
    created_path = None
    if not os.path.exists(stored_path):
        _atomic_bytes(stored_path, data)
        created_path = stored_path
    asset = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "sha256": digest,
        "original_filename": os.path.basename(original_filename),
        "stored_path": stored_path,
        "media_type": extension[1:],
    }
    connection.execute(
        """
        INSERT INTO project_assets
            (id, project_id, sha256, original_filename, stored_path, media_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            asset["id"], project_id, digest, asset["original_filename"],
            stored_path, asset["media_type"],
        ),
    )
    return asset, created_path


def _self_contained_manifest(connection, project_id, manifest, sources):
    result = json.loads(json.dumps(manifest))
    source_by_index = {source["item_index"]: source for source in sources}
    created_paths = []
    for item_index, item in enumerate(result.get("items", [])):
        source = source_by_index.get(item_index)
        if source is None:
            raise ValueError("Every project design must have a server-resolved source asset.")
        asset, created = _asset_for_source(
            connection, project_id, source["source_path"], source["original_filename"]
        )
        if created:
            created_paths.append(created)
        item["project_asset_id"] = asset["id"]
        item["filename"] = asset["original_filename"]
    return result, created_paths


def save_project_revision(
    project_values,
    manifest,
    sources,
    hpgl_data,
    thumbnail_data,
    geometry_hash,
    project_id=None,
):
    """Atomically add an immutable numbered revision and self-contained assets."""
    new_project = project_id is None
    project_id = str(uuid.uuid4()) if new_project else str(project_id)
    directory = _project_directory(project_id)
    revision_directory = None
    created_assets = []
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if new_project:
            values = _normalise_project_values(project_values)
            connection.execute(
                """
                INSERT INTO projects
                    (id, name, notes, tags_json, material_profile_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id, values["name"], values["notes"],
                    json.dumps(values["tags"]), values["material_profile_id"],
                ),
            )
        else:
            existing = _project(connection.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone())
            if existing is None or existing["deleted"]:
                raise ValueError("Active project not found.")
            values = _normalise_project_values(project_values, existing)
            connection.execute(
                """
                UPDATE projects
                SET name=?, notes=?, tags_json=?, material_profile_id=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    values["name"], values["notes"], json.dumps(values["tags"]),
                    values["material_profile_id"], project_id,
                ),
            )
        contained_manifest, created_assets = _self_contained_manifest(
            connection, project_id, manifest, sources
        )
        row = connection.execute(
            "SELECT COALESCE(MAX(revision_number), 0) + 1 AS next FROM project_revisions WHERE project_id=?",
            (project_id,),
        ).fetchone()
        revision_number = row["next"]
        contained_manifest["project_context"] = {
            "project_id": project_id,
            "revision_number": revision_number,
        }
        revision_directory = os.path.join(
            directory, "revisions", f"{revision_number:04d}"
        )
        hpgl_path = os.path.join(revision_directory, geometry_hash + ".hpgl")
        thumbnail_path = os.path.join(revision_directory, "thumbnail.svg")
        if os.path.exists(revision_directory):
            raise ValueError("The immutable project revision already exists.")
        os.makedirs(revision_directory, exist_ok=False)
        _atomic_bytes(hpgl_path, hpgl_data)
        _atomic_bytes(thumbnail_path, thumbnail_data)
        cursor = connection.execute(
            """
            INSERT INTO project_revisions (
                project_id, revision_number, manifest_json, hpgl_path,
                thumbnail_path, geometry_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id, revision_number,
                json.dumps(contained_manifest, sort_keys=True),
                hpgl_path, thumbnail_path, geometry_hash,
            ),
        )
        connection.execute("DELETE FROM project_drafts WHERE project_id=?", (project_id,))
        connection.commit()
        return get_project_revision(project_id, revision_number)
    except Exception:
        connection.rollback()
        if revision_directory and os.path.isdir(revision_directory):
            shutil.rmtree(revision_directory)
        for path in created_assets:
            if os.path.isfile(path):
                os.unlink(path)
        if new_project and os.path.isdir(directory):
            shutil.rmtree(directory)
        raise
    finally:
        connection.close()


def list_projects(include_deleted=False):
    connection = _connect()
    try:
        where = "" if include_deleted else "WHERE p.deleted_at IS NULL"
        rows = connection.execute(
            f"""
            SELECT p.*,
                (SELECT MAX(revision_number) FROM project_revisions r WHERE r.project_id=p.id)
                    AS latest_revision,
                (SELECT thumbnail_path FROM project_revisions r WHERE r.project_id=p.id
                 ORDER BY revision_number DESC LIMIT 1) AS thumbnail_path
            FROM projects p {where}
            ORDER BY p.updated_at DESC, p.name
            """
        ).fetchall()
        return [_project(row) for row in rows]
    finally:
        connection.close()


def get_project(project_id, include_deleted=False):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM projects WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL"),
            (project_id,),
        ).fetchone()
        project = _project(row)
        if project:
            project["revisions"] = [
                dict(revision) for revision in connection.execute(
                    """
                    SELECT id, revision_number, geometry_hash, hpgl_path,
                           thumbnail_path, created_at
                    FROM project_revisions WHERE project_id=?
                    ORDER BY revision_number DESC
                    """,
                    (project_id,),
                ).fetchall()
            ]
            draft = connection.execute(
                "SELECT manifest_json, updated_at FROM project_drafts WHERE project_id=?",
                (project_id,),
            ).fetchone()
            project["recovery_draft"] = (
                {"manifest": json.loads(draft["manifest_json"]), "updated_at": draft["updated_at"]}
                if draft else None
            )
        return project
    finally:
        connection.close()


def get_project_revision(project_id, revision_number):
    connection = _connect()
    try:
        row = connection.execute(
            """
            SELECT * FROM project_revisions
            WHERE project_id=? AND revision_number=?
            """,
            (project_id, int(revision_number)),
        ).fetchone()
        revision = _revision(row)
        if revision:
            revision["project"] = get_project(project_id, include_deleted=True)
        return revision
    finally:
        connection.close()


def get_project_asset(asset_id):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM project_assets WHERE id=?", (asset_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def save_recovery_draft(project_id, manifest, sources):
    connection = _connect()
    created_assets = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        project = connection.execute(
            "SELECT id FROM projects WHERE id=? AND deleted_at IS NULL", (project_id,)
        ).fetchone()
        revision = connection.execute(
            "SELECT 1 FROM project_revisions WHERE project_id=? LIMIT 1", (project_id,)
        ).fetchone()
        if not project or not revision:
            raise ValueError("A deliberate project save is required before autosave.")
        contained, created_assets = _self_contained_manifest(
            connection, project_id, manifest, sources
        )
        connection.execute(
            """
            INSERT INTO project_drafts (project_id, manifest_json)
            VALUES (?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                manifest_json=excluded.manifest_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (project_id, json.dumps(contained, sort_keys=True)),
        )
        connection.commit()
        return {"project_id": project_id, "saved": True}
    except Exception:
        connection.rollback()
        for path in created_assets:
            if os.path.isfile(path):
                os.unlink(path)
        raise
    finally:
        connection.close()


def update_project(project_id, values):
    existing = get_project(project_id)
    if existing is None:
        return None
    normalised = _normalise_project_values(values, existing)
    connection = _connect()
    try:
        connection.execute(
            """
            UPDATE projects SET name=?, notes=?, tags_json=?, material_profile_id=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND deleted_at IS NULL
            """,
            (
                normalised["name"], normalised["notes"],
                json.dumps(normalised["tags"]), normalised["material_profile_id"],
                project_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return get_project(project_id)


def soft_delete_project(project_id):
    connection = _connect()
    try:
        changed = connection.execute(
            """
            UPDATE projects SET deleted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND deleted_at IS NULL
            """,
            (project_id,),
        ).rowcount
        connection.commit()
        return bool(changed)
    finally:
        connection.close()


def restore_project(project_id):
    connection = _connect()
    try:
        changed = connection.execute(
            """
            UPDATE projects SET deleted_at=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND deleted_at IS NOT NULL
            """,
            (project_id,),
        ).rowcount
        connection.commit()
        return get_project(project_id) if changed else None
    finally:
        connection.close()


def purge_project(project_id):
    project = get_project(project_id, include_deleted=True)
    if project is None:
        return False
    if not project["deleted"]:
        raise ValueError("Soft-delete the project before permanent purge.")
    directory = _project_directory(project_id)
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM projects WHERE id=?", (project_id,))
        connection.commit()
    finally:
        connection.close()
    if os.path.isdir(directory):
        shutil.rmtree(directory)
    return True
