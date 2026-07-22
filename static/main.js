// Update port list
function updatePorts() {
  axios.get('/update_ports')
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        // Remove old content from list
        jQuery('.portList').html('');
        for (var content of response.data.content) {
          jQuery('.portList').append(`<option value="${content}">${content}</option>`)
        }
        // Re-apply configured port selection (may have been cleared by repopulate)
        var savedPort = jQuery('#portList').data('configured-port');
        if (savedPort) jQuery('#portList').val(savedPort);
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    })
    .then(function() {});
}

function clearSelectedFile() {
  jQuery('#fileName').val('');
  jQuery('#fileList li').removeClass('uk-alert-primary');
  jQuery('.selectedFilename').text('');
}

function selectFileByName(filename) {
  var element = jQuery('.selectFile').filter(function() {
    return jQuery(this).data('filename') === filename;
  }).first();
  if (!element.length) return false;
  selectFile(element);
  return true;
}

// Update file list and preserve (or explicitly set) the selected HPGL file.
function updateFiles(preferredFilename) {
  var selectedFilename = preferredFilename || jQuery('#fileName').val();
  return axios.get('/update_files')
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        // Remove old content from list
        jQuery('#fileList').html('');

        for (var content of response.data.content) {
          jQuery('#fileList').append(`<li> ${renderFileListElement(content.name)} </li>`)
        }
        if (selectedFilename && !selectFileByName(selectedFilename)) {
          clearSelectedFile();
        }
      }
      return response;
    })
    .catch(function(error) {
      notify(error.message || 'Could not update the file list.', 'danger')
      console.error(error);
      return null;
    });
}

// Handle file selection
function selectFile(element) {
  const filename = jQuery(element).data('filename');

  // Update form
  jQuery('#fileName').val(filename);

  // Update list
  jQuery('#fileList li').removeClass('uk-alert-primary');
  const li = jQuery(element).parents('li')[0]
  if (li) jQuery(li).addClass('uk-alert-primary');

  // Update sidebar
  jQuery('.selectedFilename').text(filename);
}

// Handle file deletion
function deleteFile(element) {
  const filename = jQuery(element).data('filename');

  axios.post('/delete_file', { filename: filename })
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        // Remove old content from list
        notify(response.data, 'warning')
        if (jQuery('#fileName').val() === filename) clearSelectedFile();
        updateFiles()
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    })
    .then(function() {});
}

var conversionAspectRatio = null;
var conversionDimensionRequest = 0;

function formatMillimetres(value) {
  return Math.round(value * 10) / 10;
}

function syncConversionDimensions(changedDimension) {
  if (!conversionAspectRatio) return;
  if (changedDimension === 'width') {
    var width = parseFloat(jQuery('#targetWidthMm').val());
    if (Number.isFinite(width) && width > 0) {
      jQuery('#targetHeightMm').val(formatMillimetres(width / conversionAspectRatio));
    }
  } else {
    var height = parseFloat(jQuery('#targetHeightMm').val());
    if (Number.isFinite(height) && height > 0) {
      jQuery('#targetWidthMm').val(formatMillimetres(height * conversionAspectRatio));
    }
  }
}

// Handle file conversion
function convertFileModal(element) {
  const filename = jQuery(element).data('filename');
  const requestId = ++conversionDimensionRequest;
  jQuery('#convertFile').val(filename)
  jQuery('#targetWidthMm, #targetHeightMm').val('').prop('disabled', true);
  jQuery('#conversionSizeError').hide().text('');
  jQuery('#conversionSizeInfo').show().text('Reading SVG dimensions…');
  jQuery('.startConversion').prop('disabled', true).text('Convert');
  UIkit.modal('#modal-convertFile').show();

  jQuery('#targetWidthMm').off('input.linkedScale').on('input.linkedScale', function() {
    syncConversionDimensions('width');
  });
  jQuery('#targetHeightMm').off('input.linkedScale').on('input.linkedScale', function() {
    syncConversionDimensions('height');
  });

  axios.get('/svg_dimensions/' + encodeURIComponent(filename))
    .then(function(response) {
      if (requestId !== conversionDimensionRequest) return;
      conversionAspectRatio = response.data.aspect_ratio;
      jQuery('#targetWidthMm').val(formatMillimetres(response.data.width_mm)).prop('disabled', false);
      jQuery('#targetHeightMm').val(formatMillimetres(response.data.height_mm)).prop('disabled', false);
      jQuery('#conversionSizeInfo').text(
        'Width and height are linked to preserve proportions. Maximum cutting width: 1200 mm.'
      );
      jQuery('.startConversion').prop('disabled', false);
    })
    .catch(function(error) {
      if (requestId !== conversionDimensionRequest) return;
      var message = error.response && error.response.data && error.response.data.error
        ? error.response.data.error
        : (error.message || 'Could not read SVG dimensions.');
      jQuery('#conversionSizeInfo').hide();
      jQuery('#conversionSizeError').text(message).show();
    });
}

