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

  var submit = function(confirmedTest) {
    var plotterData = jQuery('#plotterData').serialize();
    if (confirmedTest) plotterData += '&operator_confirm_test=confirmed';
    axios.post('/start_plot', plotterData)
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
  };
  if (
    jQuery('#fileName').val().indexOf('PCP_compensation_test') === 0
    || jQuery('#fileName').val().indexOf('PCP_calibration_100mm') === 0
  ) {
    UIkit.modal.confirm(
      'Physical cutter test: confirm media is loaded and the blade/tool is ready. Commands already buffered by the cutter cannot be recalled.'
    ).then(function() { submit(true); });
  } else {
    submit(false);
  }
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
  renderedPaths: [], renderedTravels: [], pointer: null, syncingDimensions: false,
  serverPreview: null, preparationTimer: null, preparationRequestId: 0,
  manifestItems: [], selectedItemIndex: 0, selectedInstanceId: null,
  profiles: [], selectedProfileId: 'unprofiled',
  calibration: null, calibrationCandidate: null
};

function workspaceNumber(selector, fallback) {
  var value = parseFloat(jQuery(selector).val());
  return Number.isFinite(value) ? value : fallback;
}

function workspaceCutterKey() {
  return {
    serial_port: jQuery('#useCustomPort').is(':checked')
      ? String(jQuery('#customPort').val() || '')
      : String(jQuery('#portList').val() || ''),
    device: String(jQuery('#device').val() || 'creation_1200')
  };
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
  if (cutterWorkspace.serverPreview) {
    return cutterWorkspace.serverPreview.cut_paths.map(function(path) {
      return path.map(function(point) { return [point[0], point[1]]; });
    });
  }
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

function workspaceRequestData() {
  var selected = cutterWorkspace.manifestItems[cutterWorkspace.selectedItemIndex] || {};
  return {
    manifest_version: 1,
    filename: cutterWorkspace.filename,
    material_profile_id: cutterWorkspace.selectedProfileId || 'unprofiled',
    roll_width_mm: workspaceNumber('#workspaceRollWidth', 1200),
    items: cutterWorkspace.manifestItems.map(function(item) {
      return {
        filename: item.filename,
        target_width_mm: item.targetWidth,
        target_height_mm: item.targetHeight,
        rotation: item.rotation || 0,
        mirror_x: !!item.mirrorX,
        mirror_y: !!item.mirrorY,
        copies: item.copies || 1,
        placements: item.placements || []
      };
    }),
    transform: {
      target_width_mm: selected.targetWidth || workspaceNumber('#workspaceWidth', cutterWorkspace.payload.width_mm),
      target_height_mm: selected.targetHeight || workspaceNumber('#workspaceHeight', cutterWorkspace.payload.height_mm),
      roll_width_mm: workspaceNumber('#workspaceRollWidth', 1200),
      offset_x_mm: 0,
      offset_y_mm: 0,
      rotation: selected.rotation || 0,
      mirror_x: !!selected.mirrorX,
      mirror_y: !!selected.mirrorY
    },
    layout: {
      automatic: jQuery('#workspaceAutoLayout').is(':checked'),
      edge_margin_mm: workspaceNumber('#workspaceEdgeMargin', 5),
      spacing_mm: workspaceNumber('#workspaceCopySpacing', 5),
      allow_rotation: jQuery('#workspaceAutoRotate').is(':checked')
    },
    preparation: {
      enabled: jQuery('#workspacePreparationEnabled').is(':checked'),
      remove_duplicates: jQuery('#workspaceRemoveDuplicates').is(':checked'),
      inside_first: jQuery('#workspaceInsideFirst').is(':checked'),
      minimize_travel: jQuery('#workspaceMinimizeTravel').is(':checked'),
      merge_enabled: jQuery('#workspaceMerge').is(':checked'),
      merge_tolerance_mm: workspaceNumber('#workspaceMergeTolerance', 0.05),
      simplify_enabled: jQuery('#workspaceSimplify').is(':checked'),
      simplify_tolerance_mm: workspaceNumber('#workspaceSimplifyTolerance', 0.05)
    },
    cutting_aids: {
      weed_enabled: jQuery('#workspaceWeedEnabled').is(':checked'),
      weed_border_mode: jQuery('#workspaceWeedBorderMode').val() || 'layout',
      weed_margin_mm: workspaceNumber('#workspaceWeedMargin', 5),
      weed_horizontal: jQuery('#workspaceWeedHorizontal').is(':checked'),
      weed_vertical: jQuery('#workspaceWeedVertical').is(':checked'),
      overcut_enabled: jQuery('#workspaceOvercutEnabled').is(':checked'),
      overcut_mm: workspaceNumber('#workspaceOvercut', 1),
      blade_compensation_enabled: jQuery('#workspaceBladeCompensation').is(':checked'),
      blade_offset_mm: workspaceNumber('#workspaceBladeOffset', 0.25)
    },
    calibration: {
      enabled: jQuery('#workspaceCalibrationEnabled').is(':checked'),
      serial_port: workspaceCutterKey().serial_port,
      device: workspaceCutterKey().device
    }
  };
}

function workspaceEscape(value) {
  return jQuery('<div>').text(value == null ? '' : String(value)).html();
}

function workspaceSelectedItem() {
  return cutterWorkspace.manifestItems[cutterWorkspace.selectedItemIndex] || null;
}

function workspaceRenderDesignList() {
  var html = '';
  cutterWorkspace.manifestItems.forEach(function(item, index) {
    html += '<div class="workspace-design-row ' + (index === cutterWorkspace.selectedItemIndex ? 'selected' : '') +
      '" data-item-index="' + index + '">' +
      '<span class="workspace-design-name" title="' + workspaceEscape(item.filename) + '">' + workspaceEscape(item.filename) + '</span>' +
      '<input class="uk-input uk-form-small workspace-copy-count" data-item-index="' + index +
      '" type="number" min="1" max="500" value="' + item.copies + '" aria-label="Copy count">' +
      '<button class="uk-button uk-button-danger uk-button-small workspace-remove-design" data-item-index="' + index +
      '" type="button" title="Remove design"' + (cutterWorkspace.manifestItems.length === 1 ? ' disabled' : '') + '>×</button></div>';
  });
  jQuery('#workspaceDesigns').html(html);
}

function workspacePopulateDesignSelect() {
  var names = [];
  jQuery('.previewFile').each(function() {
    var name = jQuery(this).data('filename');
    if (name && String(name).toLowerCase().endsWith('.svg') && names.indexOf(name) < 0) names.push(name);
  });
  jQuery('#workspaceAddDesignSelect').html(names.map(function(name) {
    return '<option value="' + workspaceEscape(name) + '">' + workspaceEscape(name) + '</option>';
  }).join(''));
}

function workspaceSelectedProfile() {
  return cutterWorkspace.profiles.find(function(profile) {
    return profile.id === cutterWorkspace.selectedProfileId;
  }) || null;
}

function workspaceRenderProfile(profile) {
  if (!profile) return;
  jQuery('#workspaceProfileNotes').val(profile.notes || '');
  jQuery('#workspaceSuggestedPressure').val(profile.suggested_pressure || '');
  jQuery('#workspaceSuggestedSpeed').val(profile.suggested_speed || '');
  jQuery('#workspaceProfileDelete').prop('disabled', !profile.deletable);
  jQuery('#workspaceProfileSave').prop('disabled', !profile.deletable);
  jQuery('#workspaceProfileVerify').prop('disabled', !profile.deletable || profile.verified);
  var checklist = 'Set pressure and speed on the cutter panel only.';
  if (profile.suggested_pressure) checklist += ' Pressure: ' + profile.suggested_pressure + '.';
  if (profile.suggested_speed) checklist += ' Speed: ' + profile.suggested_speed + '.';
  jQuery('#workspaceOperatorChecklist').text(checklist);
  jQuery('#workspaceProfileStatus').text(
    profile.id === 'unprofiled' ? 'Permanent default • no settings are sent to the cutter.'
      : (profile.verified
        ? 'Verified profile • saved geometry settings are applied visibly.'
        : 'Unverified • complete and accept a physical test before automatic application.')
  );
}

function workspaceApplyVerifiedProfile(profile) {
  if (!profile || !profile.verified) return;
  var weed = profile.weed_settings || {};
  jQuery('#workspaceRollWidth').val(Number(profile.roll_width_mm).toFixed(1));
  jQuery('#workspaceEdgeMargin').val(Number(profile.edge_margin_mm).toFixed(1));
  jQuery('#workspaceCopySpacing').val(Number(profile.copy_spacing_mm).toFixed(1));
  jQuery('#workspaceWeedEnabled').prop('checked', !!weed.weed_enabled);
  jQuery('#workspaceWeedBorderMode').val(weed.weed_border_mode || 'layout');
  jQuery('#workspaceWeedMargin').val(Number(weed.weed_margin_mm == null ? 5 : weed.weed_margin_mm));
  jQuery('#workspaceWeedHorizontal').prop('checked', !!weed.weed_horizontal);
  jQuery('#workspaceWeedVertical').prop('checked', !!weed.weed_vertical);
  jQuery('#workspaceBladeOffset').val(Number(profile.blade_offset_mm).toFixed(2));
  jQuery('#workspaceBladeCompensation').prop('checked', !!profile.blade_offset_enabled);
  jQuery('#workspaceOvercut').val(Number(profile.overcut_mm).toFixed(2));
  jQuery('#workspaceOvercutEnabled').prop('checked', !!profile.overcut_enabled);
  workspaceSchedulePreparation(false);
}

function workspaceLoadProfiles(selectedId, applyVerified) {
  return axios.get('/api/material-profiles').then(function(response) {
    cutterWorkspace.profiles = response.data.profiles || [];
    var requested = selectedId || cutterWorkspace.selectedProfileId || 'unprofiled';
    if (!cutterWorkspace.profiles.some(function(profile) { return profile.id === requested; })) {
      requested = 'unprofiled';
    }
    cutterWorkspace.selectedProfileId = requested;
    jQuery('#workspaceMaterialProfile').html(cutterWorkspace.profiles.map(function(profile) {
      return '<option value="' + workspaceEscape(profile.id) + '">' +
        workspaceEscape(profile.name + (profile.verified ? ' ✓' : '')) + '</option>';
    }).join('')).val(requested);
    var profile = workspaceSelectedProfile();
    workspaceRenderProfile(profile);
    if (applyVerified) workspaceApplyVerifiedProfile(profile);
    return profile;
  });
}

function workspaceProfileValues(name) {
  return {
    name: name || (workspaceSelectedProfile() || {}).name || '',
    notes: jQuery('#workspaceProfileNotes').val() || '',
    roll_width_mm: workspaceNumber('#workspaceRollWidth', 1200),
    edge_margin_mm: workspaceNumber('#workspaceEdgeMargin', 5),
    copy_spacing_mm: workspaceNumber('#workspaceCopySpacing', 5),
    suggested_pressure: jQuery('#workspaceSuggestedPressure').val() || '',
    suggested_speed: jQuery('#workspaceSuggestedSpeed').val() || '',
    weed_settings: {
      weed_enabled: jQuery('#workspaceWeedEnabled').is(':checked'),
      weed_border_mode: jQuery('#workspaceWeedBorderMode').val() || 'layout',
      weed_margin_mm: workspaceNumber('#workspaceWeedMargin', 5),
      weed_horizontal: jQuery('#workspaceWeedHorizontal').is(':checked'),
      weed_vertical: jQuery('#workspaceWeedVertical').is(':checked')
    },
    blade_offset_mm: workspaceNumber('#workspaceBladeOffset', 0.25),
    blade_offset_enabled: jQuery('#workspaceBladeCompensation').is(':checked'),
    overcut_mm: workspaceNumber('#workspaceOvercut', 1),
    overcut_enabled: jQuery('#workspaceOvercutEnabled').is(':checked')
  };
}

function workspaceRenderCalibration(calibration) {
  cutterWorkspace.calibration = calibration || null;
  var key = workspaceCutterKey();
  jQuery('#workspaceCalibrationKey').text(
    (key.serial_port || 'No serial port selected') + ' • ' + key.device
  );
  jQuery('#workspaceCalibrationEnabled')
    .prop('checked', !!(calibration && calibration.enabled))
    .prop('disabled', !calibration || !calibration.accepted);
  if (calibration) {
    jQuery('#workspaceMeasuredX').val(Number(calibration.measured_x_mm).toFixed(2));
    jQuery('#workspaceMeasuredY').val(Number(calibration.measured_y_mm).toFixed(2));
    jQuery('#workspaceCalibrationResult').text(
      'Accepted factors: X ' + Number(calibration.factor_x).toFixed(6) +
      ' • Y ' + Number(calibration.factor_y).toFixed(6) +
      (calibration.large_correction ? ' • correction exceeds 2%' : '')
    );
  } else {
    jQuery('#workspaceCalibrationResult').text('No accepted calibration for this cutter.');
  }
}

function workspaceLoadCalibration() {
  var key = workspaceCutterKey();
  cutterWorkspace.calibrationCandidate = null;
  jQuery('#workspaceCalibrationAccept').prop('disabled', true);
  if (!key.serial_port) {
    workspaceRenderCalibration(null);
    return Promise.resolve(null);
  }
  return axios.get('/api/cutter-calibrations', { params: key }).then(function(response) {
    workspaceRenderCalibration(response.data.calibration);
    return response.data.calibration;
  });
}

function workspaceSyncControlsFromItem() {
  var item = workspaceSelectedItem();
  if (!item) return;
  cutterWorkspace.syncingDimensions = true;
  jQuery('#workspaceWidth').val(Number(item.targetWidth).toFixed(1));
  jQuery('#workspaceHeight').val(Number(item.targetHeight).toFixed(1));
  jQuery('#workspaceRotation').val(String(item.rotation || 0));
  jQuery('#workspaceMirrorX').prop('checked', !!item.mirrorX);
  jQuery('#workspaceMirrorY').prop('checked', !!item.mirrorY);
  cutterWorkspace.syncingDimensions = false;
  workspaceSyncPlacementControls();
  workspaceRenderDesignList();
}

function workspaceSelectedPlacement() {
  if (!cutterWorkspace.serverPreview || !cutterWorkspace.serverPreview.instances) return null;
  return cutterWorkspace.serverPreview.instances.find(function(instance) {
    return instance.instance_id === cutterWorkspace.selectedInstanceId;
  }) || null;
}

function workspaceSyncPlacementControls() {
  var placement = workspaceSelectedPlacement();
  var automatic = jQuery('#workspaceAutoLayout').is(':checked');
  jQuery('#workspaceOffsetX, #workspaceOffsetY').prop('disabled', automatic || !placement);
  if (placement) {
    jQuery('#workspaceOffsetX').val(Number(placement.x).toFixed(1));
    jQuery('#workspaceOffsetY').val(Number(placement.y).toFixed(1));
  }
}

function workspaceCaptureManualPlacements() {
  if (!cutterWorkspace.serverPreview || !cutterWorkspace.serverPreview.instances) return;
  cutterWorkspace.manifestItems.forEach(function(item) { item.placements = []; });
  cutterWorkspace.serverPreview.instances.forEach(function(instance) {
    var item = cutterWorkspace.manifestItems[instance.item_index];
    item.placements[instance.copy_index] = {
      x_mm: instance.x, y_mm: instance.y, rotation: instance.rotation
    };
  });
}

function workspaceUpdateSelectedPlacement(x, y) {
  var instance = workspaceSelectedPlacement();
  if (!instance && cutterWorkspace.pointer && cutterWorkspace.pointer.instance) {
    instance = cutterWorkspace.pointer.instance;
  }
  if (!instance) return;
  var item = cutterWorkspace.manifestItems[instance.item_index];
  if (!item.placements[instance.copy_index]) {
    item.placements[instance.copy_index] = { x_mm: instance.x, y_mm: instance.y, rotation: instance.rotation };
  }
  if (x != null) item.placements[instance.copy_index].x_mm = Math.max(0, x);
  if (y != null) item.placements[instance.copy_index].y_mm = Math.max(0, y);
}

function workspaceStatsHtml(preview) {
  if (!preview || !preview.before || !preview.after) return '';
  var before = preview.before, after = preview.after;
  function length(value) { return Number(value || 0).toFixed(1) + ' mm'; }
  return '<strong>Preflight</strong><br>' +
    'Paths: ' + before.path_count + ' → ' + after.path_count + '<br>' +
    'Points: ' + before.point_count + ' → ' + after.point_count + '<br>' +
    'Cut: ' + length(before.cut_length_mm) + ' → ' + length(after.cut_length_mm) + '<br>' +
    'Travel: ' + length(before.travel_length_mm) + ' → ' + length(after.travel_length_mm) + '<br>' +
    'HPGL: ' + before.hpgl_bytes + ' → ' + after.hpgl_bytes + ' bytes';
}

function workspaceApplyServerPreview(preview, resetView) {
  cutterWorkspace.serverPreview = preview;
  if (!cutterWorkspace.selectedInstanceId && preview.instances && preview.instances.length) {
    cutterWorkspace.selectedInstanceId = preview.instances[0].instance_id;
  }
  jQuery('#workspacePreparing').hide();
  jQuery('#workspacePreflightStats').html(workspaceStatsHtml(preview)).show();
  var warnings = (preview.warnings || []).map(function(item) {
    return typeof item === 'string' ? item : item.message;
  });
  jQuery('#previewWarning').toggle(warnings.length > 0).text(warnings.join(' '));
  if (jQuery('#workspaceMerge').is(':checked') || jQuery('#workspaceSimplify').is(':checked')) {
    jQuery('#workspaceShowOriginal').prop('checked', true);
  }
  workspaceSyncPlacementControls();
  workspaceRender(resetView);
}

function workspaceRefreshPrepared(resetView) {
  if (!cutterWorkspace.payload || cutterWorkspace.payload.read_only) return;
  var requestId = ++cutterWorkspace.preparationRequestId;
  jQuery('#workspacePreparing').show();
  jQuery('#workspaceGenerate').prop('disabled', true);
  axios.post('/api/workspace/preview', workspaceRequestData())
    .then(function(response) {
      if (requestId !== cutterWorkspace.preparationRequestId) return;
      workspaceApplyServerPreview(response.data, resetView);
    })
    .catch(function(error) {
      if (requestId !== cutterWorkspace.preparationRequestId) return;
      cutterWorkspace.serverPreview = null;
      jQuery('#workspacePreparing').hide();
      var message = error.response && error.response.data && error.response.data.error
        ? error.response.data.error : (error.message || 'Cut preparation failed.');
      jQuery('#previewWarning').text(message).show();
      jQuery('#workspaceGenerate').prop('disabled', true);
    });
}

function workspaceSchedulePreparation(resetView) {
  clearTimeout(cutterWorkspace.preparationTimer);
  cutterWorkspace.serverPreview = null;
  jQuery('#workspacePreparing').show();
  jQuery('#workspaceGenerate').prop('disabled', true);
  cutterWorkspace.preparationTimer = setTimeout(function() {
    workspaceRefreshPrepared(resetView);
  }, 180);
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
  var collisions = cutterWorkspace.serverPreview
    && (cutterWorkspace.serverPreview.collisions || []).length > 0;
  var serverInvalid = cutterWorkspace.serverPreview && cutterWorkspace.serverPreview.valid === false;
  cutterWorkspace.metadata = { bounds: bounds, rollWidth: rollWidth, rollLength: rollLength, outOfBounds: out };

  jQuery('#workspaceRoll').attr({ width: rollWidth, height: rollLength });
  var cuts = '', travels = '', markers = '', original = '', intendedOverlay = '', instances = '';
  var travelPaths = workspaceTravelPaths(paths);
  var pathRoles = cutterWorkspace.serverPreview
    ? (cutterWorkspace.serverPreview.path_roles || []) : [];
  cutterWorkspace.renderedPaths = paths;
  cutterWorkspace.renderedTravels = travelPaths;
  paths.forEach(function(path, index) {
    var roleClass = pathRoles[index] === 'weed_line' ? ' workspace-weed-line'
      : (pathRoles[index] === 'weed_border' ? ' workspace-weed-border' : '');
    cuts += '<path class="workspace-cut-path' + roleClass + '" data-sequence="' + index + '" d="' + workspacePathD(path) + '"/>';
    if (travelPaths[index]) travels += '<path class="workspace-travel-path" data-sequence="' + index + '" d="' + workspacePathD(travelPaths[index]) + '"/>';
    if (jQuery('#workspaceOrder').is(':checked')) {
      markers += '<text x="' + (path[0][0] + 1) + '" y="' + (path[0][1] - 1) + '" fill="#7c3aed">' + (index + 1) + '</text>';
    }
  });
  if (cutterWorkspace.serverPreview && jQuery('#workspaceShowOriginal').is(':checked')) {
    (cutterWorkspace.serverPreview.original_paths || cutterWorkspace.serverPreview.intended_paths || []).forEach(function(path) {
      original += '<path d="' + workspacePathD(path) + '"/>';
    });
  }
  if (
    cutterWorkspace.serverPreview
    && cutterWorkspace.serverPreview.compensated_paths
    && cutterWorkspace.serverPreview.compensated_paths.length
    && jQuery('#workspaceShowIntended').is(':checked')
  ) {
    (cutterWorkspace.serverPreview.intended_paths || []).forEach(function(path) {
      intendedOverlay += '<path d="' + workspacePathD(path) + '"/>';
    });
  }
  if (cutterWorkspace.serverPreview && cutterWorkspace.serverPreview.instances) {
    var collisionIds = cutterWorkspace.serverPreview.collisions || [];
    cutterWorkspace.serverPreview.instances.forEach(function(instance) {
      var classes = [];
      if (instance.instance_id === cutterWorkspace.selectedInstanceId) classes.push('selected');
      if (collisionIds.indexOf(instance.instance_id) >= 0) classes.push('collision');
      instances += '<rect class="' + classes.join(' ') + '" data-instance-id="' + instance.instance_id +
        '" x="' + instance.x + '" y="' + instance.y + '" width="' + instance.width +
        '" height="' + instance.height + '"/>';
    });
  }
  var first = paths[0][0], lastPath = paths[paths.length - 1], last = lastPath[lastPath.length - 1];
  markers += '<circle cx="' + first[0] + '" cy="' + first[1] + '" r="1.8" fill="#16a34a" stroke="#fff" stroke-width="0.4"/>';
  markers += '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="1.8" fill="#111827" stroke="#fff" stroke-width="0.4"/>';
  jQuery('#workspaceCutsLayer').html(cuts).toggleClass('out-of-bounds', out);
  jQuery('#workspaceOriginalLayer').html(original);
  jQuery('#workspaceIntendedLayer').html(intendedOverlay);
  jQuery('#workspaceInstancesLayer').html(instances);
  jQuery('#workspaceTravelsLayer').html(travels).toggle(jQuery('#workspaceTravels').is(':checked'));
  jQuery('#workspaceMarkersLayer').html(markers);
  var copiesLabel = cutterWorkspace.serverPreview && cutterWorkspace.serverPreview.instances
    ? ' • ' + cutterWorkspace.serverPreview.instances.length + ' arranged cop' +
      (cutterWorkspace.serverPreview.instances.length === 1 ? 'y' : 'ies')
    : '';
  jQuery('#previewInfo').text(
    paths.length + (paths.length === 1 ? ' cut path • ' : ' cut paths • ') +
    (bounds.maxX - bounds.minX).toFixed(1) + ' × ' + (bounds.maxY - bounds.minY).toFixed(1) +
    ' mm • roll length ' + rollLength.toFixed(1) + ' mm' + copiesLabel
  );
  var layoutError = '';
  if (out) layoutError = 'The red cut path is outside the loaded roll. Move, rotate, or scale it before generating HPGL.';
  else if (collisions) layoutError = 'Highlighted copies overlap. Move them apart or regenerate the automatic layout before generating HPGL.';
  else if (serverInvalid) layoutError = 'The prepared workspace is not valid for HPGL generation.';
  jQuery('#workspaceBoundsError').toggle(!!layoutError).text(layoutError);
  jQuery('#workspaceGenerate').prop(
    'disabled', out || serverInvalid || cutterWorkspace.payload.read_only
    || (!cutterWorkspace.payload.read_only && !cutterWorkspace.serverPreview)
  );
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
  jQuery('#workspaceMaterialProfile').off('.workspace').on('change.workspace', function() {
    cutterWorkspace.selectedProfileId = jQuery(this).val() || 'unprofiled';
    var profile = workspaceSelectedProfile();
    workspaceRenderProfile(profile);
    workspaceApplyVerifiedProfile(profile);
  });
  jQuery('#workspaceProfileNew').off('.workspace').on('click.workspace', function() {
    UIkit.modal.prompt('New material profile name', '').then(function(name) {
      if (!name) return;
      axios.post('/api/material-profiles', workspaceProfileValues(name))
        .then(function(response) {
          cutterWorkspace.selectedProfileId = response.data.id;
          return workspaceLoadProfiles(response.data.id, false);
        })
        .then(function() { notify('Material profile created with compensation disabled.', 'success'); })
        .catch(function(error) {
          notify(error.response && error.response.data ? error.response.data.error : error.message, 'danger');
        });
    });
  });
  jQuery('#workspaceProfileSave').off('.workspace').on('click.workspace', function() {
    var profile = workspaceSelectedProfile();
    if (!profile) return;
    axios.put('/api/material-profiles/' + encodeURIComponent(profile.id), workspaceProfileValues())
      .then(function(response) {
        return workspaceLoadProfiles(response.data.id, false);
      })
      .then(function() { notify('Material profile saved.', 'success'); })
      .catch(function(error) {
        notify(error.response && error.response.data ? error.response.data.error : error.message, 'danger');
      });
  });
  jQuery('#workspaceProfileVerify').off('.workspace').on('click.workspace', function() {
    var profile = workspaceSelectedProfile();
    if (!profile || !profile.deletable) return;
    UIkit.modal.confirm(
      'Confirm that a physical test cut using these settings has been completed and accepted.'
    ).then(function() {
      return axios.post('/api/material-profiles/' + encodeURIComponent(profile.id) + '/verify', {
        test_cut_accepted: true
      });
    }).then(function(response) {
      return workspaceLoadProfiles(response.data.id, true);
    }).then(function() {
      notify('Profile verified; its saved preparation settings are now applied.', 'success');
    }).catch(function(error) {
      if (error && error.response) {
        notify(error.response.data.error || error.message, 'danger');
      }
    });
  });
  jQuery('#workspaceProfileDelete').off('.workspace').on('click.workspace', function() {
    var profile = workspaceSelectedProfile();
    if (!profile || !profile.deletable) return;
    UIkit.modal.confirm('Delete material profile “' + profile.name + '”?').then(function() {
      return axios.delete('/api/material-profiles/' + encodeURIComponent(profile.id));
    }).then(function() {
      cutterWorkspace.selectedProfileId = 'unprofiled';
      return workspaceLoadProfiles('unprofiled', false);
    }).then(function() { notify('Material profile deleted.', 'warning'); });
  });
  jQuery('#workspaceProfileExport').off('.workspace').on('click.workspace', function() {
    window.location.assign('/api/material-profiles/export');
  });
  jQuery('#workspaceProfileImport').off('.workspace').on('click.workspace', function() {
    jQuery('#workspaceProfileImportFile').val('').trigger('click');
  });
  jQuery('#workspaceProfileImportFile').off('.workspace').on('change.workspace', function() {
    var file = this.files && this.files[0];
    if (!file) return;
    file.text().then(JSON.parse).then(function(document) {
      return axios.post('/api/material-profiles/import', document);
    }).then(function(response) {
      notify('Imported ' + response.data.imported.length + ' material profile(s).', 'success');
      return workspaceLoadProfiles(null, false);
    }).catch(function(error) {
      notify(error.response && error.response.data ? error.response.data.error : error.message, 'danger');
    });
  });
  jQuery('#workspaceCalibrationPattern').off('.workspace').on('click.workspace', function() {
    axios.post('/api/cutter-calibrations/pattern').then(function(response) {
      return updateFiles(response.data.filename).then(function() {
        notify(response.data.message, 'primary');
        previewFile(response.data.filename);
      });
    }).catch(function(error) {
      notify(error.response && error.response.data ? error.response.data.error : error.message, 'danger');
    });
  });
  jQuery('#workspaceCalibrationCalculate').off('.workspace').on('click.workspace', function() {
    var key = workspaceCutterKey();
    axios.post('/api/cutter-calibrations', {
      serial_port: key.serial_port,
      device: key.device,
      measured_x_mm: workspaceNumber('#workspaceMeasuredX', 0),
      measured_y_mm: workspaceNumber('#workspaceMeasuredY', 0),
      accept: false
    }).then(function(response) {
      cutterWorkspace.calibrationCandidate = response.data;
      jQuery('#workspaceCalibrationResult').text(
        'Proposed factors: X ' + Number(response.data.factor_x).toFixed(6) +
        ' • Y ' + Number(response.data.factor_y).toFixed(6) +
        (response.data.large_correction ? ' • WARNING: correction exceeds 2%' : '')
      );
      jQuery('#workspaceCalibrationAccept').prop('disabled', false);
    }).catch(function(error) {
      notify(error.response && error.response.data ? error.response.data.error : error.message, 'danger');
    });
  });
  jQuery('#workspaceCalibrationAccept').off('.workspace').on('click.workspace', function() {
    var candidate = cutterWorkspace.calibrationCandidate;
    if (!candidate) return;
    var key = workspaceCutterKey();
    var accept = function(confirmLarge) {
      return axios.post('/api/cutter-calibrations', {
        serial_port: key.serial_port,
        device: key.device,
        measured_x_mm: candidate.measured_x_mm,
        measured_y_mm: candidate.measured_y_mm,
        accept: true,
        enabled: true,
        confirm_large_correction: confirmLarge
      });
    };
    var promise = candidate.large_correction
      ? UIkit.modal.confirm('This correction exceeds 2%. Confirm the measurements are accurate.').then(function() { return accept(true); })
      : accept(false);
    promise.then(function(response) {
      workspaceRenderCalibration(response.data);
      cutterWorkspace.calibrationCandidate = null;
      jQuery('#workspaceCalibrationAccept').prop('disabled', true);
      workspaceSchedulePreparation(false);
      notify('Cutter calibration accepted and enabled.', 'success');
    }).catch(function(error) {
      if (error && error.response) notify(error.response.data.error || error.message, 'danger');
    });
  });
  jQuery('#workspaceCalibrationEnabled').off('.workspace').on('change.workspace', function() {
    var key = workspaceCutterKey();
    var enabled = jQuery(this).is(':checked');
    axios.put('/api/cutter-calibrations', {
      serial_port: key.serial_port, device: key.device, enabled: enabled
    }).then(function(response) {
      workspaceRenderCalibration(response.data);
      workspaceSchedulePreparation(false);
    }).catch(function(error) {
      notify(error.response && error.response.data ? error.response.data.error : error.message, 'danger');
      workspaceRenderCalibration(cutterWorkspace.calibration);
    });
  });
  jQuery('.workspace-transform').off('.workspace').on('input.workspace change.workspace', function() {
    var item = workspaceSelectedItem();
    if (!item || cutterWorkspace.syncingDimensions) return;
    if (this.id === 'workspaceWidth') {
      cutterWorkspace.syncingDimensions = true;
      item.targetWidth = workspaceNumber('#workspaceWidth', item.targetWidth);
      item.targetHeight = item.targetWidth / (item.naturalWidth / item.naturalHeight);
      jQuery('#workspaceHeight').val(item.targetHeight.toFixed(1));
      cutterWorkspace.syncingDimensions = false;
    } else if (this.id === 'workspaceHeight') {
      cutterWorkspace.syncingDimensions = true;
      item.targetHeight = workspaceNumber('#workspaceHeight', item.targetHeight);
      item.targetWidth = item.targetHeight * (item.naturalWidth / item.naturalHeight);
      jQuery('#workspaceWidth').val(item.targetWidth.toFixed(1));
      cutterWorkspace.syncingDimensions = false;
    } else if (this.id === 'workspaceRotation') {
      item.rotation = parseInt(jQuery('#workspaceRotation').val(), 10) || 0;
    } else if (this.id === 'workspaceMirrorX') {
      item.mirrorX = jQuery('#workspaceMirrorX').is(':checked');
    } else if (this.id === 'workspaceMirrorY') {
      item.mirrorY = jQuery('#workspaceMirrorY').is(':checked');
    } else if (this.id === 'workspaceOffsetX' || this.id === 'workspaceOffsetY') {
      if (!jQuery('#workspaceAutoLayout').is(':checked')) {
        workspaceUpdateSelectedPlacement(
          workspaceNumber('#workspaceOffsetX', 0), workspaceNumber('#workspaceOffsetY', 0)
        );
      }
    }
    workspaceRender(false);
    workspaceSchedulePreparation(false);
  });
  jQuery('.workspace-layout').off('.workspace').on('input.workspace change.workspace', function() {
    if (this.id === 'workspaceAutoLayout' && !jQuery(this).is(':checked')) {
      workspaceCaptureManualPlacements();
    }
    workspaceSyncPlacementControls();
    workspaceSchedulePreparation(false);
  });
  jQuery('#workspaceDesigns').off('.workspace')
    .on('click.workspace', '.workspace-design-row', function(event) {
      if (jQuery(event.target).is('input,button')) return;
      cutterWorkspace.selectedItemIndex = parseInt(jQuery(this).data('item-index'), 10);
      var first = cutterWorkspace.serverPreview && cutterWorkspace.serverPreview.instances.find(function(instance) {
        return instance.item_index === cutterWorkspace.selectedItemIndex;
      });
      cutterWorkspace.selectedInstanceId = first ? first.instance_id : null;
      workspaceSyncControlsFromItem();
      workspaceRender(false);
    })
    .on('change.workspace', '.workspace-copy-count', function() {
      var index = parseInt(jQuery(this).data('item-index'), 10);
      cutterWorkspace.manifestItems[index].copies = Math.max(1, Math.min(500, parseInt(this.value, 10) || 1));
      cutterWorkspace.manifestItems[index].placements = [];
      jQuery('#workspaceAutoLayout').prop('checked', true);
      workspaceRenderDesignList();
      workspaceSchedulePreparation(false);
    })
    .on('click.workspace', '.workspace-remove-design', function(event) {
      event.stopPropagation();
      if (cutterWorkspace.manifestItems.length === 1) return;
      var index = parseInt(jQuery(this).data('item-index'), 10);
      cutterWorkspace.manifestItems.splice(index, 1);
      cutterWorkspace.selectedItemIndex = Math.max(0, Math.min(cutterWorkspace.selectedItemIndex, cutterWorkspace.manifestItems.length - 1));
      cutterWorkspace.selectedInstanceId = null;
      workspaceSyncControlsFromItem();
      workspaceSchedulePreparation(false);
    });
  jQuery('#workspaceAddDesign').off('.workspace').on('click.workspace', function() {
    var filename = jQuery('#workspaceAddDesignSelect').val();
    if (!filename) return;
    axios.get('/cut_workspace/' + encodeURIComponent(filename), { headers: { 'Cache-Control': 'no-cache' } })
      .then(function(response) {
        var payload = response.data;
        cutterWorkspace.manifestItems.push({
          filename: filename,
          naturalWidth: payload.width_mm, naturalHeight: payload.height_mm,
          targetWidth: payload.width_mm, targetHeight: payload.height_mm,
          rotation: 0, mirrorX: false, mirrorY: false, copies: 1, placements: []
        });
        cutterWorkspace.selectedItemIndex = cutterWorkspace.manifestItems.length - 1;
        cutterWorkspace.selectedInstanceId = null;
        jQuery('#workspaceAutoLayout').prop('checked', true);
        workspaceSyncControlsFromItem();
        workspaceSchedulePreparation(false);
      });
  });
  jQuery('.workspace-preparation').off('.workspace').on('input.workspace change.workspace', function() {
    workspaceRender(false);
    workspaceSchedulePreparation(false);
  });
  jQuery('.workspace-cutting-aid').off('.workspace').on('input.workspace change.workspace', function() {
    if (this.id === 'workspaceBladeCompensation' && jQuery(this).is(':checked')) {
      jQuery('#workspaceShowIntended').prop('checked', true);
    }
    workspaceRender(false);
    workspaceSchedulePreparation(false);
  });
  jQuery('#workspaceTravels, #workspaceOrder, #workspaceShowOriginal, #workspaceShowIntended')
    .off('.workspace').on('change.workspace', function() { workspaceRender(false); });
  jQuery('#workspaceTestPattern').off('.workspace').on('click.workspace', function() {
    var button = jQuery(this).prop('disabled', true).text('Creating…');
    axios.post('/api/workspace/test-pattern')
      .then(function(response) {
        return updateFiles(response.data.filename).then(function() {
          notify(response.data.message, 'primary');
          previewFile(response.data.filename);
        });
      })
      .catch(function(error) {
        var message = error.response && error.response.data && error.response.data.error
          ? error.response.data.error : (error.message || 'Could not create the test pattern.');
        notify(message, 'danger');
      })
      .then(function() { button.prop('disabled', false).text('Create compensation test pattern'); });
  });
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
    var instanceId = jQuery(event.target).data('instance-id');
    var design = !cutterWorkspace.payload.read_only && !!instanceId;
    if (design) {
      cutterWorkspace.selectedInstanceId = instanceId;
      var selectedInstance = workspaceSelectedPlacement();
      if (selectedInstance) cutterWorkspace.selectedItemIndex = selectedInstance.item_index;
      if (jQuery('#workspaceAutoLayout').is(':checked')) {
        workspaceCaptureManualPlacements();
        jQuery('#workspaceAutoLayout').prop('checked', false);
      }
      workspaceSyncControlsFromItem();
    }
    cutterWorkspace.pointer = {
      mode: design ? 'design' : 'pan', point: point,
      view: Object.assign({}, cutterWorkspace.view),
      offsetX: workspaceNumber('#workspaceOffsetX', 0), offsetY: workspaceNumber('#workspaceOffsetY', 0),
      instance: design ? workspaceSelectedPlacement() : null
    };
    this.setPointerCapture(event.originalEvent.pointerId);
    svg.addClass('dragging');
  }).on('pointermove.workspace', function(event) {
    if (!cutterWorkspace.pointer) return;
    var point = workspaceClientPoint(event.originalEvent), start = cutterWorkspace.pointer;
    if (start.mode === 'design') {
      var nextX = start.offsetX + point.x - start.point.x;
      var nextY = start.offsetY + point.y - start.point.y;
      jQuery('#workspaceOffsetX').val(nextX.toFixed(1));
      jQuery('#workspaceOffsetY').val(nextY.toFixed(1));
      workspaceUpdateSelectedPlacement(nextX, nextY);
      cutterWorkspace.serverPreview = null;
    } else {
      workspaceSetView({
        x: start.view.x - (point.x - start.point.x), y: start.view.y - (point.y - start.point.y),
        width: start.view.width, height: start.view.height
      });
    }
  }).on('pointerup.workspace pointercancel.workspace', function() {
    var movedDesign = cutterWorkspace.pointer && cutterWorkspace.pointer.mode === 'design';
    cutterWorkspace.pointer = null; svg.removeClass('dragging');
    if (movedDesign) workspaceSchedulePreparation(false);
  });
}

