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

// Update file list
function updateFiles() {
  axios.get('/update_files')
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        // Remove old content from list
        jQuery('#fileList').html('');

        for (var content of response.data.content) {
          jQuery('#fileList').append(`<li> ${renderFileListElement(content.name)} </li>`)
        }
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    })
    .then(function() {});
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
  jQuery('.selectedFilename').html(filename);
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
        updateFiles()
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    })
    .then(function() {});
}

// Handle file conversion
function convertFileModal(element) {
  const filename = jQuery(element).data('filename');
  jQuery('#convertFile').val(filename)
  UIkit.modal('#modal-convertFile').show();
}

// Start conversion
function convertFile() {
  const convertData = jQuery('#convertData').serializeArray()
  console.log('convertData', convertData);

  // Validation
  if (jQuery('#convertFile').val() == '') {
    notify('No *.svg file selected', 'danger');
    return false
  }

  axios.post('/start_conversion', jQuery('#convertData').serialize())
    .then(function(response) {
      console.log(response);
      // handle success
      if (response.status == 200) {
        updateFiles();
        UIkit.modal('#modal-convertFile').hide();
        notify(response.data, 'warning')
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
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
    notify('No *.hpgl file selected', 'danger');
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

  axios.get('/stop_plot')
    .then(function(response) {
      // handle success
      if (response.status == 200) {
        console.log(response);
        notify('Stopped Print', 'danger');

        // Update sidebar
        jQuery('.selectedFilename').html("");
      }
    })
    .catch(function(error) {
      notify(error, 'danger')
      console.error(error);
    });

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

// Show cut-path preview in a modal (SVG served directly; HPGL converted via vpype)
function previewFile(filename) {
  jQuery('#previewModalTitle').text(filename);
  jQuery('#previewImage').hide().attr('src', '');
  jQuery('#previewError').hide().text('');
  jQuery('#previewSpinner').show();
  UIkit.modal('#modal-preview').show();

  jQuery('#previewImage')
    .off('load error')
    .on('load', function() {
      jQuery('#previewSpinner').hide();
      jQuery(this).show();
    })
    .on('error', function() {
      jQuery('#previewSpinner').hide();
      jQuery('#previewError').text('Preview failed — file may be empty or conversion error.').show();
    })
    .attr('src', '/preview/' + encodeURIComponent(filename));
}
