import os
import math
import shutil
import sys
import tempfile
import time
import threading
import subprocess
import configparser
import sqlite3
import re

from flask import Flask, Response, render_template, request, redirect, url_for, abort, send_from_directory, send_file, jsonify
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit

import globals
import send2serial
import tasmota
import jobqueue
import serial_control
import workspace
import production_store
from preview import PreviewError, hpgl_preview, svg_preview

# Read Configuration
config = configparser.ConfigParser()
config.read('config.ini')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
app.config['UPLOAD_EXTENSIONS'] = ['.svg', '.hpgl']
app.config['UPLOAD_PATH'] = 'uploads'
app.config['SECRET_KEY'] = '#tiUJ791&jPYI9N7Kj'
app.config['DEBUG'] = False

socketio = SocketIO(app)


# ──────────────────────────────────────────────────────────────────────────────
# Background job-queue worker
# ──────────────────────────────────────────────────────────────────────────────

def _emit_job_update():
    socketio.emit('job_update', {'jobs': jobqueue.get_recent_jobs()})


def _execute_job(job):
    """Run a single plot job, updating its status throughout."""
    job_id = job['id']
    fname = job.get('display_file') or os.path.basename(job['file'])

    _emit_job_update()
    socketio.emit('status_log', {'data': f'[Job {job_id}] Transmitting: {fname}'})
    socketio.emit('lock_edit', {'data': 'on'})

    if job.get('tasmota') == 'on':
        tasmota.tasmota_setStatus(socketio, 'on')
        time.sleep(2)

    result = None
    error_msg = None
    globals.active_job_id = job_id
    globals.print_progress = 0.0
    try:
        if not jobqueue.is_cancel_requested(job_id):
            result = send2serial.sendToPlotter(
                socketio,
                job['file'],
                job['port'],
                int(job['baudrate']),
                job['device'],
                lambda: jobqueue.is_cancel_requested(job_id),
            )
    except Exception as e:
        error_msg = str(e)
        socketio.emit('error', {'data': f'[Job {job_id}] Exception: {error_msg}'})

    # Determine final status:
    #   - globals.printing was cleared by /stop_plot → cancelled
    #   - result is True and printing still set → completed
    #   - otherwise → failed
    if jobqueue.is_cancel_requested(job_id) or not globals.printing:
        status = 'cancelled'
    elif result is True:
        status = 'completed'
    else:
        status = 'failed'

    globals.printing = False
    globals.active_job_id = None
    globals.print_progress = 100.0 if status == 'completed' else 0.0

    jobqueue.update_job_status(job_id, status, error_msg)
    socketio.emit('status_log', {'data': f'[Job {job_id}] {status.capitalize()}.'})
    socketio.emit('lock_edit', {'data': 'off'})
    _emit_job_update()

    if job.get('tasmota') == 'on':
        tasmota.tasmota_setStatus(socketio, 'off')


def _job_worker():
    """Daemon thread: drain the job queue one job at a time."""
    while True:
        # Wait up to 3 s for a signal, then check anyway (poll fallback).
        globals.job_worker_event.wait(timeout=3)
        globals.job_worker_event.clear()

        # Process all waiting jobs before going back to sleep.
        while True:
            job = jobqueue.claim_next_queued()
            if not job:
                break
            _execute_job(job)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_tree(path):
    tree = dict(name=os.path.basename(path), content=[])
    try: lst = os.listdir(path)
    except OSError:
        pass #ignore errors
    else:
        for name in lst:
            fn = os.path.join(path, name)
            if os.path.isdir(fn):
                tree['content'].append(make_tree(fn))
            else:
                if (name != '.gitignore'):
                    tree['content'].append(dict(name=name))
    return tree

class ConversionError(RuntimeError):
    """An SVG could not be converted into a valid HPGL file."""