// Start conversion
function convertFile() {
  const convertData = jQuery('#convertData').serializeArray()
  const button = jQuery('.startConversion');
  console.log('convertData', convertData);

  // Validation
  if (jQuery('#convertFile').val() == '') {
    notify('No *.svg file selected', 'danger');
    return false
  }

  var targetWidth = parseFloat(jQuery('#targetWidthMm').val());
  var targetHeight = parseFloat(jQuery('#targetHeightMm').val());
  if (!Number.isFinite(targetWidth) || !Number.isFinite(targetHeight) ||
      targetWidth <= 0 || targetHeight <= 0) {
    notify('Enter a valid width and height in millimetres.', 'danger');
    return false;
  }
  if (targetWidth > 1200) {
    notify('Width cannot exceed 1200 mm.', 'danger');
    return false;
  }
  if (targetHeight > 20000) {
    notify('Height cannot exceed 20000 mm.', 'danger');
    return false;
  }

  button.prop('disabled', true).text('Converting…');
  axios.post('/start_conversion', jQuery('#convertData').serialize())
    .then(function(response) {
      console.log(response);
      // handle success
      if (response.status == 200) {
        return updateFiles(response.data.filename).then(function(updateResponse) {
          if (!updateResponse) return;
          UIkit.modal('#modal-convertFile').hide();
          notify(response.data.message + ' and selected it for cutting.', 'success')
        });
      }
    })
    .catch(function(error) {
      var message = error.response && error.response.data && error.response.data.error
        ? error.response.data.error
        : (error.message || 'Conversion failed.');
      notify(message, 'danger')
      console.error(error);
    })
    .then(function() {
      button.prop('disabled', false).text('Convert');
    });
}

// Display card
function closeCard(element) {
  const card = jQuery(element).data('card');

  jQuery(element).addClass('uk-hidden')
  jQuery("#"+card).addClass('uk-hidden')
  jQuery(".showCard[data-card='"+card+"']").removeClass('uk-hidden')
}

function showCard(element) {
  const card = jQuery(element).data('card');

  jQuery(element).addClass('uk-hidden')
  jQuery("#"+card).removeClass('uk-hidden')
  jQuery(".closeCard[data-card='"+card+"']").removeClass('uk-hidden')
}

// Clear Logs
function clearLog() {
  // Remove old content from log
  jQuery('#statusLog').html('');
}

// Start plotting — enqueues a job; the worker processes it asynchronously
function startPlot() {
  const plotterData = jQuery('#plotterData').serializeArray()
  console.log('plotterData', plotterData);

  // Validation
  if (jQuery('#fileName').val() == '') {
    notify('No *.hpgl file selected. Click Select beside an HPGL file.', 'danger');
    return false
  }

  // Determine effective port value
  var useCustom = jQuery('#useCustomPort').is(':checked');
  if (!useCustom && jQuery('#portList').val() == null) {
    notify('No serial port selected', 'danger');
    updatePorts()
    return false
  }

  axios.post('/start_plot', jQuery('#plotterData').serialize())
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        console.log(response);
        notify(response.data, 'success');
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });
}

function stopPlot() {
  jQuery('#cancelCutButton').prop('disabled', true).find('.cancelCutLabel').text('CANCELLING…');
  axios.post('/stop_plot')
    .then(function(response) {
      if (response.status == 200) {
        notify('Transmission cancelled. The cutter may finish buffered commands.', 'danger');
      }
    })
    .catch(function(error) {
      notify(error.message || 'Could not cancel the cut.', 'danger')
      console.error(error);
    })
    .then(function() {
      jQuery('#cancelCutButton .cancelCutLabel').text('CANCEL CUT');
    });
}

