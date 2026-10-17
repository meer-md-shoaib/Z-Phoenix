/**
 * Z-PHOENIX - socket.js
 * Compatibility bridge for Socket.IO connection and metric event bindings.
 * Core implementation lives in base.js (window.ZP.connectSocket).
 */
(function () {
  'use strict';
  window.ZP = window.ZP || {};
  window.initSocket = function (onTelemetry, onAgentEvent, onIncidentUpdate) {
    return window.ZP.connectSocket ? window.ZP.connectSocket(onTelemetry, onAgentEvent, onIncidentUpdate) : null;
  };
})();
