// webpack.mix.js

let mix = require('laravel-mix');

mix.js('resources/v2/vendor.js', 'v2/vendor.js')
  .styles([
    'node_modules/uikit/dist/css/uikit.min.css',
    'node_modules/dropzone/dist/min/dropzone.min.css',
  ], 'static/v2/vendor.css')
  .options({
    processCssUrls: false,
  })
  .setPublicPath('static')