function resetPlotterConnection() {
  if (jQuery('#useCustomPort').is(':checked')) {
    notify('USB reset requires the configured stable /dev/serial/by-id port.', 'warning');
    return;
  }
  var port = jQuery('#portList').val();
  UIkit.modal.confirm(
    'Reset the selected USB/COM adapter? Any active cut will be cancelled. This will not power-cycle the cutter.'
  ).then(function() {
    var button = jQuery('#resetConnectionButton');
    button.prop('disabled', true).find('.resetConnectionLabel').text('RESETTING…');
    jQuery('#connectionResetStatus').text('Cancelling any active transmission…');
    return axios.post('/reset_plotter_connection', { port: port })
      .then(function(response) {
        jQuery('#connectionResetStatus').text('USB serial connection is ready.');
        notify('USB serial connection reset successfully.', 'success');
        updatePorts();
        return response;
      })
      .catch(function(error) {
        var message = error.response && error.response.data && error.response.data.error
          ? error.response.data.error : (error.message || 'USB reset failed.');
        jQuery('#connectionResetStatus').text(message);
        notify(message, 'danger');
      })
      .then(function() {
        button.prop('disabled', false).find('.resetConnectionLabel').text('RESET USB / COM');
      });
  }, function() {});
}

// Reboot Pi
function actionReboot() {

  axios.post('/action_reboot')
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        UIkit.modal('#modal-reboot').hide();
        notify('Rebooting now', 'warning');
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });
}

// Poweroff Pi
function actionPoweroff() {

  axios.post('/action_poweroff')
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        UIkit.modal('#modal-poweroff').hide();
        notify('Poweroff now', 'danger');
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });
}

function actionTasmota() {

  axios.post('/action_tasmota')
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        notify('Tasmota Toggled', 'success');
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });
}

