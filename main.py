import os
import math
import shutil
import sys
import tempfile
import time
import threading
import subprocess
import configparser

from flask import Flask, Response, render_template, request, redirect, url_for, abort, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit

import globals
import send2serial
import tasmota
import jobqueue
import serial_control
import workspace
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
    fname = os.path.basename(job['file'])

    _emit_job_update()
    socketio.emit('status_log', {'data': f'[Job {job_id}] Transmitting: {fname}'})
    socketio.emit('lock_edit', {'data': 'on'})

    if job.get('tasmota') == 'on':
        tasmota.tasmota_setStatus(socketio, 'on')
        time.sleep(2)

    result = None
    error_msg = None
    globals.active_job_id = job_id
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
    """Validate a browser workspace request without accepting client geometry."""
    filename = data.get('filename', '')
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename or not safe_filename.lower().endswith('.svg'):
        raise workspace.WorkspaceError('Invalid SVG filename.')
    filepath = os.path.join(app.config['UPLOAD_PATH'], safe_filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError('The selected SVG file does not exist.')
    transform = workspace.parse_transform(data.get('transform') or data)
    preparation = workspace.parse_preparation(data.get('preparation') or {})
    return safe_filename, filepath, transform, preparation


@app.route('/api/workspace/preview', methods=['POST'])
def workspace_preview_api():
    data = request.get_json(silent=True) or {}
    try:
        filename, filepath, transform, preparation = _workspace_svg_request(data)
        _paths, metadata = workspace.build_svg_preview(filepath, transform, preparation)
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except workspace.WorkspaceError as exc:
        return jsonify({'error': str(exc)}), 422
    metadata.update({
        'manifest_version': 1,
        'filename': filename,
        'source_type': 'svg',
        'read_only': False,
        'valid': not metadata['out_of_bounds'],
    })
    response = jsonify(metadata)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/workspace/generate', methods=['POST'])
def workspace_generate_api():
    data = request.get_json(silent=True) or {}
    try:
        filename, filepath, transform, preparation = _workspace_svg_request(data)
        output, metadata = workspace.convert_svg(
            os.path.abspath(filepath),
            transform,
            preparation,
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

@app.route('/')
def index():
    files = make_tree(app.config['UPLOAD_PATH'])

    configuration = {
        'telegram_token': config['telegram']['telegram_token'],
        'telegram_chatid': config['telegram']['telegram_chatid'],
        'tasmota_enable': config['tasmota']['tasmota_enable'],
        'tasmota_ip': config['tasmota']['tasmota_ip'],
        'plotter_name': config['plotter']['name'],
        'plotter_port': config['plotter']['port'],
        'plotter_device': config['plotter']['device'],
        'plotter_baudrate': config['plotter']['baudrate'],
    }

    try:
        version = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                          stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        version = '1'

    # Include the live asset timestamp as deployments may update files without
    # creating a git commit on the plotter. This prevents browsers retaining an
    # older UI under the same commit-based cache key.
    try:
        asset_mtime = max(
            int(os.path.getmtime(os.path.join(app.static_folder, asset)))
            for asset in ('main.js', 'utility.js', 'css/main.css', 'img/pcp-logo.png')
        )
        version = f'{version}-{asset_mtime}'
    except OSError:
        pass

    return render_template('index.html', files=files, configuration=configuration, version=version)


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

        job_id = jobqueue.enqueue_job(file, port, baudrate, device, tasmota_ctrl)
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
        _emit_job_update()
        return jsonify({'status': 'ready', 'port': selected_port, 'phases': phases})
    except serial_control.SerialResetError as exc:
        item = {'phase': 'error', 'message': str(exc), 'status': 'error'}
        emit_phase(item)
        return jsonify({'error': str(exc), 'phases': phases}), 422
    finally:
        with globals.active_serial_lock:
            globals.reset_in_progress = False

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

    # Start the background job-queue worker (daemon so it exits with the app).
    worker = threading.Thread(target=_job_worker, daemon=True, name='job-worker')
    worker.start()

    # app.run(host='127.0.0.1',port=5000,debug=True,threaded=True)
    socketio.run(
        app, host='0.0.0.0', port=5000,
        debug=False, use_reloader=False, allow_unsafe_werkzeug=True,
    )