function workspaceGenerateHpgl() {
  if (!cutterWorkspace.payload || cutterWorkspace.payload.read_only
      || cutterWorkspace.metadata.outOfBounds || !cutterWorkspace.serverPreview
      || cutterWorkspace.serverPreview.valid === false) return;
  var button = jQuery('#workspaceGenerate');
  button.prop('disabled', true).text('Generating…');
  var requestData = workspaceRequestData();
  requestData.geometry_hash = cutterWorkspace.serverPreview.geometry_hash;
  axios.post('/api/workspace/generate', requestData)
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
  cutterWorkspace.serverPreview = null;
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
      jQuery('#workspacePreparationEnabled, #workspaceRemoveDuplicates, #workspaceInsideFirst, #workspaceMinimizeTravel').prop('checked', true);
      jQuery('#workspaceMerge, #workspaceSimplify, #workspaceShowOriginal').prop('checked', false);
      jQuery('#workspaceMergeTolerance, #workspaceSimplifyTolerance').val('0.05');
      jQuery('#workspaceWeedEnabled, #workspaceWeedHorizontal, #workspaceWeedVertical, #workspaceOvercutEnabled, #workspaceBladeCompensation').prop('checked', false);
      jQuery('#workspaceShowIntended').prop('checked', true);
      jQuery('#workspaceWeedBorderMode').val('layout');
      jQuery('#workspaceWeedMargin').val('5');
      jQuery('#workspaceOvercut').val('1');
      jQuery('#workspaceBladeOffset').val('0.25');
      jQuery('#workspaceAutoLayout').prop('checked', true);
      jQuery('#workspaceAutoRotate').prop('checked', false);
      jQuery('#workspaceEdgeMargin, #workspaceCopySpacing').val('5');
      cutterWorkspace.manifestItems = [{
        filename: filename,
        naturalWidth: payload.width_mm, naturalHeight: payload.height_mm,
        targetWidth: payload.width_mm, targetHeight: payload.height_mm,
        rotation: 0, mirrorX: false, mirrorY: false, copies: 1, placements: []
      }];
      cutterWorkspace.selectedItemIndex = 0;
      cutterWorkspace.selectedInstanceId = null;
      cutterWorkspace.selectedProfileId = 'unprofiled';
      cutterWorkspace.calibration = null;
      cutterWorkspace.calibrationCandidate = null;
      workspaceRenderDesignList();
      workspacePopulateDesignSelect();
      jQuery('#workspaceDesigns, #workspaceAddDesignSelect, #workspaceAddDesign, #workspaceLayout')
        .toggle(!payload.read_only);
      jQuery('.workspace-transform').prop('disabled', payload.read_only);
      jQuery('.workspace-preparation').prop('disabled', payload.read_only);
      jQuery('.workspace-cutting-aid').prop('disabled', payload.read_only);
      jQuery('#workspacePreparation').toggle(!payload.read_only);
      jQuery('#workspaceCuttingAids').toggle(!payload.read_only);
      jQuery('#workspaceMaterialProfilePanel, #workspaceCalibrationPanel').toggle(!payload.read_only);
      jQuery('#workspaceRollWidth').prop('disabled', false);
      jQuery('#workspaceReadOnly').toggle(payload.read_only);
      jQuery('#workspaceGenerate').toggle(!payload.read_only);
      jQuery('#previewWarning').toggle(payload.warnings.length > 0).text(payload.warnings.join(' '));
      workspaceBindControls();
      jQuery('#previewSpinner').hide(); jQuery('#cutWorkspace').show();
      workspaceRender(true);
      if (!payload.read_only) {
        Promise.all([
          workspaceLoadProfiles('unprofiled', false),
          workspaceLoadCalibration()
        ]).then(function() {
          workspaceRefreshPrepared(true);
        }).catch(function(error) {
          var message = error.response && error.response.data && error.response.data.error
            ? error.response.data.error : (error.message || 'Could not load production settings.');
          jQuery('#previewWarning').text(message).show();
        });
      }
    })
    .catch(function(error) {
      if (requestId !== previewRequestId) return;
      var message = error.response && error.response.data && error.response.data.error
        ? error.response.data.error : (error.message || 'Preview failed.');
      jQuery('#previewSpinner').hide(); jQuery('#previewError').text(message).show();
    });
}
