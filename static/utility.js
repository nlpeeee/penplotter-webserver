// Nicer format for file list
function renderFileListElement(name) {

  // Het the file extension
  const re = /(?:\.([^.]+))?$/;
  const extensionMatch = re.exec(name);
  const ext = extensionMatch[1] ? extensionMatch[1].toLowerCase() : '';
  let html = ''

  switch (ext) {
    case 'hpgl':
      html = `<div class="uk-grid uk-grid-small">
                <div class="uk-width-expand">
                  <a href="#" class="selectFile" data-filename="${name}">
                    <span>${name}</span>
                  </a>
                </div>
                <div class="uk-width-auto uk-text-right panel-icons">
                  <a href="#" class="uk-button uk-button-default uk-button-small selectFile" data-filename="${name}" title="Select for cutting">
                    Select
                  </a>
                  <a href="#" class="uk-button uk-button-primary uk-button-small previewFile" data-filename="${name}" title="Preview Cut Path">
                    <span data-uk-icon="icon: search; ratio: 0.75"></span> Preview
                  </a>
                  <a href="#" class="uk-icon-link deleteFile lock-edit" data-filename="${name}" title="Delete" data-uk-tooltip data-uk-icon="icon: close"></a>
                </div>
              </div>`;
        break;
    case 'svg':
      html = `<div class="uk-grid uk-grid-small">
                <div class="uk-width-expand">
                  <a href="#" class="no-selectFile" data-filename="${name}">
                    <span>${name}</span>
                  </a>
                </div>
                <div class="uk-width-auto uk-text-right panel-icons">
                  <a href="#" class="uk-button uk-button-primary uk-button-small previewFile" data-filename="${name}" title="Preview Cut Path">
                    <span data-uk-icon="icon: search; ratio: 0.75"></span> Preview
                  </a>
                  <a href="#" class="uk-icon-link convertFile lock-edit" data-filename="${name}" title="Convert to HPGL" data-uk-tooltip data-uk-icon="icon: bolt"></a>
                  <a href="#" class="uk-icon-link deleteFile lock-edit" data-filename="${name}" title="Delete" data-uk-tooltip data-uk-icon="icon: close"></a>
                </div>
              </div>`;
        break;
    default:
      html = `<div class="uk-grid uk-grid-small">
                <div class="uk-width-expand">
                  <a href="#" class="no-selectFile" data-filename="${name}">
                    <span>${name}</span>
                  </a>
                </div>
                <div class="uk-width-auto uk-text-right panel-icons">
                  <a href="#" class="uk-icon-link deleteFile lock-edit" data-filename="${name}" title="Delete" data-uk-tooltip data-uk-icon="icon: close"></a>
                </div>
              </div>`;
  }

  return html;
}

// Simplift notification handling
function notify(message, status) {
    UIkit.notification({
      message: message,
      status: status,
      pos: 'top-right',
      timeout: 5000
  });
}

function scrollLog() {
  jQuery('.auto-scroll').each(function( index ) {
    jQuery(this).animate({
      scrollTop: jQuery(this)[0].scrollHeight
    }, 10);
  });
}
