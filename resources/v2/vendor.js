import jQuery from 'jquery';
import axios from 'axios';
import Dropzone from 'dropzone';
import UIkit from 'uikit';
import Icons from 'uikit/dist/js/uikit-icons';
import { io } from 'socket.io-client';

window.jQuery = window.$ = jQuery;
window.axios = axios;
window.Dropzone = Dropzone;
window.UIkit = UIkit;
window.io = io;

UIkit.use(Icons);