def _vpype_executable():
    """Find vpype beside the running Python before consulting PATH."""
    scripts_dir = os.path.dirname(sys.executable)
    for executable_name in ('vpype', 'vpype.exe'):
        candidate = os.path.join(scripts_dir, executable_name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    executable = shutil.which('vpype')
    if executable:
        return executable
    raise ConversionError('vpype is not installed in the PCP runtime environment.')


def svg_geometry_dimensions(file):
    """Return the visible vector geometry's width and height in millimetres."""
    try:
        payload = workspace.workspace_payload(file)
    except Exception as exc:
        raise ConversionError('Could not read the SVG geometry: ' + str(exc)) from exc
    return payload['width_mm'], payload['height_mm']


def _size_label(value):
    return f'{value:.1f}'.rstrip('0').rstrip('.')


def convert(file, target_width_mm, target_height_mm, preparation=None, expected_geometry_hash=None, **options):
    """Create HPGL from the same millimetre transform shown in the workspace."""
    values = dict(options)
    values['target_width_mm'] = target_width_mm
    values['target_height_mm'] = target_height_mm
    try:
        transform = workspace.parse_transform(values)
        output_file, metadata = workspace.convert_svg(
            os.path.abspath(file),
            transform,
            workspace.parse_preparation(preparation),
            expected_geometry_hash=expected_geometry_hash,
        )
    except workspace.WorkspaceError as exc:
        raise ValueError(str(exc)) from exc
    return output_file, metadata['width_mm'], metadata['height_mm']

@app.errorhandler(413)
def too_large(e):
    return "File is too large", 413

@app.route('/preview/<filename>')
def preview_file(filename):
    """Return a dependency-free SVG rendering of the file's cut lines."""
    filename = secure_filename(filename)
    filepath = os.path.join(app.config['UPLOAD_PATH'], filename)
    if not os.path.exists(filepath):
        abort(404)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in app.config['UPLOAD_EXTENSIONS']:
        abort(400)

    try:
        with open(filepath, 'rb') as preview_source:
            data = preview_source.read()
        result = svg_preview(data) if ext == '.svg' else hpgl_preview(data)
    except PreviewError as exc:
        return jsonify({'error': str(exc)}), 422
    except OSError:
        return jsonify({'error': 'The preview file could not be read.'}), 500

    response = Response(result.svg, mimetype='image/svg+xml')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Preview-Paths'] = str(result.path_count)
    if ext == '.hpgl':
        # Creation/vpype HPGL uses 40 plotter units per millimetre.
        response.headers['X-Preview-Width-MM'] = f'{result.width_units / 40.0:.1f}'
        response.headers['X-Preview-Height-MM'] = f'{result.height_units / 40.0:.1f}'
    if result.warnings:
        response.headers['X-Preview-Warning'] = '; '.join(result.warnings)
    return response


@app.route('/cut_workspace/<filename>')
def cut_workspace(filename):
    """Return ordered millimetre geometry for the interactive cutter workspace."""
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        return jsonify({'error': 'Invalid preview filename.'}), 400
    filepath = os.path.join(app.config['UPLOAD_PATH'], safe_filename)
    if not os.path.isfile(filepath):
        return jsonify({'error': 'The selected file does not exist.'}), 404
    try:
        payload = workspace.workspace_payload(filepath)
    except workspace.WorkspaceError as exc:
        return jsonify({'error': str(exc)}), 422
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'no-store'
    return response


def _workspace_svg_request(data):
    """Validate a browser manifest without accepting client-supplied geometry."""
    raw_items = data.get('items')
    if raw_items is None:
        raw_items = [{
            'filename': data.get('filename', ''),
            **(data.get('transform') or data),
            'copies': 1,
        }]
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 20:
        raise workspace.WorkspaceError('A workspace must contain between 1 and 20 SVG designs.')
    roll_width = workspace._finite_number(
        data.get('roll_width_mm', (data.get('transform') or {}).get('roll_width_mm', 1200)),
        'Roll width',
    )
    items = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise workspace.WorkspaceError('Every workspace design must be an object.')
        asset_id = str(raw_item.get('project_asset_id') or '')
        if asset_id:
            asset = production_store.get_project_asset(asset_id)
            if not asset or asset['media_type'] != 'svg' or not os.path.isfile(asset['stored_path']):
                raise FileNotFoundError('The selected project SVG asset does not exist.')
            safe_filename = asset['original_filename']
            filepath = asset['stored_path']
        else:
            filename = raw_item.get('filename', '')
            safe_filename = secure_filename(filename)
            if not safe_filename or safe_filename != filename or not safe_filename.lower().endswith('.svg'):
                raise workspace.WorkspaceError('Invalid SVG filename.')
            filepath = os.path.join(app.config['UPLOAD_PATH'], safe_filename)
            if not os.path.isfile(filepath):
                raise FileNotFoundError('The selected SVG file does not exist: ' + safe_filename)
        values = dict(raw_item)
        values['roll_width_mm'] = roll_width
        values.setdefault('offset_x_mm', 0)
        values.setdefault('offset_y_mm', 0)
        items.append({
            'filename': safe_filename,
            'filepath': filepath,
            'transform': workspace.parse_transform(values),
            'copies': raw_item.get('copies', 1),
            'placements': raw_item.get('placements') or [],
            'project_asset_id': asset_id or None,
        })
    preparation = workspace.parse_preparation(data.get('preparation') or {})
    layout = workspace.parse_layout(data.get('layout') or {})
    cutting_aids = workspace.parse_cutting_aids(data.get('cutting_aids') or {})
    calibration_request = data.get('calibration') or {}
    project_context = data.get('project_context') or {}
    stored_revision = None
    if (
        project_context
        and (
            calibration_request.get('revision_snapshot')
            or project_context.get('profile_snapshot')
        )
    ):
        if project_context.get('draft_snapshot'):
            draft_project = production_store.get_project(project_context.get('project_id'))
            if draft_project and draft_project.get('recovery_draft'):
                stored_revision = {'manifest': draft_project['recovery_draft']['manifest']}
        else:
            stored_revision = production_store.get_project_revision(
                project_context.get('project_id'), project_context.get('revision_number')
            )
        if stored_revision is None:
            raise workspace.WorkspaceError('The source project revision does not exist.')
    if calibration_request.get('revision_snapshot') and stored_revision:
        snapshot = stored_revision['manifest'].get('calibration_snapshot') or {}
        calibration = workspace.parse_calibration(snapshot)
    elif workspace._truthy(calibration_request.get('enabled', False)):
        serial_port = str(calibration_request.get('serial_port', ''))
        device = str(calibration_request.get('device', ''))
        stored_calibration = production_store.get_calibration(serial_port, device)
        if not stored_calibration or not stored_calibration['accepted']:
            raise workspace.WorkspaceError(
                'The selected cutter does not have an accepted calibration.'
            )
        if not stored_calibration['enabled']:
            raise workspace.WorkspaceError(
                'The selected cutter calibration is currently disabled.'
            )
        calibration = workspace.parse_calibration({
            **stored_calibration,
            'serial_port': serial_port,
            'device': device,
        })
    else:
        calibration = workspace.Calibration(
            serial_port=str(calibration_request.get('serial_port', ''))[:500],
            device=str(calibration_request.get('device', ''))[:100],
        )
    profile_id = str(data.get('material_profile_id') or production_store.UNPROFILED_ID)
    if stored_revision and project_context.get('profile_snapshot'):
        profile_snapshot = stored_revision['manifest'].get('profile_snapshot')
    else:
        profile_snapshot = production_store.get_profile(profile_id)
    if profile_snapshot is None:
        raise workspace.WorkspaceError('The selected material profile does not exist.')
    return (
        items, roll_width, layout, preparation, cutting_aids,
        calibration, profile_snapshot,
    )


@app.route('/api/workspace/preview', methods=['POST'])
def workspace_preview_api():
    data = request.get_json(silent=True) or {}
    try:
        (
            items, roll_width, layout, preparation, cutting_aids,
            calibration, profile_snapshot,
        ) = _workspace_svg_request(data)
        _paths, metadata = workspace.build_manifest_preview(
            items, roll_width, layout, preparation, cutting_aids, calibration
        )
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except workspace.WorkspaceError as exc:
        return jsonify({'error': str(exc)}), 422
    metadata.update({
        'manifest_version': 1,
        'filename': items[0]['filename'],
        'profile_snapshot': profile_snapshot,
    })
    response = jsonify(metadata)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/workspace/generate', methods=['POST'])
def workspace_generate_api():
    data = request.get_json(silent=True) or {}
    try:
        (
            items, roll_width, layout, preparation, cutting_aids,
            calibration, profile_snapshot,
        ) = _workspace_svg_request(data)
        output, metadata = workspace.convert_manifest(
            items,
            roll_width,
            layout,
            preparation,
            os.path.abspath(app.config['UPLOAD_PATH']),
            cutting_aids=cutting_aids,
            calibration=calibration,
            expected_geometry_hash=data.get('geometry_hash'),
        )
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except workspace.WorkspaceError as exc:
        return jsonify({'error': str(exc)}), 422
    output_filename = os.path.basename(output)
    socketio.emit('status_log', {'data': 'Created HPGL: ' + output_filename})
    return jsonify({
        'message': (
            'Created ' + output_filename + ' at '
            + _size_label(metadata['width_mm']) + ' × ' + _size_label(metadata['height_mm']) + ' mm'
        ),
        'filename': output_filename,
        'width_mm': round(metadata['width_mm'], 2),
        'height_mm': round(metadata['height_mm'], 2),
        'geometry_hash': metadata['geometry_hash'],
        'statistics': metadata['after'],
        'warnings': metadata['warnings'],
        'calibration': metadata['calibration'],
        'profile_snapshot': profile_snapshot,
    })


@app.route('/api/workspace/test-pattern', methods=['POST'])
def workspace_test_pattern():
    """Create PCP's compact, known-dimension compensation test source."""
    filename = 'PCP_compensation_test.svg'
    destination = os.path.join(app.config['UPLOAD_PATH'], filename)
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="90mm" height="55mm" viewBox="0 0 90 55">
<path d="M5 5H25V25H5Z"/>
<circle cx="40" cy="15" r="10"/>
<path d="M55 25L65 5L75 25L65 17Z"/>
<circle cx="7" cy="37" r=".5"/><circle cx="12" cy="37" r="1"/><circle cx="18" cy="37" r="1.5"/>
<path d="M28 33H48V48H28Z"/>
<path d="M56 34L65 48L74 34L68 39L65 34L62 39Z"/>
<path d="M80 34V48M77 37H83M77 45H83"/>
</svg>'''
    temporary = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', prefix='.pcp-test-', suffix='.svg',
        dir=app.config['UPLOAD_PATH'], delete=False,
    )
    try:
        temporary.write(svg)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary.name, destination)
    except OSError as exc:
        if not temporary.closed:
            temporary.close()
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)
        return jsonify({'error': 'Could not create the test pattern: ' + str(exc)}), 500
    return jsonify({
        'filename': filename,
        'message': 'Created the PCP compensation test pattern. Preview it before cutting.',
        'requires_operator_confirmation': True,
    })


@app.route('/api/material-profiles', methods=['GET', 'POST'])
def material_profiles_api():
    if request.method == 'GET':
        return jsonify({'profiles': production_store.list_profiles()})
    try:
        profile = production_store.create_profile(request.get_json(silent=True) or {})
    except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
        return jsonify({'error': str(exc)}), 422
    return jsonify(profile), 201


@app.route('/api/material-profiles/export')
def material_profiles_export_api():
    response = jsonify(production_store.export_profiles())
    response.headers['Content-Disposition'] = 'attachment; filename=pcp-material-profiles.json'
    return response


@app.route('/api/material-profiles/import', methods=['POST'])
def material_profiles_import_api():
    try:
        profiles = production_store.import_profiles(request.get_json(silent=True) or {})
    except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
        return jsonify({'error': str(exc)}), 422
    return jsonify({'imported': profiles}), 201


@app.route('/api/material-profiles/<profile_id>', methods=['GET', 'PUT', 'DELETE'])
def material_profile_api(profile_id):
    if request.method == 'GET':
        profile = production_store.get_profile(profile_id)
        return (jsonify(profile), 200) if profile else (jsonify({'error': 'Profile not found.'}), 404)
    try:
        if request.method == 'DELETE':
            if not production_store.delete_profile(profile_id):
                return jsonify({'error': 'Profile not found.'}), 404
            return jsonify({'deleted': True})
        profile = production_store.update_profile(
            profile_id, request.get_json(silent=True) or {}
        )
        if profile is None:
            return jsonify({'error': 'Profile not found.'}), 404
        return jsonify(profile)
    except (ValueError, TypeError, sqlite3.IntegrityError) as exc:
        return jsonify({'error': str(exc)}), 422


@app.route('/api/material-profiles/<profile_id>/verify', methods=['POST'])
def material_profile_verify_api(profile_id):
    try:
        profile = production_store.mark_profile_verified(
            profile_id,
            (request.get_json(silent=True) or {}).get('test_cut_accepted') is True,
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    if profile is None:
        return jsonify({'error': 'Profile not found.'}), 404
    return jsonify(profile)


@app.route('/api/cutter-calibrations', methods=['GET', 'POST', 'PUT'])
def cutter_calibrations_api():
    if request.method == 'GET':
        serial_port = request.args.get('serial_port')
        device = request.args.get('device')
        if serial_port is not None and device is not None:
            calibration = production_store.get_calibration(serial_port, device)
            return jsonify({'calibration': calibration})
        return jsonify({'calibrations': production_store.list_calibrations()})
    values = request.get_json(silent=True) or {}
    try:
        if request.method == 'PUT':
            calibration = production_store.set_calibration_enabled(
                str(values.get('serial_port', '')),
                str(values.get('device', '')),
                values.get('enabled') is True,
            )
            if calibration is None:
                return jsonify({'error': 'Accepted cutter calibration not found.'}), 404
            return jsonify(calibration)
        serial_port = str(values.get('serial_port', ''))
        if not (
            serial_port.startswith('/dev/serial/by-id/')
            or re.fullmatch(r'COM[1-9][0-9]*', serial_port, re.IGNORECASE)
        ):
            raise ValueError(
                'Calibration requires a stable /dev/serial/by-id/... port '
                '(or a COM port on Windows).'
            )
        candidate = production_store.calibration_candidate(
            values.get('measured_x_mm'), values.get('measured_y_mm')
        )
        if values.get('accept') is not True:
            return jsonify({
                **candidate,
                'accepted': False,
                'enabled': False,
                'requires_additional_confirmation': candidate['large_correction'],
            })
        calibration = production_store.save_calibration(
            serial_port,
            values.get('device'),
            values.get('measured_x_mm'),
            values.get('measured_y_mm'),
            enabled=values.get('enabled') is True,
            confirm_large_correction=values.get('confirm_large_correction') is True,
        )
        return jsonify(calibration), 201
    except (ValueError, TypeError) as exc:
        return jsonify({'error': str(exc)}), 422


@app.route('/api/cutter-calibrations/pattern', methods=['POST'])
def cutter_calibration_pattern_api():
    filename = 'PCP_calibration_100mm.svg'
    destination = os.path.join(app.config['UPLOAD_PATH'], filename)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
        'viewBox="0 0 100 100"><path d="M0 0H100V100H0Z"/></svg>'
    )
    temporary = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', prefix='.pcp-calibration-', suffix='.svg',
        dir=app.config['UPLOAD_PATH'], delete=False,
    )
    try:
        temporary.write(svg)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary.name, destination)
    except OSError as exc:
        if not temporary.closed:
            temporary.close()
        if os.path.exists(temporary.name):
            os.unlink(temporary.name)
        return jsonify({'error': 'Could not create the calibration square: ' + str(exc)}), 500
    return jsonify({
        'filename': filename,
        'message': (
            'Created the exact 100 × 100 mm calibration square. '
            'Preview it before physical cutting.'
        ),
        'requires_operator_confirmation': True,
    })


def _project_thumbnail(paths, metadata):
    path_data = []
    for path in paths:
        if len(path) < 2:
            continue
        path_data.append(
            'M' + ' L'.join(f'{x:.3f},{y:.3f}' for x, y in path)
        )
    width = max(metadata['max_x_mm'] - metadata['min_x_mm'], 0.1)
    height = max(metadata['max_y_mm'] - metadata['min_y_mm'], 0.1)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" '
        f'viewBox="{metadata["min_x_mm"]:.3f} {metadata["min_y_mm"]:.3f} '
        f'{width:.3f} {height:.3f}" preserveAspectRatio="xMidYMid meet">'
        '<rect width="100%" height="100%" fill="#17232d"/>'
        + ''.join(
            f'<path d="{data}" fill="none" stroke="#e11d48" '
            'stroke-width=".35" vector-effect="non-scaling-stroke"/>'
            for data in path_data
        )
        + '</svg>'
    )
    return svg.encode('utf-8')


def _canonical_project_manifest(data, metadata):
    return {
        'manifest_version': 1,
        'roll_width_mm': data.get('roll_width_mm', 1200),
        'items': [
            {
                key: item.get(key)
                for key in (
                    'filename', 'project_asset_id', 'target_width_mm',
                    'target_height_mm', 'rotation', 'mirror_x', 'mirror_y',
                    'copies', 'placements', 'natural_width_mm',
                    'natural_height_mm',
                )
                if item.get(key) is not None
            }
            for item in data.get('items', [])
        ],
        'layout': data.get('layout') or {},
        'preparation': data.get('preparation') or {},
        'cutting_aids': data.get('cutting_aids') or {},
        'material_profile_id': data.get('material_profile_id') or production_store.UNPROFILED_ID,
        'profile_snapshot': metadata.get('profile_snapshot'),
        'calibration': data.get('calibration') or {},
        'calibration_snapshot': metadata.get('calibration'),
    }


def _save_project_request(project_id=None):
    data = request.get_json(silent=True) or {}
    workspace_data = data.get('workspace') or {}
    if not isinstance(workspace_data.get('items'), list) or not workspace_data.get('items'):
        return jsonify({'error': 'A project workspace must contain source design items.'}), 422
    expected_hash = workspace_data.get('geometry_hash')
    try:
        (
            items, roll_width, layout, preparation, cutting_aids,
            calibration, profile_snapshot,
        ) = _workspace_svg_request(workspace_data)
        paths, metadata = workspace.build_manifest_preview(
            items, roll_width, layout, preparation, cutting_aids, calibration
        )
        metadata['profile_snapshot'] = profile_snapshot
        if not metadata['valid']:
            raise workspace.WorkspaceError(
                'The workspace must be valid before saving a project revision.'
            )
        if expected_hash and expected_hash != metadata['geometry_hash']:
            raise workspace.WorkspaceError(
                'The workspace changed after preview. Refresh it before saving.'
            )
        project_values = data.get('project') or {}
        project_values['material_profile_id'] = (
            workspace_data.get('material_profile_id') or production_store.UNPROFILED_ID
        )
        manifest = _canonical_project_manifest(workspace_data, metadata)
        sources = [
            {
                'item_index': index,
                'source_path': item['filepath'],
                'original_filename': item['filename'],
            }
            for index, item in enumerate(items)
        ]
        revision = production_store.save_project_revision(
            project_values,
            manifest,
            sources,
            workspace.hpgl_bytes(paths),
            _project_thumbnail(paths, metadata),
            metadata['geometry_hash'],
            project_id=project_id,
        )
        return jsonify(revision), 201
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except (workspace.WorkspaceError, ValueError, OSError, sqlite3.IntegrityError) as exc:
        return jsonify({'error': str(exc)}), 422


@app.route('/api/projects', methods=['GET', 'POST'])
def projects_api():
    if request.method == 'GET':
        return jsonify({
            'projects': production_store.list_projects(
                include_deleted=request.args.get('include_deleted') == 'true'
            )
        })
    return _save_project_request()


@app.route('/api/projects/<project_id>', methods=['GET', 'PUT', 'DELETE'])
def project_api(project_id):
    if request.method == 'GET':
        project = production_store.get_project(
            project_id, include_deleted=request.args.get('include_deleted') == 'true'
        )
        return (jsonify(project), 200) if project else (jsonify({'error': 'Project not found.'}), 404)
    if request.method == 'DELETE':
        if not production_store.soft_delete_project(project_id):
            return jsonify({'error': 'Project not found.'}), 404
        return jsonify({'deleted': True, 'recoverable': True})
    try:
        project = production_store.update_project(
            project_id, request.get_json(silent=True) or {}
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        return jsonify({'error': str(exc)}), 422
    return (jsonify(project), 200) if project else (jsonify({'error': 'Project not found.'}), 404)


@app.route('/api/projects/<project_id>/revisions', methods=['POST'])
def project_revision_create_api(project_id):
    return _save_project_request(project_id)


@app.route('/api/projects/<project_id>/revisions/<int:revision_number>')
def project_revision_api(project_id, revision_number):
    revision = production_store.get_project_revision(project_id, revision_number)
    return (jsonify(revision), 200) if revision else (jsonify({'error': 'Revision not found.'}), 404)


@app.route('/api/project-assets/<asset_id>/workspace')
def project_asset_workspace_api(asset_id):
    asset = production_store.get_project_asset(asset_id)
    if not asset or asset['media_type'] != 'svg' or not os.path.isfile(asset['stored_path']):
        return jsonify({'error': 'Project SVG asset not found.'}), 404
    try:
        payload = workspace.workspace_payload(asset['stored_path'])
    except workspace.WorkspaceError as exc:
        return jsonify({'error': str(exc)}), 422
    payload['project_asset_id'] = asset_id
    payload['filename'] = asset['original_filename']
    return jsonify(payload)


@app.route('/api/projects/<project_id>/revisions/<int:revision_number>/thumbnail')
def project_thumbnail_api(project_id, revision_number):
    revision = production_store.get_project_revision(project_id, revision_number)
    if not revision or not os.path.isfile(revision['thumbnail_path']):
        return jsonify({'error': 'Project thumbnail not found.'}), 404
    return send_file(revision['thumbnail_path'], mimetype='image/svg+xml', max_age=0)


@app.route('/api/projects/<project_id>/draft', methods=['PUT'])
def project_draft_api(project_id):
    data = request.get_json(silent=True) or {}
    try:
        items, _roll, _layout, _prep, _aids, _calibration, _profile = _workspace_svg_request(data)
        sources = [
            {
                'item_index': index,
                'source_path': item['filepath'],
                'original_filename': item['filename'],
            }
            for index, item in enumerate(items)
        ]
        manifest = _canonical_project_manifest(data, {
            'profile_snapshot': _profile,
            'calibration': {
                'enabled': _calibration.enabled,
                'factor_x': _calibration.factor_x,
                'factor_y': _calibration.factor_y,
                'serial_port': _calibration.serial_port,
                'device': _calibration.device,
            },
        })
        return jsonify(production_store.save_recovery_draft(
            project_id, manifest, sources
        ))
    except (FileNotFoundError, ValueError, workspace.WorkspaceError) as exc:
        return jsonify({'error': str(exc)}), 422


@app.route('/api/projects/<project_id>/restore', methods=['POST'])
def project_restore_api(project_id):
    project = production_store.restore_project(project_id)
    return (jsonify(project), 200) if project else (jsonify({'error': 'Deleted project not found.'}), 404)


@app.route('/api/projects/<project_id>/purge', methods=['POST'])
def project_purge_api(project_id):
    try:
        if not production_store.purge_project(project_id):
            return jsonify({'error': 'Project not found.'}), 404
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 422
    return jsonify({'purged': True})


@app.route('/api/projects/<project_id>/duplicate', methods=['POST'])
def project_duplicate_api(project_id):
    values = request.get_json(silent=True) or {}
    source = production_store.get_project(project_id)
    if source is None or not source['revisions']:
        return jsonify({'error': 'Project revision not found.'}), 404
    revision_number = int(values.get('revision_number') or source['revisions'][0]['revision_number'])
    revision = production_store.get_project_revision(project_id, revision_number)
    try:
        sources = []
        for index, item in enumerate(revision['manifest'].get('items', [])):
            asset = production_store.get_project_asset(item.get('project_asset_id'))
            if not asset:
                raise ValueError('Project source asset not found.')
            sources.append({
                'item_index': index,
                'source_path': asset['stored_path'],
                'original_filename': asset['original_filename'],
            })
        with open(revision['hpgl_path'], 'rb') as hpgl_source:
            hpgl_data = hpgl_source.read()
        with open(revision['thumbnail_path'], 'rb') as thumbnail_source:
            thumbnail_data = thumbnail_source.read()
        duplicate = production_store.save_project_revision(
            {
                'name': values.get('name') or source['name'] + ' copy',
                'notes': source['notes'],
                'tags': source['tags'],
                'material_profile_id': source['material_profile_id'],
            },
            revision['manifest'],
            sources,
            hpgl_data,
            thumbnail_data,
            revision['geometry_hash'],
        )
        return jsonify(duplicate), 201
    except (ValueError, OSError, sqlite3.IntegrityError) as exc:
        return jsonify({'error': str(exc)}), 422


@app.route('/api/projects/<project_id>/revisions/<int:revision_number>/cut-again', methods=['POST'])
def project_cut_again_api(project_id, revision_number):
    revision = production_store.get_project_revision(project_id, revision_number)
    if not revision or not os.path.isfile(revision['hpgl_path']):
        return jsonify({'error': 'Stored project HPGL not found.'}), 404
    if revision['project'].get('deleted'):
        return jsonify({'error': 'Restore the soft-deleted project before cutting it.'}), 422
    values = request.get_json(silent=True) or {}
    if not values.get('port'):
        return jsonify({'error': 'A serial port is required.'}), 422
    source_names = [
        item.get('filename', '') for item in revision['manifest'].get('items', [])
    ]
    if any(name.startswith(('PCP_compensation_test', 'PCP_calibration_100mm')) for name in source_names):
        if values.get('operator_confirm_test') != 'confirmed':
            return jsonify({
                'error': 'Confirm media and tool readiness before repeating this physical test.',
                'requires_operator_confirmation': True,
            }), 409
    try:
        spooled = jobqueue.snapshot_for_queue(revision['hpgl_path'])
        display_file = f"{revision['project']['name']} — revision {revision_number}.hpgl"
        job_id = jobqueue.enqueue_job(
            spooled,
            values.get('port'),
            values.get('baudrate') or '9600',
            values.get('device') or 'creation_1200',
            values.get('tasmota') or 'off',
            display_file=display_file,
            project_id=project_id,
            project_revision=revision_number,
        )
    except OSError as exc:
        return jsonify({'error': str(exc)}), 422
    _emit_job_update()
    globals.job_worker_event.set()
    return jsonify({
        'job_id': job_id,
        'message': f'Project revision {revision_number} queued byte-for-byte.',
    })


@app.route('/svg_dimensions/<filename>')
def svg_dimensions(filename):
    """Return the SVG cut geometry's natural aspect ratio and dimensions."""
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename or not safe_filename.lower().endswith('.svg'):
        return jsonify({'error': 'Invalid SVG filename.'}), 400
    filepath = os.path.join(app.config['UPLOAD_PATH'], safe_filename)
    if not os.path.isfile(filepath):
        return jsonify({'error': 'The selected SVG file does not exist.'}), 404
    try:
        width, height = svg_geometry_dimensions(filepath)
    except ConversionError as exc:
        return jsonify({'error': str(exc)}), 422
    return jsonify({
        'width_mm': round(width, 2),
        'height_mm': round(height, 2),
        'aspect_ratio': width / height,
        'max_width_mm': 1200,
        'max_height_mm': 20000,
    })

def _configuration_payload():
    return {
        'telegram_token': config['telegram']['telegram_token'],
        'telegram_chatid': config['telegram']['telegram_chatid'],
        'tasmota_enable': config['tasmota']['tasmota_enable'],
        'tasmota_ip': config['tasmota']['tasmota_ip'],
        'plotter_name': config['plotter']['name'],
        'plotter_port': config['plotter']['port'],
        'plotter_device': config['plotter']['device'],
        'plotter_baudrate': config['plotter']['baudrate'],
    }


def _asset_version(extra_assets=()):
    try:
        version = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        version = '1'

    assets = (
        'main.js', 'utility.js', 'css/main.css', 'img/pcp-logo.png',
    ) + tuple(extra_assets)
    try:
        asset_mtime = max(
            int(os.path.getmtime(os.path.join(app.static_folder, asset)))
            for asset in assets
        )
        version = f'{version}-{asset_mtime}'
    except OSError:
        pass
    return version


def _render_v1():
    return render_template(
        'index.html',
        files=make_tree(app.config['UPLOAD_PATH']),
        configuration=_configuration_payload(),
        version=_asset_version(),
    )


def _render_v2(view='new-cut'):
    return render_template(
        'v2.html',
        configuration=_configuration_payload(),
        initial_view=view,
        version=_asset_version(('v2/app.js', 'v2/app.css')),
    )


@app.route('/')
def index():
    if os.environ.get('PCP_UI_DEFAULT', 'v1').lower() == 'v2':
        return _render_v2('new-cut')
    return _render_v1()


@app.route('/v1')
def v1_index():
    return _render_v1()


@app.route('/v2')
def v2_index():
    return _render_v2('new-cut')


@app.route('/v2/<view>')
def v2_view(view):
    if view not in {'workbench', 'projects', 'jobs', 'settings'}:
        abort(404)
    return _render_v2(view)


@app.route('/api/ui-state')
def ui_state():
    configured_port = config['plotter'].get('port', '')
    detected_ports = send2serial.listComPorts().get('content', [])
    configured_port_exists = bool(configured_port and os.path.exists(configured_port))
    if configured_port_exists and configured_port not in detected_ports:
        # pyserial reports the resolved tty (for example /dev/ttyUSB0), while
        # PCP deliberately stores the stable /dev/serial/by-id/... symlink.
        detected_ports.insert(0, configured_port)
    recent_jobs = jobqueue.get_recent_jobs()
    active_job = next(
        (
            job for job in recent_jobs
            if job['id'] == globals.active_job_id
            or job.get('status') == 'transmitting'
        ),
        None,
    )
    queue_count = jobqueue.get_queue_count()

    if globals.reset_in_progress:
        port_state, serial_operation = 'resetting', 'reset'
    elif globals.active_job_id is not None:
        port_state, serial_operation = 'busy', 'cut'
    elif configured_port and (
        configured_port in detected_ports or configured_port_exists
    ):
        port_state, serial_operation = 'available', 'idle'
    elif configured_port:
        port_state, serial_operation = 'missing', 'idle'
    else:
        port_state, serial_operation = 'unknown', 'idle'
    if globals.last_connection_error and port_state not in {'busy', 'resetting'}:
        port_state = 'error'

    if active_job:
        active_job = dict(active_job)
        active_job['progress'] = round(float(globals.print_progress), 2)

    return jsonify({
        'plotter': {
            'name': config['plotter'].get('name', 'Plotter'),
            'device': config['plotter'].get('device', 'creation_1200'),
            'baudrate': config['plotter'].get('baudrate', '9600'),
            'configured_port': configured_port,
            'port_state': port_state,
            'detected_ports': detected_ports,
            'serial_operation': serial_operation,
            'reset_phase': globals.reset_phase,
            'last_connection_error': globals.last_connection_error,
        },
        'active_job': active_job,
        'queue_count': queue_count,
    })


@app.route('/license')
def license_text():
    """Serve the bundled license referenced by the application footer."""
    return send_from_directory(app.root_path, 'LICENSE', mimetype='text/plain')

# Upload
@app.route('/', methods=['POST'])
def upload_files():
    uploaded_file = request.files['file']
    filename = secure_filename(uploaded_file.filename)
    if filename != '':
        file_ext = os.path.splitext(filename)[1]
        if file_ext not in app.config['UPLOAD_EXTENSIONS']:
            return "Invalid image", 400
        uploaded_file.save(os.path.join(app.config['UPLOAD_PATH'], filename))
    return '', 204

@app.route('/uploads/<filename>')
def upload(filename):
    return send_from_directory(app.config['UPLOAD_PATH'], filename)

# Fetch Files
@app.route('/update_files', methods=['GET'])
def update_files():
    files = make_tree(app.config['UPLOAD_PATH'])
    return files

# List COM Ports — always include the configured port so stable /dev/serial/by-id
# paths are selectable even when not among currently enumerated devices.
@app.route('/update_ports', methods=['GET'])
def update_ports():
    configured_port = config['plotter'].get('port', '')
    ports = send2serial.listComPorts(extra_port=configured_port if configured_port else None)
    return ports

# Delete uploaded filed
@app.route('/delete_file', methods=['GET', 'POST'])
def delete_file():
    if request.method == "POST":
        data = request.get_json(silent=True)
        filename = data.get('filename')

        # Delete file
        if os.path.exists(app.config['UPLOAD_PATH'] + "/" + filename):
            os.remove(app.config['UPLOAD_PATH'] + "/" + filename)
            socketio.emit('status_log', {'data': 'Deleted: ' + filename})
            return 'Deleted: ' + filename
        else:
            socketio.emit('error', {'data': 'The file does not exist'})
            return 'The file does not exist'

# Enqueue a plot job
@app.route('/start_plot', methods=['GET', 'POST'])
def start_plot():
    if request.method == "POST":
        # Sanitize filename to prevent path traversal before constructing the path.
        raw_file = request.form.get('file', '')
        filename = secure_filename(raw_file)
        if not filename:
            return 'No file specified', 400
        file = os.path.join(app.config['UPLOAD_PATH'], filename)
        port = request.form.get('port')
        baudrate = request.form.get('baudrate') or '9600'
        tasmota_ctrl = request.form.get('tasmota') or 'off'
        device = request.form.get('device') or '7475a'

        if not os.path.exists(file):
            return 'File not found', 400
        if (
            (
                filename.startswith('PCP_compensation_test')
                or filename.startswith('PCP_calibration_100mm')
            )
            and request.form.get('operator_confirm_test') != 'confirmed'
        ):
            return jsonify({
                'error': (
                    'Confirm that media is loaded, the blade/tool is ready, and this '
                    'physical compensation test may be transmitted.'
                ),
                'requires_operator_confirmation': True,
            }), 409

        spooled_file = jobqueue.snapshot_for_queue(file)
        job_id = jobqueue.enqueue_job(
            spooled_file, port, baudrate, device, tasmota_ctrl,
            display_file=filename,
        )
        _emit_job_update()
        socketio.emit('status_log', {'data': f'[Job {job_id}] Queued: {os.path.basename(file)}'})

        # Wake up the worker thread
        globals.job_worker_event.set()

        return f'Job {job_id} queued'

# Stop the currently-transmitting job (cancels further byte transmission;
# cannot recall commands already in the cutter's hardware buffer).
@app.route('/stop_plot', methods=['GET', 'POST'])
def stop_plot():
    active_job_id = globals.active_job_id
    changed = False
    if active_job_id is not None:
        changed = jobqueue.request_cancel(active_job_id)
    globals.printing = False
    interrupted = serial_control.cancel_active_write()
    socketio.emit('status_log', {
        'data': 'Cut cancelled. The cutter may finish commands already in its internal buffer.'
    })
    socketio.emit('plot_cancelled', {'job_id': active_job_id, 'buffer_warning': True})
    _emit_job_update()
    return jsonify({
        'status': 'cancel_requested' if active_job_id is not None else 'idle',
        'job_id': active_job_id,
        'changed': changed,
        'write_interrupted': interrupted,
        'warning': 'The cutter may finish commands already in its internal buffer.',
    })

# Cancel a queued job or stop future transmission for the active job.
@app.route('/cancel_job', methods=['POST'])
def cancel_job():
    data = request.get_json(silent=True) or {}
    job_id = data.get('job_id')
    if job_id is None:
        return 'Missing job_id', 400
    changed = jobqueue.request_cancel(int(job_id))
    if changed and globals.active_job_id == int(job_id):
        globals.printing = False
        serial_control.cancel_active_write()
    _emit_job_update()
    return ('Cancelled' if changed else 'Not found or already running'), (200 if changed else 404)

# Return recent job history as JSON.
@app.route('/job_history', methods=['GET'])
def job_history():
    return jsonify({'jobs': jobqueue.get_recent_jobs()})

# Start converting file using vpype
@app.route('/start_conversion', methods=['GET', 'POST'])
def start_conversion():
    if request.method == "POST":
        requested_filename = request.form.get('file', '')
        filename = secure_filename(requested_filename)
        if not filename or filename != requested_filename:
            return jsonify({'error': 'Invalid SVG filename.'}), 400
        file = os.path.join(app.config['UPLOAD_PATH'], filename)
        target_width = request.form.get('target_width_mm')
        target_height = request.form.get('target_height_mm')

        try:
            output, output_width, output_height = convert(
                file,
                target_width,
                target_height,
                roll_width_mm=request.form.get('roll_width_mm', 1200),
                offset_x_mm=request.form.get('offset_x_mm', 0),
                offset_y_mm=request.form.get('offset_y_mm', 0),
                rotation=request.form.get('rotation', 0),
                mirror_x=request.form.get('mirror_x', ''),
                mirror_y=request.form.get('mirror_y', ''),
                preparation={
                    'enabled': request.form.get('preparation_enabled', 'true'),
                    'remove_duplicates': request.form.get('remove_duplicates', 'true'),
                    'inside_first': request.form.get('inside_first', 'true'),
                    'minimize_travel': request.form.get('minimize_travel', 'true'),
                    'merge_enabled': request.form.get('merge_enabled', ''),
                    'merge_tolerance_mm': request.form.get('merge_tolerance_mm', 0.05),
                    'simplify_enabled': request.form.get('simplify_enabled', ''),
                    'simplify_tolerance_mm': request.form.get('simplify_tolerance_mm', 0.05),
                },
                expected_geometry_hash=request.form.get('geometry_hash') or None,
            )
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({'error': str(exc)}), 400
        except ConversionError as exc:
            socketio.emit('error', {'data': str(exc)})
            return jsonify({'error': str(exc)}), 500

        output_filename = os.path.basename(output)
        socketio.emit('status_log', {'data': 'Created HPGL: ' + output_filename})
        return jsonify({
            'message': (
                'Created ' + output_filename + ' at '
                + _size_label(output_width) + ' × ' + _size_label(output_height) + ' mm'
            ),
            'filename': output_filename,
            'width_mm': round(output_width, 2),
            'height_mm': round(output_height, 2),
        })


@app.route('/reset_plotter_connection', methods=['POST'])
def reset_plotter_connection():
    """Cancel transmission and reset only the configured USB serial adapter."""
    data = request.get_json(silent=True) or request.form
    selected_port = data.get('port', '')
    configured_port = config['plotter'].get('port', '')
    baudrate = int(config['plotter'].get('baudrate', '9600'))

    with globals.active_serial_lock:
        if globals.reset_in_progress:
            return jsonify({'error': 'A USB reset is already in progress.'}), 409
        globals.reset_in_progress = True

    phases = []

    def emit_phase(item):
        phases.append(item)
        globals.reset_phase = item.get('phase')
        if item.get('status') == 'error':
            globals.last_connection_error = item.get('message')
        socketio.emit('connection_reset', item)
        socketio.emit('status_log', {'data': item['message']})

    try:
        active_job_id = globals.active_job_id
        if active_job_id is not None:
            jobqueue.request_cancel(active_job_id)
        globals.printing = False
        serial_control.cancel_active_write()
        emit_phase({
            'phase': 'cancelling',
            'message': 'Cancelling any active transmission',
            'status': 'working',
        })
        reset_phases = serial_control.reset_usb_serial(
            selected_port, configured_port, baudrate=baudrate, progress=emit_phase
        )
        globals.last_connection_error = None
        _emit_job_update()
        return jsonify({'status': 'ready', 'port': selected_port, 'phases': phases})
    except serial_control.SerialResetError as exc:
        item = {'phase': 'error', 'message': str(exc), 'status': 'error'}
        emit_phase(item)
        return jsonify({'error': str(exc), 'phases': phases}), 422
    finally:
        with globals.active_serial_lock:
            globals.reset_in_progress = False
            if globals.reset_phase != 'error':
                globals.reset_phase = None

# Start reboot sequence
@app.route('/action_reboot', methods=['GET', 'POST'])
def action_reboot():
    if request.method == "POST":
        response = Response('action_reboot started')

        @response.call_on_close
        def on_close():
            rendering = subprocess.Popen('sudo reboot', shell=True)
            rendering.wait() # Hold on till process is finished

        return response

# Start poweroff sequence
@app.route('/action_poweroff', methods=['GET', 'POST'])
def action_poweroff():
    if request.method == "POST":
        response = Response('action_poweroff started')

        @response.call_on_close
        def on_close():
            rendering = subprocess.Popen('sudo poweroff', shell=True)
            rendering.wait() # Hold on till process is finished

        return response

# Toggle tasmota switch
@app.route('/action_tasmota', methods=['GET', 'POST'])
def action_tasmota():
    if request.method == "POST":
        tasmota.tasmota_setToggle(socketio)

        return 'action_tasmota started'

# Update configfile values
@app.route('/save_configfile', methods=['GET', 'POST'])
def save_configfile():
    if request.method == "POST":
        if "telegram_token" in request.form:
            config['telegram']['telegram_token'] = request.form.get('telegram_token')
        if "telegram_chatid" in request.form:
            config['telegram']['telegram_chatid'] = request.form.get('telegram_chatid')

        if "tasmota_enable" in request.form:
            config['tasmota']['tasmota_enable'] = request.form.get('tasmota_enable')
        if "tasmota_ip" in request.form:
            config['tasmota']['tasmota_ip'] = request.form.get('tasmota_ip')

        if "plotter_name" in request.form:
            config['plotter']['name'] = request.form.get('plotter_name')
        if "plotter_port" in request.form:
            config['plotter']['port'] = request.form.get('plotter_port')
        if "plotter_device" in request.form:
            config['plotter']['device'] = request.form.get('plotter_device')
        if "plotter_baudrate" in request.form:
            config['plotter']['baudrate'] = request.form.get('plotter_baudrate')

        with open('config.ini', 'w') as configfile:
            config.write(configfile)

        output = 'Configuration Updated'
        return output
    elif request.method == "GET":

        output = {
            'telegram_token': config['telegram']['telegram_token'],
            'telegram_chatid': config['telegram']['telegram_chatid'],
            'tasmota_enable': config['tasmota']['tasmota_enable'],
            'tasmota_ip': config['tasmota']['tasmota_ip'],
            'plotter_name': config['plotter']['name'],
            'plotter_port': config['plotter']['port'],
            'plotter_device': config['plotter']['device'],
            'plotter_baudrate': config['plotter']['baudrate'],
        }
        return output

# On connection — push current job history to the new client.
@socketio.event
def connection(message):
    print('Client connected')
    emit('job_update', {'jobs': jobqueue.get_recent_jobs()})

if __name__ == "__main__":

    # Globals variables
    globals.initialize()

    # Initialise SQLite job database
    jobqueue.init_db()
    production_store.init_db()

    # Start the background job-queue worker (daemon so it exits with the app).
    worker = threading.Thread(target=_job_worker, daemon=True, name='job-worker')
    worker.start()

    # app.run(host='127.0.0.1',port=5000,debug=True,threaded=True)
    socketio.run(
        app, host='0.0.0.0', port=5000,
        debug=False, use_reloader=False, allow_unsafe_werkzeug=True,
    )
