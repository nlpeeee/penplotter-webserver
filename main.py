import os
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

# Read Configuration
config = configparser.ConfigParser()
config.read('config.ini')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024
app.config['UPLOAD_EXTENSIONS'] = ['.svg', '.hpgl']
app.config['UPLOAD_PATH'] = 'uploads'
app.config['SECRET_KEY'] = '#tiUJ791&jPYI9N7Kj'
app.config['DEBUG'] = True

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

def convert(file, pagesize = 'a4', svgscale = 'a4', pageorientation = 'landscape'):
    if file:

        filename, file_extension = os.path.splitext(file)

        outputFile = filename + '_converted_' + pageorientation + '_' + svgscale + '_' + pagesize + '.hpgl'

        # Scale svg to desired paper size
        args = 'vpype';
        args += ' read "' + os.getcwd() + '/' + str(file) + '"'; #Read input svg

        if (pageorientation == 'landscape'):
            if (svgscale == 'a3'):
                args += ' scaleto 39cm 26.7cm';
            elif (svgscale == 'a4'):
                args += ' scaleto 27.7cm 19cm';
        else:
            if (svgscale == 'a3'):
                args += ' scaleto 27.7cm 40cm';
            elif (svgscale == 'a4'):
                args += ' scaleto 19cm 27.7cm';

        args += ' write --device hp7475a';

        args += ' --page-size ' + str(pagesize);

        if (pageorientation == 'landscape'):
            args += ' --landscape';

        args += ' --center';
        args += ' "' + os.getcwd() + '/' + str(outputFile) + '"'

        rendering = subprocess.Popen(args, shell=True)
        rendering.wait() # Hold on till process is finished

        # Delete file
        if os.path.exists(file):
            os.remove(file)
            socketio.emit('status_log', {'data': 'Deleted SVG: ' + str(file)})
        else:
            socketio.emit('error', {'data': 'The file does not exist'})

        return '- Exported ' + str(outputFile)

@app.errorhandler(413)
def too_large(e):
    return "File is too large", 413

@app.route('/preview/<filename>')
def preview_file(filename):
    """Return an SVG preview of the given upload. HPGL is converted via vpype."""
    import tempfile
    filename = secure_filename(filename)
    filepath = os.path.join(app.config['UPLOAD_PATH'], filename)
    if not os.path.exists(filepath):
        abort(404)
    ext = os.path.splitext(filename)[1].lower()
    if ext == '.svg':
        return send_from_directory(app.config['UPLOAD_PATH'], filename, mimetype='image/svg+xml')
    elif ext == '.hpgl':
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                ['vpype', 'read', filepath, 'write', '--device', 'svg', tmp_path],
                capture_output=True, timeout=60
            )
            if result.returncode != 0:
                abort(500)
            with open(tmp_path, 'rb') as f:
                svg_data = f.read()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        return Response(svg_data, mimetype='image/svg+xml')
    else:
        abort(400)

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

    return render_template('index.html', files=files, configuration=configuration, version=version)

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
    if request.method == "GET":
        if globals.active_job_id is not None:
            jobqueue.request_cancel(globals.active_job_id)
        globals.printing = False
        return 'Plotter Stopped'

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
        file = app.config['UPLOAD_PATH'] + '/' + request.form.get('file')
        pagesize = request.form.get('pagesize')
        svgscale = request.form.get('svgscale')
        pageorientation = request.form.get('pageorientation')

        output = convert(file, pagesize, svgscale, pageorientation)

        return output

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
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