// Fetch config.ini data and update UI
function updateConfiguration() {
  axios.get('/save_configfile')
    .then(function(response) {
      // handle success
      if (response.status == 200) {

        jQuery('#telegram_token').val(response.data.telegram_token);
        jQuery('#telegram_chatid').val(response.data.telegram_chatid);
        jQuery('#tasmota_enable').val(response.data.tasmota_enable);
        jQuery('#tasmota_ip').val(response.data.tasmota_ip);

        jQuery('.plotter_name').html(response.data.plotter_name);

        var configuredPort = response.data.plotter_port;
        // Store for re-application after port-list refresh
        jQuery('#portList').data('configured-port', configuredPort);
        // Add the configured port to the list if not already present
        if (configuredPort && jQuery('#portList option[value="' + configuredPort + '"]').length === 0) {
          jQuery('#portList').prepend(`<option value="${configuredPort}">${configuredPort}</option>`);
        }
        jQuery('.portList').val(configuredPort).change();

        jQuery('#device').val(response.data.plotter_device).change();
        jQuery('#baudRate').val(response.data.plotter_baudrate).change();
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });
}


// Fetch config.ini data and display modal
function actionOpenConfig() {
  axios.get('/save_configfile')
    .then(function(response) {
      // handle success
      if (response.status == 200) {

        jQuery('#telegram_token').val(response.data.telegram_token);
        jQuery('#telegram_chatid').val(response.data.telegram_chatid);
        jQuery('#tasmota_enable').val(response.data.tasmota_enable);
        jQuery('#tasmota_ip').val(response.data.tasmota_ip);

        jQuery('#plotter_name').val(response.data.plotter_name);
        jQuery('#plotter_port').val(response.data.plotter_port);
        jQuery('#plotter_device').val(response.data.plotter_device).change();
        jQuery('#plotter_baudrate').val(response.data.plotter_baudrate).change();

        UIkit.modal('#modal-configFile').show();
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });
}

// Save new values in config.ini
function saveConfig() {
  const configData = jQuery('#configData').serializeArray()
  console.log('configData', configData);

  axios.post('/save_configfile', jQuery('#configData').serialize())
    .then(function(response) {
      console.log(response);
      // handle success
      if (response.status == 200) {
        notify(response.data, 'success')
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });
}

// ── Job queue / history ──────────────────────────────────────────────────────

var _statusBadge = {
  queued:       'uk-label',
  transmitting: 'uk-label uk-label-warning',
  completed:    'uk-label uk-label-success',
  failed:       'uk-label uk-label-danger',
  cancelled:    'uk-label uk-label-danger',
};

function renderJobHistory(jobs) {
  var tbody = jQuery('#jobHistoryBody');
  tbody.html('');
  var active = (jobs || []).some(function(job) { return job.status === 'transmitting'; });
  jQuery('#cancelCutButton').prop('disabled', !active).find('.cancelCutLabel').text('CANCEL CUT');
  if (!jobs || jobs.length === 0) {
    tbody.append('<tr><td colspan="7" class="uk-text-center uk-text-muted">No jobs yet</td></tr>');
    return;
  }
  jobs.forEach(function(job) {
    var badge = _statusBadge[job.status] || 'uk-label';
    var fname = job.file ? job.file.split('/').pop() : '';
    var action = '';
    if (job.status === 'queued') {
      action = `<a href="#" class="cancelJob uk-button uk-button-danger uk-button-small" data-job-id="${job.id}">Cancel</a>`;
    } else if (job.status === 'transmitting') {
      action = `<a href="#" class="uk-button uk-button-danger uk-button-small stopPlot">Stop</a>`;
    }
    tbody.append(`<tr>
      <td>${job.id}</td>
      <td>${$('<div/>').text(fname).html()}</td>
      <td>${$('<div/>').text(job.device).html()}</td>
      <td>${$('<div/>').text(job.port).html()}</td>
      <td><span class="${badge}">${job.status}</span></td>
      <td class="uk-text-small">${job.created_at || ''}</td>
      <td>${action}</td>
    </tr>`);
  });
}

function refreshJobHistory() {
  axios.get('/job_history')
    .then(function(response) {
      if (response.status == 200) {
        renderJobHistory(response.data.jobs);
      }
    })
    .catch(function(error) {
      console.error(error);
    });
}

function cancelJob(jobId) {
  axios.post('/cancel_job', { job_id: jobId })
    .then(function(response) {
      if (response.status == 200) {
        notify('Job ' + jobId + ' cancelled', 'warning');
        refreshJobHistory();
      }
    })
    .catch(function(error) {
      notify(error, 'danger');
      console.error(error);
    });
}

var previewRequestId = 0;
var cutterWorkspace = {
  payload: null, filename: '', view: null, metadata: null,
  animationFrame: null, animationProgress: 1, animationElements: [],
  renderedPaths: [], renderedTravels: [], pointer: null, syncingDimensions: false
};

function workspaceNumber(selector, fallback) {
  var value = parseFloat(jQuery(selector).val());
  return Number.isFinite(value) ? value : fallback;
}

function workspacePathD(path) {
  if (!path || !path.length) return '';
  return 'M ' + path.map(function(point) { return point[0] + ' ' + point[1]; }).join(' L ');
}

function workspaceBounds(paths) {
  var xs = [], ys = [];
  paths.forEach(function(path) { path.forEach(function(point) { xs.push(point[0]); ys.push(point[1]); }); });
  return {
    minX: Math.min.apply(null, xs), minY: Math.min.apply(null, ys),
    maxX: Math.max.apply(null, xs), maxY: Math.max.apply(null, ys)
  };
}

// This affine transform intentionally mirrors workspace.transform_paths().
function workspaceTransformedPaths() {
  var payload = cutterWorkspace.payload;
  if (!payload) return [];
  if (payload.read_only) return payload.cut_paths.map(function(path) {
    return path.map(function(point) { return [point[0], point[1]]; });
  });
  var sourceWidth = payload.width_mm, sourceHeight = payload.height_mm;
  var targetWidth = workspaceNumber('#workspaceWidth', sourceWidth);
  var targetHeight = workspaceNumber('#workspaceHeight', sourceHeight);
  var scale = Math.min(targetWidth / sourceWidth, targetHeight / sourceHeight);
  var width = sourceWidth * scale, height = sourceHeight * scale;
  var offsetX = workspaceNumber('#workspaceOffsetX', 0);
  var offsetY = workspaceNumber('#workspaceOffsetY', 0);
  var rotation = parseInt(jQuery('#workspaceRotation').val(), 10) || 0;
  var mirrorX = jQuery('#workspaceMirrorX').is(':checked');
  var mirrorY = jQuery('#workspaceMirrorY').is(':checked');
  return payload.cut_paths.map(function(path) {
    return path.map(function(point) {
      var x = point[0] * scale, y = point[1] * scale, next;
      if (mirrorX) x = width - x;
      if (mirrorY) y = height - y;
      if (rotation === 90) next = [height - y, x];
      else if (rotation === 180) next = [width - x, height - y];
      else if (rotation === 270) next = [y, width - x];
      else next = [x, y];
      return [next[0] + offsetX, next[1] + offsetY];
    });
  });
}

function workspaceTravelPaths(paths) {
  var current = [0, 0];
  return paths.map(function(path) {
    var travel = null;
    if (path.length && (path[0][0] !== current[0] || path[0][1] !== current[1])) {
      travel = [[current[0], current[1]], [path[0][0], path[0][1]]];
    }
    if (path.length) current = path[path.length - 1];
    return travel;
  });
}

function workspaceSetView(box) {
  var width = Math.max(box.width, 1), height = Math.max(box.height, 1);
  cutterWorkspace.view = { x: box.x, y: box.y, width: width, height: height };
  jQuery('#workspaceSvg').attr('viewBox', [box.x, box.y, width, height].join(' '));
  workspaceRenderGrid();
}

function workspaceFitRoll() {
  if (!cutterWorkspace.metadata) return;
  var rollWidth = cutterWorkspace.metadata.rollWidth;
  var rollLength = cutterWorkspace.metadata.rollLength;
  var margin = Math.max(rollWidth, rollLength) * 0.025 + 5;
  workspaceSetView({ x: -margin, y: -margin, width: rollWidth + margin * 2, height: rollLength + margin * 2 });
}

function workspaceFitDesign() {
  if (!cutterWorkspace.metadata) return;
  var b = cutterWorkspace.metadata.bounds;
  var extent = Math.max(b.maxX - b.minX, b.maxY - b.minY, 1);
  var margin = extent * 0.08 + 2;
  workspaceSetView({
    x: b.minX - margin, y: b.minY - margin,
    width: b.maxX - b.minX + margin * 2, height: b.maxY - b.minY + margin * 2
  });
}

function workspaceRenderGrid() {
  if (!cutterWorkspace.view || !cutterWorkspace.metadata) return;
  var view = cutterWorkspace.view;
  var choices = [1, 2, 5, 10, 20, 50, 100, 200];
  var wanted = view.width / 18;
  var step = choices[choices.length - 1];
  choices.some(function(choice) { if (choice >= wanted) { step = choice; return true; } return false; });
  jQuery('#workspaceGrid').attr({ width: step, height: step });
  jQuery('#workspaceGridPath').attr({ d: 'M ' + step + ' 0 L 0 0 0 ' + step, stroke: '#c6d1db', 'stroke-width': 0.25 });
  var ruler = '';
  var rollWidth = cutterWorkspace.metadata.rollWidth;
  var rollLength = cutterWorkspace.metadata.rollLength;
  for (var x = 0; x <= rollWidth; x += step) {
    ruler += '<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + Math.min(step * 0.25, 5) + '" stroke="#647789" stroke-width="0.35"/>';
    ruler += '<text x="' + (x + 1) + '" y="' + Math.min(step * 0.55, 9) + '" fill="#516170">' + x + '</text>';
  }
  for (var y = step; y <= rollLength; y += step) {
    ruler += '<line x1="0" y1="' + y + '" x2="' + Math.min(step * 0.25, 5) + '" y2="' + y + '" stroke="#647789" stroke-width="0.35"/>';
    ruler += '<text x="1" y="' + (y - 1) + '" fill="#516170">' + y + '</text>';
  }
  jQuery('#workspaceRulers').html(ruler);
}

function workspaceRender(resetView) {
  var paths = workspaceTransformedPaths();
  if (!paths.length) return;
  var bounds = workspaceBounds(paths);
  var rollWidth = workspaceNumber('#workspaceRollWidth', 1200);
  var rollLength = Math.max(bounds.maxY + 20, 20);
  var out = bounds.minX < -0.0001 || bounds.minY < -0.0001 || bounds.maxX > rollWidth + 0.0001 || bounds.maxY > 20000;
  cutterWorkspace.metadata = { bounds: bounds, rollWidth: rollWidth, rollLength: rollLength, outOfBounds: out };

  jQuery('#workspaceRoll').attr({ width: rollWidth, height: rollLength });
  var cuts = '', travels = '', markers = '';
  var travelPaths = workspaceTravelPaths(paths);
  cutterWorkspace.renderedPaths = paths;
  cutterWorkspace.renderedTravels = travelPaths;
  paths.forEach(function(path, index) {
    cuts += '<path class="workspace-cut-path" data-sequence="' + index + '" d="' + workspacePathD(path) + '"/>';
    if (travelPaths[index]) travels += '<path class="workspace-travel-path" data-sequence="' + index + '" d="' + workspacePathD(travelPaths[index]) + '"/>';
    if (jQuery('#workspaceOrder').is(':checked')) {
      markers += '<text x="' + (path[0][0] + 1) + '" y="' + (path[0][1] - 1) + '" fill="#7c3aed">' + (index + 1) + '</text>';
    }
  });
  var first = paths[0][0], lastPath = paths[paths.length - 1], last = lastPath[lastPath.length - 1];
  markers += '<circle cx="' + first[0] + '" cy="' + first[1] + '" r="1.8" fill="#16a34a" stroke="#fff" stroke-width="0.4"/>';
  markers += '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="1.8" fill="#111827" stroke="#fff" stroke-width="0.4"/>';
  jQuery('#workspaceCutsLayer').html(cuts).toggleClass('out-of-bounds', out);
  jQuery('#workspaceTravelsLayer').html(travels).toggle(jQuery('#workspaceTravels').is(':checked'));
  jQuery('#workspaceMarkersLayer').html(markers);
  jQuery('#previewInfo').text(
    paths.length + (paths.length === 1 ? ' cut path • ' : ' cut paths • ') +
    (bounds.maxX - bounds.minX).toFixed(1) + ' × ' + (bounds.maxY - bounds.minY).toFixed(1) + ' mm • roll length ' + rollLength.toFixed(1) + ' mm'
  );
  jQuery('#workspaceBoundsError').toggle(out).text(out ? 'The red cut path is outside the loaded roll. Move, rotate, or scale it before generating HPGL.' : '');
  jQuery('#workspaceGenerate').prop('disabled', out || cutterWorkspace.payload.read_only);
  localStorage.setItem('pcutRollWidthMm', rollWidth);
  workspacePrepareSimulation(cutterWorkspace.animationProgress);
  if (resetView || !cutterWorkspace.view) workspaceFitRoll(); else workspaceRenderGrid();
}

function workspacePrepareSimulation(progress) {
  cutterWorkspace.animationElements = [];
  var cuts = jQuery('#workspaceCutsLayer path').toArray();
  var travels = jQuery('#workspaceTravelsLayer path').toArray();
  for (var i = 0; i < cuts.length; i++) {
    var travel = travels.find(function(item) { return parseInt(item.dataset.sequence, 10) === i; });
    if (travel && cutterWorkspace.renderedTravels[i]) {
      cutterWorkspace.animationElements.push({
        element: travel, points: cutterWorkspace.renderedTravels[i], kind: 'travel'
      });
    }
    cutterWorkspace.animationElements.push({
      element: cuts[i], points: cutterWorkspace.renderedPaths[i], kind: 'cut'
    });
  }
  cutterWorkspace.animationElements.forEach(function(item) {
    item.fullD = workspacePathD(item.points);
    item.length = workspacePolylineLength(item.points);
    item.state = null;
  });
  workspaceSimulationProgress(progress == null ? 1 : progress);
}

function workspacePolylineLength(points) {
  var length = 0;
  for (var i = 1; i < points.length; i++) {
    length += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  }
  return length;
}

function workspaceTruncatedPath(points, distance) {
  if (!points.length) return { d: '', point: null };
  var output = [[points[0][0], points[0][1]]];
  var remaining = Math.max(0, distance);
  for (var i = 1; i < points.length; i++) {
    var start = points[i - 1], end = points[i];
    var segment = Math.hypot(end[0] - start[0], end[1] - start[1]);
    if (segment <= 0.000001) continue;
    if (remaining >= segment) {
      output.push([end[0], end[1]]);
      remaining -= segment;
    } else {
      var ratio = remaining / segment;
      output.push([
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio
      ]);
      break;
    }
  }
  return { d: workspacePathD(output), point: output[output.length - 1] };
}

function workspaceSimulationProgress(progress) {
  progress = Math.max(0, Math.min(1, progress));
  cutterWorkspace.animationProgress = progress;
  var total = cutterWorkspace.animationElements.reduce(function(sum, item) { return sum + item.length; }, 0);
  var reveal = total * progress;
  var head = cutterWorkspace.animationElements.length ? cutterWorkspace.animationElements[0].points[0] : null;
  var headKind = 'travel';
  cutterWorkspace.animationElements.forEach(function(item) {
    var element = item.element;
    var length = item.length;
    var shown = Math.max(0, Math.min(length, reveal));
    element.style.strokeDasharray = 'none';
    element.style.strokeDashoffset = '0';
    if (shown <= 0.0001) {
      if (item.state !== 'future') element.style.display = 'none';
      item.state = 'future';
    } else if (shown >= length - 0.0001) {
      if (item.state !== 'complete') {
        element.style.display = '';
        element.setAttribute('d', item.fullD);
      }
      item.state = 'complete';
      head = item.points[item.points.length - 1];
      headKind = item.kind;
    } else {
      // Build only the travelled portion of the active polyline. Unlike SVG
      // dash animation this cannot create several apparent cutter heads.
      var partial = workspaceTruncatedPath(item.points, shown);
      element.style.display = '';
      element.setAttribute('d', partial.d);
      item.state = 'active';
      head = partial.point;
      headKind = item.kind;
    }
    reveal -= shown;
  });
  jQuery('#workspaceMarkersLayer').toggle(progress >= 0.9999);
  if (head && progress < 0.9999) {
    jQuery('#workspaceToolHead').attr({ cx: head[0], cy: head[1], fill: headKind === 'cut' ? '#f59e0b' : '#38bdf8' }).show();
  } else {
    jQuery('#workspaceToolHead').hide();
  }
  jQuery('#workspaceProgress').val(Math.round(progress * 1000));
  jQuery('#workspaceProgressLabel').text(Math.round(progress * 100) + '%');
}

function workspacePlay() {
  if (cutterWorkspace.animationProgress >= 0.999) workspaceSimulationProgress(0);
  cancelAnimationFrame(cutterWorkspace.animationFrame);
  var previous = performance.now();
  function tick(now) {
    var speed = workspaceNumber('#workspaceSpeed', 1);
    workspaceSimulationProgress(cutterWorkspace.animationProgress + (now - previous) * speed / 20000);
    previous = now;
    if (cutterWorkspace.animationProgress < 1) cutterWorkspace.animationFrame = requestAnimationFrame(tick);
  }
  cutterWorkspace.animationFrame = requestAnimationFrame(tick);
}

function workspaceClientPoint(event) {
  var svg = document.getElementById('workspaceSvg');
  var point = svg.createSVGPoint();
  point.x = event.clientX; point.y = event.clientY;
  return point.matrixTransform(svg.getScreenCTM().inverse());
}

function workspaceBindControls() {
  jQuery('.workspace-transform').off('.workspace').on('input.workspace change.workspace', function() {
    if (this.id === 'workspaceWidth' && !cutterWorkspace.syncingDimensions && cutterWorkspace.payload && !cutterWorkspace.payload.read_only) {
      cutterWorkspace.syncingDimensions = true;
      jQuery('#workspaceHeight').val((workspaceNumber('#workspaceWidth', 1) / (cutterWorkspace.payload.width_mm / cutterWorkspace.payload.height_mm)).toFixed(1));
      cutterWorkspace.syncingDimensions = false;
    } else if (this.id === 'workspaceHeight' && !cutterWorkspace.syncingDimensions && cutterWorkspace.payload && !cutterWorkspace.payload.read_only) {
      cutterWorkspace.syncingDimensions = true;
      jQuery('#workspaceWidth').val((workspaceNumber('#workspaceHeight', 1) * (cutterWorkspace.payload.width_mm / cutterWorkspace.payload.height_mm)).toFixed(1));
      cutterWorkspace.syncingDimensions = false;
    }
    workspaceRender(false);
  });
  jQuery('#workspaceTravels, #workspaceOrder').off('.workspace').on('change.workspace', function() { workspaceRender(false); });
  jQuery('#workspaceFitDesign').off('.workspace').on('click.workspace', workspaceFitDesign);
  jQuery('#workspaceFitRoll, #workspaceResetView').off('.workspace').on('click.workspace', workspaceFitRoll);
  jQuery('#workspacePlay').off('.workspace').on('click.workspace', workspacePlay);
  jQuery('#workspacePause').off('.workspace').on('click.workspace', function() { cancelAnimationFrame(cutterWorkspace.animationFrame); });
  jQuery('#workspaceRestart').off('.workspace').on('click.workspace', function() { cancelAnimationFrame(cutterWorkspace.animationFrame); workspaceSimulationProgress(0); });
  jQuery('#workspaceProgress').off('.workspace').on('input.workspace', function() { cancelAnimationFrame(cutterWorkspace.animationFrame); workspaceSimulationProgress(parseInt(this.value, 10) / 1000); });
  jQuery('#workspaceGenerate').off('.workspace').on('click.workspace', workspaceGenerateHpgl);

  var svg = jQuery('#workspaceSvg');
  svg.off('.workspace').on('wheel.workspace', function(event) {
    event.preventDefault();
    var original = event.originalEvent, cursor = workspaceClientPoint(original);
    var factor = original.deltaY > 0 ? 1.18 : 0.85, view = cutterWorkspace.view;
    workspaceSetView({
      x: cursor.x - (cursor.x - view.x) * factor,
      y: cursor.y - (cursor.y - view.y) * factor,
      width: view.width * factor, height: view.height * factor
    });
  }).on('pointerdown.workspace', function(event) {
    if (!cutterWorkspace.view) return;
    var point = workspaceClientPoint(event.originalEvent);
    var design = !cutterWorkspace.payload.read_only && jQuery(event.target).closest('#workspaceCutsLayer').length > 0;
    cutterWorkspace.pointer = {
      mode: design ? 'design' : 'pan', point: point,
      view: Object.assign({}, cutterWorkspace.view),
      offsetX: workspaceNumber('#workspaceOffsetX', 0), offsetY: workspaceNumber('#workspaceOffsetY', 0)
    };
    this.setPointerCapture(event.originalEvent.pointerId);
    svg.addClass('dragging');
  }).on('pointermove.workspace', function(event) {
    if (!cutterWorkspace.pointer) return;
    var point = workspaceClientPoint(event.originalEvent), start = cutterWorkspace.pointer;
    if (start.mode === 'design') {
      jQuery('#workspaceOffsetX').val((start.offsetX + point.x - start.point.x).toFixed(1));
      jQuery('#workspaceOffsetY').val((start.offsetY + point.y - start.point.y).toFixed(1));
      workspaceRender(false);
    } else {
      workspaceSetView({
        x: start.view.x - (point.x - start.point.x), y: start.view.y - (point.y - start.point.y),
        width: start.view.width, height: start.view.height
      });
    }
  }).on('pointerup.workspace pointercancel.workspace', function() { cutterWorkspace.pointer = null; svg.removeClass('dragging'); });
}

function workspaceGenerateHpgl() {
  if (!cutterWorkspace.payload || cutterWorkspace.payload.read_only || cutterWorkspace.metadata.outOfBounds) return;
  var button = jQuery('#workspaceGenerate');
  button.prop('disabled', true).text('Generating…');
  axios.post('/start_conversion', jQuery('#workspaceData').serialize())
    .then(function(response) {
      return updateFiles(response.data.filename).then(function() {
        UIkit.modal('#modal-preview').hide();
        notify(response.data.message + ' and selected it for cutting.', 'success');
      });
    })
    .catch(function(error) {
      var message = error.response && error.response.data && error.response.data.error
        ? error.response.data.error : (error.message || 'Conversion failed.');
      notify(message, 'danger');
    })
    .then(function() { button.prop('disabled', false).text('Generate HPGL'); });
}

// Load either editable SVG geometry or an exact read-only HPGL path workspace.
function previewFile(filename) {
  var requestId = ++previewRequestId;
  cancelAnimationFrame(cutterWorkspace.animationFrame);
  cutterWorkspace.filename = filename; cutterWorkspace.payload = null; cutterWorkspace.view = null;
  jQuery('#previewModalTitle').text('Cut workspace — ' + filename);
  jQuery('#previewError, #cutWorkspace').hide();
  jQuery('#previewSpinner').show();
  UIkit.modal('#modal-preview').show();
  axios.get('/cut_workspace/' + encodeURIComponent(filename), { headers: { 'Cache-Control': 'no-cache' } })
    .then(function(response) {
      if (requestId !== previewRequestId) return;
      var payload = response.data;
      cutterWorkspace.payload = payload;
      cutterWorkspace.animationProgress = 1;
      jQuery('#workspaceFile').val(filename);
      jQuery('#workspaceWidth').val(payload.width_mm.toFixed(1));
      jQuery('#workspaceHeight').val(payload.height_mm.toFixed(1));
      jQuery('#workspaceRollWidth').val(localStorage.getItem('pcutRollWidthMm') || '1200');
      jQuery('#workspaceOffsetX, #workspaceOffsetY').val('0');
      jQuery('#workspaceRotation').val('0');
      jQuery('#workspaceMirrorX, #workspaceMirrorY').prop('checked', false);
      jQuery('#workspaceTravels, #workspaceOrder').prop('checked', false);
      jQuery('.workspace-transform').prop('disabled', payload.read_only);
      jQuery('#workspaceRollWidth').prop('disabled', false);
      jQuery('#workspaceReadOnly').toggle(payload.read_only);
      jQuery('#workspaceGenerate').toggle(!payload.read_only);
      jQuery('#previewWarning').toggle(payload.warnings.length > 0).text(payload.warnings.join(' '));
      workspaceBindControls();
      jQuery('#previewSpinner').hide(); jQuery('#cutWorkspace').show();
      workspaceRender(true);
    })
    .catch(function(error) {
      if (requestId !== previewRequestId) return;
      var message = error.response && error.response.data && error.response.data.error
        ? error.response.data.error : (error.message || 'Preview failed.');
      jQuery('#previewSpinner').hide(); jQuery('#previewError').text(message).show();
    });
}
