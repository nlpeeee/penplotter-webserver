(function () {
  'use strict';

  var UI_PREFS_KEY = 'pcp.ui.v2';
  var currentView = document.body.dataset.initialView || 'new-cut';
  var uiState = null;
  var generatedOutput = null;
  var uploadBatch = [];
  var historyState = { entries: [], index: -1, restoring: false };

  Dropzone.autoDiscover = false;

  function escapeHtml(value) {
    return jQuery('<div>').text(value == null ? '' : String(value)).html();
  }

  function readPrefs() {
    try {
      return JSON.parse(localStorage.getItem(UI_PREFS_KEY) || '{}');
    } catch (_error) {
      return {};
    }
  }

  function writePrefs(values) {
    var next = Object.assign(readPrefs(), values || {});
    localStorage.setItem(UI_PREFS_KEY, JSON.stringify(next));
  }

  function announce(message) {
    jQuery('#v2LiveRegion').text('').text(message || '');
  }

  function apiError(error, fallback) {
    if (error && error.response && error.response.data) {
      return error.response.data.error || error.response.data.message || String(error.response.data);
    }
    return (error && error.message) || fallback || 'The request could not be completed.';
  }

  function showView(view) {
    currentView = view || 'new-cut';
    jQuery('.v2-view').removeClass('active');
    jQuery('.v2-view[data-view="' + currentView + '"]').addClass('active');
    jQuery('[data-view-link]').removeClass('active').filter('[data-view-link="' + currentView + '"]').addClass('active');
  }

  function stateLabel(state) {
    return {
      available: 'Port available',
      missing: 'Port unavailable',
      busy: 'Cut in progress',
      resetting: 'Resetting connection',
      error: 'Connection error',
      unknown: 'Port not configured'
    }[state] || 'Status unknown';
  }

  function renderUiState(state) {
    uiState = state || {};
    var plotter = uiState.plotter || {};
    var portState = plotter.port_state || 'unknown';
    jQuery('#v2PortState').attr('data-state', portState).text(stateLabel(portState));
    jQuery('#v2CutterName').text(plotter.name || 'Plotter');
    jQuery('#v2CutterPort').text(plotter.configured_port || 'Not configured');
    jQuery('#v2SerialOperation').text(
      plotter.serial_operation === 'cut' ? 'Transmitting'
        : (plotter.serial_operation === 'reset' ? 'Resetting' : 'Idle')
    );
    jQuery('#v2QueueCount').text(
      uiState.queue_count ? uiState.queue_count + ' queued job' + (uiState.queue_count === 1 ? '' : 's') : 'No queued jobs'
    );

    var active = uiState.active_job;
    jQuery('#v2GlobalCancel').prop('hidden', !active);
    if (active) {
      var filename = active.display_file || String(active.file || '').split('/').pop();
      var progress = Number(active.progress || 0);
      jQuery('#v2ActiveJobCard').prop('hidden', false).html(
        '<div class="v2-panel-heading"><div><span class="v2-step-number">!</span>' +
        '<div><h2>Active cut</h2><p class="v2-mono">' + escapeHtml(filename) + '</p></div></div>' +
        '<strong>' + progress.toFixed(1) + '%</strong></div>' +
        '<progress class="uk-progress" max="100" value="' + progress + '"></progress>' +
        '<button class="v2-button v2-button-danger v2-button-large stopPlot" type="button">CANCEL CUT</button>'
      );
    } else {
      jQuery('#v2ActiveJobCard').prop('hidden', true).empty();
    }
    updateReadiness();
  }

  function refreshUiState() {
    return axios.get('/api/ui-state').then(function (response) {
      renderUiState(response.data);
      return response.data;
    }).catch(function (error) {
      jQuery('#v2PortState').attr('data-state', 'error').text('Status unavailable');
      console.error(error);
      return null;
    });
  }

  function renderV2File(name) {
    var ext = String(name).split('.').pop().toLowerCase();
    var editable = ext === 'svg';
    return '<span class="v2-file-kind">' + escapeHtml(ext.toUpperCase()) + '</span>' +
      '<span class="v2-file-name" title="' + escapeHtml(name) + '">' + escapeHtml(name) + '</span>' +
      '<span class="v2-file-actions">' +
      '<button class="v2-button v2-button-primary previewFile" type="button" data-filename="' + escapeHtml(name) + '">' +
      (editable ? 'Open workspace' : 'Exact preview') + '</button>' +
      '<button class="v2-button v2-button-quiet deleteFile lock-edit" type="button" data-filename="' + escapeHtml(name) + '">Delete</button>' +
      '</span>';
  }

  window.renderFileListElement = renderV2File;

  function refreshFiles(preferredFilename) {
    return updateFiles(preferredFilename).then(function (response) {
      var hasFiles = !!(response && response.data && response.data.content && response.data.content.length);
      jQuery('#v2FileEmpty').toggle(!hasFiles);
      filterFiles();
      return response;
    });
  }

  function filterFiles() {
    var query = String(jQuery('#v2FileSearch').val() || '').trim().toLowerCase();
    jQuery('#fileList li').each(function () {
      jQuery(this).toggle(!query || jQuery(this).text().toLowerCase().indexOf(query) >= 0);
    });
  }

  function navigateWorkbench(filename) {
    history.pushState({ view: 'workbench', file: filename }, '', '/v2/workbench?file=' + encodeURIComponent(filename));
    showView('workbench');
    previewFile(filename);
  }

  function importSvgBatch(names) {
    if (!names.length) return;
    navigateWorkbench(names[0]);
    if (names.length === 1) return;
    var attempts = 0;
    var timer = window.setInterval(function () {
      attempts += 1;
      if (cutterWorkspace && cutterWorkspace.payload && cutterWorkspace.serverPreview) {
        window.clearInterval(timer);
        var requests = names.slice(1).map(function (filename) {
          return axios.get('/cut_workspace/' + encodeURIComponent(filename), {
            headers: { 'Cache-Control': 'no-cache' }
          }).then(function (response) {
            var payload = response.data;
            cutterWorkspace.manifestItems.push({
              filename: filename,
              naturalWidth: payload.width_mm,
              naturalHeight: payload.height_mm,
              targetWidth: payload.width_mm,
              targetHeight: payload.height_mm,
              rotation: 0,
              mirrorX: false,
              mirrorY: false,
              copies: 1,
              placements: []
            });
          });
        });
        Promise.all(requests).then(function () {
          cutterWorkspace.selectedItemIndex = 0;
          cutterWorkspace.selectedInstanceId = null;
          jQuery('#workspaceAutoLayout').prop('checked', true);
          workspaceRenderDesignList();
          workspaceSyncControlsFromItem();
          workspaceSchedulePreparation(true);
          captureWorkspaceState(true);
          notify(names.length + ' SVG files added to one layout.', 'success');
        }).catch(function (error) {
          notify(apiError(error, 'One or more SVG files could not be added.'), 'danger');
        });
      } else if (attempts > 100) {
        window.clearInterval(timer);
        notify('The first design did not finish loading; open the remaining files from Design.', 'warning');
      }
    }, 100);
  }

  function renderImportTray(names) {
    var svgNames = names.filter(function (name) { return /\.svg$/i.test(name); });
    var hpglNames = names.filter(function (name) { return /\.hpgl$/i.test(name); });
    if (names.length === 1) {
      navigateWorkbench(names[0]);
      return;
    }
    var html = '<strong>' + names.length + ' files uploaded</strong>';
    if (svgNames.length) {
      html += '<p>' + svgNames.length + ' SVG file' + (svgNames.length === 1 ? '' : 's') +
        ' can be arranged together.</p><button id="v2OpenSvgBatch" class="v2-button v2-button-primary" type="button">Create SVG layout</button>';
    }
    if (hpglNames.length) {
      html += '<p>' + hpglNames.length + ' HPGL file' + (hpglNames.length === 1 ? '' : 's') +
        ' remain separate exact cutter files.</p>';
    }
    jQuery('#v2ImportTray').prop('hidden', false).html(html);
    jQuery('#v2OpenSvgBatch').on('click', function () { importSvgBatch(svgNames); });
  }

  function initUpload() {
    if (!jQuery('#uploadFiles').length) return;
    var uploader = new Dropzone('#uploadFiles');
    uploader.on('success', function (file) {
      if (file && file.name) uploadBatch.push(file.name);
    });
    uploader.on('error', function (_file, message) {
      notify(typeof message === 'string' ? message : 'Upload failed.', 'danger');
    });
    uploader.on('queuecomplete', function () {
      var names = uploadBatch.slice();
      uploadBatch = [];
      refreshFiles().then(function () {
        if (names.length) renderImportTray(names);
      });
    });
  }

  function setWorkspacePhase(phase, openInspector) {
    phase = phase || 'design';
    jQuery('[data-workspace-phase]').removeClass('active').filter('[data-workspace-phase="' + phase + '"]').addClass('active');
    jQuery('[data-workspace-panel]').removeClass('active').filter('[data-workspace-panel="' + phase + '"]').addClass('active');
    if (openInspector !== false) jQuery('#cutWorkspace').addClass('inspector-open');
    writePrefs({ workspacePhase: phase });
  }

  function updateLayoutMetrics() {
    if (!window.cutterWorkspace || !cutterWorkspace.metadata) return;
    var bounds = cutterWorkspace.metadata.bounds;
    var rollLength = cutterWorkspace.metadata.rollLength || 0;
    var rollWidth = cutterWorkspace.metadata.rollWidth || 0;
    var area = Math.max(0, rollWidth * rollLength / 1000000);
    jQuery('#v2LayoutMetrics').html(
      '<div><strong>' + rollLength.toFixed(1) + ' mm</strong><span>Total feed length</span></div>' +
      '<div><strong>' + area.toFixed(3) + ' m²</strong><span>Loaded material area</span></div>' +
      '<div><strong>' + Math.max(0, bounds.maxX - bounds.minX).toFixed(1) + ' mm</strong><span>Artwork width</span></div>' +
      '<div><strong>' + Math.max(0, bounds.maxY - bounds.minY).toFixed(1) + ' mm</strong><span>Artwork length</span></div>'
    );
  }

  function readinessItem(state, text) {
    var icon = state === 'ready' ? '✓' : (state === 'warning' ? '!' : '×');
    return '<div class="' + state + '"><strong>' + icon + '</strong><span>' + escapeHtml(text) + '</span></div>';
  }

  function updateReadiness() {
    if (!jQuery('#v2Readiness').length || !window.cutterWorkspace) return;
    var preview = cutterWorkspace.serverPreview;
    var metadata = cutterWorkspace.metadata;
    var readOnly = !!(cutterWorkspace.payload && cutterWorkspace.payload.read_only);
    var exactReady = readOnly ? !!cutterWorkspace.payload : !!(preview && preview.valid !== false);
    var collisions = preview ? (preview.collisions || []) : [];
    var currentHash = preview && preview.geometry_hash;
    var outputCurrent = readOnly || (generatedOutput && currentHash && generatedOutput.geometryHash === currentHash);
    var portState = uiState && uiState.plotter ? uiState.plotter.port_state : 'unknown';
    var available = portState === 'available';
    var items = [];
    items.push(readinessItem(exactReady ? 'ready' : 'blocked',
      exactReady ? (readOnly ? 'Uploaded HPGL path is exact' : 'Exact preview is current')
        : (preview && preview.valid === false ? 'Exact preview contains blocking errors' : 'Waiting for exact preview')));
    items.push(readinessItem(metadata && !metadata.outOfBounds ? 'ready' : 'blocked',
      metadata && !metadata.outOfBounds ? 'Artwork fits the loaded roll' : 'Artwork is outside the loaded roll'));
    items.push(readinessItem(!collisions.length ? 'ready' : 'blocked',
      collisions.length ? 'Copies overlap' : 'No copy collisions'));
    items.push(readinessItem(outputCurrent ? 'ready' : 'warning',
      outputCurrent ? (readOnly ? 'Uploaded HPGL is ready' : 'Generated HPGL matches this preview') : 'Generate HPGL for the current preview'));
    items.push(readinessItem(available ? 'ready' : (portState === 'busy' ? 'warning' : 'blocked'),
      available ? 'Configured serial port is available' : stateLabel(portState)));
    jQuery('#v2Readiness').html(items.join(''));

    var valid = !!(exactReady && metadata && !metadata.outOfBounds && !collisions.length);
    var sendable = valid && outputCurrent && available && !(uiState && uiState.active_job);
    if (readOnly && cutterWorkspace.filename) {
      jQuery('#fileName').val(cutterWorkspace.filename);
    }
    jQuery('#v2SendCut').prop('disabled', !sendable);
    var reason = '';
    if (!preview) reason = 'Waiting for the server to prepare the exact path.';
    else if (!valid) reason = 'Resolve the blocking workspace checks before generating.';
    else if (preview && !readOnly && !generatedOutput) reason = 'Generate the current exact path before sending.';
    jQuery('#v2GenerateReason').text(reason);
    updatePhaseStates(valid, outputCurrent, available);
    updateLayoutMetrics();
  }

  function updatePhaseStates(valid, outputCurrent, available) {
    var metadata = cutterWorkspace.metadata;
    var preview = cutterWorkspace.serverPreview;
    jQuery('[data-workspace-phase="design"]').toggleClass('complete', !!cutterWorkspace.payload);
    jQuery('[data-workspace-phase="layout"]')
      .toggleClass('complete', !!(metadata && !metadata.outOfBounds && preview && !(preview.collisions || []).length))
      .toggleClass('blocked', !!(metadata && (metadata.outOfBounds || (preview && (preview.collisions || []).length))));
    jQuery('[data-workspace-phase="prepare"]')
      .toggleClass('complete', !!valid)
      .toggleClass('blocked', !!(preview && preview.valid === false));
    jQuery('[data-workspace-phase="cut"]')
      .toggleClass('complete', !!(valid && outputCurrent && available))
      .toggleClass('blocked', !!(preview && !valid));
  }

  function invalidateGeneratedOutput() {
    if (!generatedOutput) return;
    generatedOutput = null;
    jQuery('#v2GeneratedOutput').text('Workspace changed. Generate fresh HPGL before sending.');
    jQuery('#v2SendCut').prop('disabled', true);
  }

  function installWorkspaceHooks() {
    var originalPreviewFile = window.previewFile;
    window.previewFile = function (filename, options) {
      cutterWorkspace.v2AutoFitDone = false;
      cutterWorkspace.v2ReadOnlyPhaseApplied = false;
      generatedOutput = null;
      historyState = { entries: [], index: -1, restoring: false };
      updateUndoButtons();
      return originalPreviewFile(filename, options);
    };

    var originalRender = window.workspaceRender;
    window.workspaceRender = function (resetView) {
      var result = originalRender(resetView);
      updateReadiness();
      return result;
    };

    var originalSchedule = window.workspaceSchedulePreparation;
    window.workspaceSchedulePreparation = function (resetView) {
      if (!cutterWorkspace.restoringProject) invalidateGeneratedOutput();
      return originalSchedule(resetView);
    };

    window.workspaceGenerateHpgl = function () {
      if (!cutterWorkspace.payload || cutterWorkspace.payload.read_only ||
          cutterWorkspace.metadata.outOfBounds || !cutterWorkspace.serverPreview ||
          cutterWorkspace.serverPreview.valid === false) return;
      var button = jQuery('#workspaceGenerate');
      button.prop('disabled', true).text('Generating…');
      var requestData = workspaceRequestData();
      requestData.geometry_hash = cutterWorkspace.serverPreview.geometry_hash;
      axios.post('/api/workspace/generate', requestData).then(function (response) {
        generatedOutput = {
          filename: response.data.filename,
          geometryHash: cutterWorkspace.serverPreview.geometry_hash
        };
        jQuery('#fileName').val(response.data.filename);
        jQuery('#v2GeneratedOutput').text(response.data.filename + ' is current and ready to send.');
        return refreshFiles(response.data.filename).then(function () {
          // V2 HPGL rows deliberately use their own "Open" action rather than
          // V1's .selectFile hook. Re-assert the generated output after the
          // shared file-list refresh so it remains the active plot target.
          jQuery('#fileName').val(response.data.filename);
          notify(response.data.message + '. Review readiness, then send when ready.', 'success');
          setWorkspacePhase('cut');
          updateReadiness();
        });
      }).catch(function (error) {
        notify(apiError(error, 'HPGL generation failed.'), 'danger');
      }).then(function () {
        button.prop('disabled', false).text('Generate HPGL');
      });
    };

    window.workspaceSaveProject = v2WorkspaceSaveProject;

    var originalRenderProfile = window.workspaceRenderProfile;
    window.workspaceRenderProfile = function (profile) {
      var result = originalRenderProfile(profile);
      jQuery('#v2MaterialProfile').text(profile && profile.name ? profile.name : 'Unprofiled');
      return result;
    };
  }

  function workspaceSnapshot() {
    if (!window.cutterWorkspace || !cutterWorkspace.payload || cutterWorkspace.payload.read_only) return null;
    try {
      return JSON.parse(JSON.stringify(workspaceRequestData()));
    } catch (_error) {
      return null;
    }
  }

  function captureWorkspaceState(force) {
    if (historyState.restoring) return;
    var snapshot = workspaceSnapshot();
    if (!snapshot) return;
    var encoded = JSON.stringify(snapshot);
    var current = historyState.entries[historyState.index];
    if (!force && current && current.encoded === encoded) return;
    historyState.entries = historyState.entries.slice(0, historyState.index + 1);
    historyState.entries.push({ encoded: encoded, value: snapshot });
    if (historyState.entries.length > 50) historyState.entries.shift();
    historyState.index = historyState.entries.length - 1;
    updateUndoButtons();
  }

  function updateUndoButtons() {
    jQuery('#v2Undo').prop('disabled', historyState.index <= 0);
    jQuery('#v2Redo').prop('disabled', historyState.index < 0 || historyState.index >= historyState.entries.length - 1);
  }

  function restoreWorkspaceState(index) {
    var entry = historyState.entries[index];
    if (!entry) return;
    historyState.restoring = true;
    historyState.index = index;
    workspaceApplySavedManifest(JSON.parse(JSON.stringify(entry.value)));
    cutterWorkspace.serverPreview = null;
    generatedOutput = null;
    workspaceSchedulePreparation(true);
    window.setTimeout(function () {
      historyState.restoring = false;
      updateUndoButtons();
    }, 0);
  }

  function v2SaveRevision(name, notes, tags) {
    if (!cutterWorkspace.serverPreview || cutterWorkspace.serverPreview.valid === false) {
      notify('Wait for a valid exact preview before saving.', 'warning');
      return Promise.reject(new Error('Invalid preview'));
    }
    var requestData = workspaceRequestData();
    requestData.geometry_hash = cutterWorkspace.serverPreview.geometry_hash;
    var existing = cutterWorkspace.projectContext;
    var metadata = cutterWorkspace.projectMetadata || {};
    var body = {
      project: {
        name: name || metadata.name,
        notes: notes == null ? (metadata.notes || '') : notes,
        tags: tags == null ? (metadata.tags || []) : tags
      },
      workspace: requestData
    };
    var url = existing
      ? '/api/projects/' + encodeURIComponent(existing.projectId) + '/revisions'
      : '/api/projects';
    var button = jQuery('#workspaceSaveProject').prop('disabled', true).text('Saving…');
    return axios.post(url, body).then(function (response) {
      var revision = response.data;
      cutterWorkspace.projectContext = {
        projectId: revision.project_id,
        revisionNumber: revision.revision_number,
        useCalibrationSnapshot: true,
        useProfileSnapshot: true
      };
      cutterWorkspace.projectMetadata = revision.project;
      cutterWorkspace.projectDirty = false;
      (revision.manifest.items || []).forEach(function (item, index) {
        if (cutterWorkspace.manifestItems[index]) cutterWorkspace.manifestItems[index].projectAssetId = item.project_asset_id;
      });
      jQuery('#workspaceProjectStatus').text(revision.project.name + ' · saved revision ' + revision.revision_number);
      button.text('Save new revision');
      notify('Saved immutable project revision ' + revision.revision_number + '.', 'success');
      return revision;
    }).catch(function (error) {
      if (error.message !== 'Invalid preview') notify(apiError(error, 'Project save failed.'), 'danger');
      throw error;
    }).then(function (revision) {
      button.prop('disabled', false).text('Save new revision');
      return revision;
    }, function (error) {
      button.prop('disabled', false).text(existing ? 'Save new revision' : 'Save as project');
      throw error;
    });
  }

  function v2WorkspaceSaveProject() {
    if (cutterWorkspace.projectContext) {
      v2SaveRevision(cutterWorkspace.projectMetadata && cutterWorkspace.projectMetadata.name).catch(function () {});
      return;
    }
    jQuery('#v2ProjectName').val(cutterWorkspace.filename.replace(/\.[^.]+$/, ''));
    jQuery('#v2ProjectNotes, #v2ProjectTags').val('');
    document.getElementById('v2ProjectSaveDialog').showModal();
  }

  function initProjectSaveDialog() {
    jQuery('#v2ProjectSaveConfirm').on('click', function (event) {
      var name = String(jQuery('#v2ProjectName').val() || '').trim();
      if (!name) {
        event.preventDefault();
        jQuery('#v2ProjectName')[0].reportValidity();
        return;
      }
      var notes = String(jQuery('#v2ProjectNotes').val() || '');
      var tags = String(jQuery('#v2ProjectTags').val() || '').split(',')
        .map(function (tag) { return tag.trim(); }).filter(Boolean);
      v2SaveRevision(name, notes, tags).catch(function () {});
    });
  }

  function openFromLocation() {
    if (currentView !== 'workbench') return;
    var params = new URLSearchParams(location.search);
    var filename = params.get('file');
    if (filename) previewFile(filename);
  }

  function loadProfilesSettings() {
    return axios.get('/api/material-profiles').then(function (response) {
      var profiles = response.data.profiles || [];
      jQuery('#v2ProfileLibrary').html(profiles.map(function (profile) {
        return '<div class="v2-panel-heading"><div><strong>' + escapeHtml(profile.name) +
          (profile.verified ? ' ✓' : '') + '</strong></div><span>' +
          Number(profile.roll_width_mm).toFixed(1) + ' mm roll</span></div>';
      }).join('') || '<p class="v2-help">No material profiles.</p>');
    }).catch(function (error) {
      jQuery('#v2ProfileLibrary').html('<p class="v2-alert v2-alert-error">' + escapeHtml(apiError(error)) + '</p>');
    });
  }

  function loadRecentProjects() {
    if (!jQuery('#v2RecentProjects').length) return Promise.resolve();
    return axios.get('/api/projects').then(function (response) {
      var projects = (response.data.projects || []).filter(function (project) {
        return !project.deleted && project.latest_revision;
      }).slice(0, 4);
      if (!projects.length) {
        jQuery('#v2RecentProjects').html('<p class="v2-help">No saved projects yet.</p>');
        return;
      }
      jQuery('#v2RecentProjects').html(projects.map(function (project) {
        var thumb = '/api/projects/' + encodeURIComponent(project.id) + '/revisions/' +
          project.latest_revision + '/thumbnail';
        return '<article class="v2-recent-project project-card" data-project-id="' +
          escapeHtml(project.id) + '" data-revision="' + Number(project.latest_revision) + '">' +
          '<img src="' + thumb + '" alt="">' +
          '<div><strong>' + escapeHtml(project.name) + '</strong><span>Revision ' +
          Number(project.latest_revision) + (project.tags && project.tags.length
            ? ' · ' + escapeHtml(project.tags.join(', ')) : '') + '</span></div>' +
          '<button class="v2-button v2-button-primary project-open" type="button">Open</button></article>';
      }).join(''));
    }).catch(function (error) {
      jQuery('#v2RecentProjects').html(
        '<p class="v2-alert v2-alert-error">' + escapeHtml(apiError(error, 'Projects unavailable.')) + '</p>'
      );
    });
  }

  function loadCalibrationSettings() {
    var port = jQuery('#portList').val() || (uiState && uiState.plotter && uiState.plotter.configured_port) || '';
    var device = jQuery('#device').val() || 'creation_1200';
    if (!port) {
      jQuery('#v2CalibrationSummary').html('<p class="v2-alert v2-alert-warning">Configure a stable serial port first.</p>');
      return Promise.resolve();
    }
    return axios.get('/api/cutter-calibrations', { params: { serial_port: port, device: device } })
      .then(function (response) {
        var calibration = response.data.calibration;
        if (!calibration) {
          jQuery('#v2CalibrationSummary').html('<p class="v2-help">No accepted calibration for this cutter.</p>');
          return;
        }
        jQuery('#v2CalibrationSummary').html(
          '<dl class="v2-fact-list"><div><dt>Port</dt><dd class="v2-mono">' + escapeHtml(calibration.serial_port) +
          '</dd></div><div><dt>X factor</dt><dd>' + Number(calibration.factor_x).toFixed(6) +
          '</dd></div><div><dt>Y factor</dt><dd>' + Number(calibration.factor_y).toFixed(6) +
          '</dd></div><div><dt>State</dt><dd>' + (calibration.enabled ? 'Applied' : 'Accepted, disabled') + '</dd></div></dl>'
        );
      });
  }

  function loadSettings() {
    updatePorts();
    axios.get('/save_configfile').then(function (response) {
      var data = response.data;
      jQuery('#telegram_token').val(data.telegram_token);
      jQuery('#telegram_chatid').val(data.telegram_chatid);
      jQuery('#tasmota_enable').val(data.tasmota_enable);
      jQuery('#tasmota_ip').val(data.tasmota_ip);
      jQuery('#plotter_name').val(data.plotter_name);
      jQuery('#plotter_port').val(data.plotter_port);
      jQuery('#plotter_device, #device').val(data.plotter_device);
      jQuery('#plotter_baudrate, #baudRate').val(data.plotter_baudrate);
      jQuery('#portList').data('configured-port', data.plotter_port);
      if (data.plotter_port && !jQuery('#portList option[value="' + data.plotter_port + '"]').length) {
        jQuery('#portList').prepend('<option value="' + escapeHtml(data.plotter_port) + '">' + escapeHtml(data.plotter_port) + '</option>');
      }
      jQuery('#portList').val(data.plotter_port);
      return loadCalibrationSettings();
    });
    loadProfilesSettings();
  }

  function filterJobs(filter) {
    jQuery('#jobHistoryBody tr').each(function () {
      var status = jQuery(this).find('td').eq(4).text().trim();
      jQuery(this).toggle(filter === 'all' || status === filter);
    });
  }

  function bindActions() {
    jQuery('[data-workspace-phase]').on('click', function () {
      setWorkspacePhase(jQuery(this).data('workspace-phase'));
    });
    jQuery('#v2Undo').on('click', function () { if (historyState.index > 0) restoreWorkspaceState(historyState.index - 1); });
    jQuery('#v2Redo').on('click', function () { if (historyState.index < historyState.entries.length - 1) restoreWorkspaceState(historyState.index + 1); });

    jQuery('body').on('click', '.previewFile', function (event) {
      event.preventDefault();
      navigateWorkbench(jQuery(this).data('filename'));
    }).on('click', '.deleteFile', function (event) {
      event.preventDefault();
      deleteFile(jQuery(this));
    }).on('click', '.cancelJob', function (event) {
      event.preventDefault();
      cancelJob(jQuery(this).data('job-id'));
    }).on('click', '.stopPlot', function (event) {
      event.preventDefault();
      stopPlot();
    });

    jQuery('.updateFiles').on('click', function () { refreshFiles(); });
    jQuery('.updatePorts').on('click', function () { updatePorts().then(refreshUiState); });
    jQuery('.refreshJobs').on('click', refreshJobHistory);
    jQuery('.clearLog').on('click', clearLog);
    jQuery('#v2FileSearch').on('input', filterFiles);
    jQuery('#v2ProjectSearch').on('input', function () {
      var query = String(this.value || '').toLowerCase();
      jQuery('.project-card').each(function () {
        jQuery(this).toggle(!query || jQuery(this).text().toLowerCase().indexOf(query) >= 0);
      });
    });
    jQuery('#projectRefresh, #projectShowDeleted').on('click change', loadProjectLibrary);
    jQuery('.v2-filter-chip').on('click', function () {
      jQuery('.v2-filter-chip').removeClass('active');
      jQuery(this).addClass('active');
      filterJobs(jQuery(this).data('job-filter'));
    });
    jQuery('.v2-settings-nav button').on('click', function () {
      var target = jQuery(this).data('settings-target');
      jQuery('.v2-settings-nav button').removeClass('active');
      jQuery(this).addClass('active');
      jQuery('[data-settings-panel]').prop('hidden', true).filter('[data-settings-panel="' + target + '"]').prop('hidden', false);
      if (target === 'materials') loadProfilesSettings();
      if (target === 'calibration') loadCalibrationSettings();
    });
    jQuery('#useCustomPort').on('change', function () {
      var custom = this.checked;
      jQuery('#v2CustomPortField').prop('hidden', !custom);
      jQuery('#portList').prop('disabled', custom).attr('name', custom ? '_port_unused' : 'port');
      jQuery('#customPort').attr('name', custom ? 'port' : 'custom_port');
    });
    jQuery('#resetConnectionButton').on('click', resetPlotterConnection);
    jQuery('.saveConfig').on('click', saveConfig);
    jQuery('.actionTasmota').on('click', actionTasmota);
    jQuery('.actionReboot').on('click', function () {
      UIkit.modal.confirm('Reboot the Raspberry Pi? PCP will be unavailable temporarily.').then(actionReboot);
    });
    jQuery('.actionPoweroff').on('click', function () {
      UIkit.modal.confirm('Power off the Raspberry Pi? It must be powered on manually afterward.').then(actionPoweroff);
    });
    jQuery('#v2SendCut').on('click', function () {
      if (this.disabled) return;
      startPlot();
      refreshUiState();
    });
    jQuery('#v2MobileMenu').on('click', function () { jQuery(document.body).toggleClass('v2-menu-open'); });
    jQuery('.v2-nav a').on('click', function () { jQuery(document.body).removeClass('v2-menu-open'); });

    jQuery('#workspaceData').on(
      'change.v2History',
      '.workspace-transform,.workspace-layout,.workspace-preparation,.workspace-cutting-aid,.workspace-copy-count',
      function () { window.setTimeout(captureWorkspaceState, 0); }
    );
    jQuery('#workspaceSvg').on('pointerup.v2History', function () { window.setTimeout(captureWorkspaceState, 20); });
  }

  function bindSocket() {
    var socket = io();
    socket.on('connect', function () { socket.emit('connection', { data: 'PCP V2 connected' }); });
    socket.on('status_log', function (message) {
      var current = jQuery('#statusLog').text();
      jQuery('#statusLog').text(current + (current ? '\n' : '') + message.data);
    });
    socket.on('error', function (message) {
      notify(message.data || 'Cutter error', 'danger');
      refreshUiState();
    });
    socket.on('print_progress', function (message) {
      if (uiState && uiState.active_job) {
        uiState.active_job.progress = Number(message.data || 0);
        renderUiState(uiState);
      }
    });
    socket.on('job_update', function (message) {
      renderJobHistory(message.jobs || []);
      refreshUiState();
    });
    socket.on('connection_reset', function (message) {
      jQuery('#connectionResetStatus').text(message.message || '');
      jQuery('#v2ResetPhases').append(
        '<li data-status="' + escapeHtml(message.status || '') + '">' + escapeHtml(message.message || message.phase) + '</li>'
      );
      announce(message.message);
      refreshUiState();
    });
    socket.on('plot_cancelled', function () {
      notify('Transmission stopped; buffered cutter motion may continue.', 'danger');
      announce('Cut transmission cancelled. Buffered cutter motion may continue.');
      refreshUiState();
    });
    socket.on('lock_edit', function (message) {
      var locked = message.data === 'on';
      jQuery('.lock-edit').prop('disabled', locked);
      refreshUiState();
    });
  }

  function init() {
    showView(currentView);
    installWorkspaceHooks();
    bindProjectLibraryControls();
    bindActions();
    bindSocket();
    initProjectSaveDialog();
    initUpload();
    refreshUiState();
    refreshFiles();
    loadRecentProjects();
    refreshJobHistory();
    updateConfiguration();
    loadSettings();

    if (currentView === 'projects') loadProjectLibrary();
    openFromLocation();

    var preferredPhase = readPrefs().workspacePhase || 'design';
    setWorkspacePhase(preferredPhase, false);

    var previewWatcher = window.setInterval(function () {
      if (cutterWorkspace && cutterWorkspace.payload) {
        if (cutterWorkspace.payload.read_only && !cutterWorkspace.v2ReadOnlyPhaseApplied) {
          cutterWorkspace.v2ReadOnlyPhaseApplied = true;
          setWorkspacePhase('cut');
        }
        if (!cutterWorkspace.v2AutoFitDone) {
          cutterWorkspace.v2AutoFitDone = true;
          workspaceFitDesign();
        }
        if (cutterWorkspace.serverPreview && !historyState.entries.length) captureWorkspaceState(true);
        updateReadiness();
      }
    }, 500);
    window.addEventListener('beforeunload', function () { window.clearInterval(previewWatcher); });
    window.addEventListener('popstate', function () { window.location.reload(); });
  }

  jQuery(init);
}());
