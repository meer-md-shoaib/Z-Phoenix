/*
 * Z-PHOENIX :: socket.js
 * Socket.IO connection plus the shared client-side dispatcher.
 * Every server push (telemetry, stages, incident, agent, audit, console)
 * is re-fired through ZPhoenix.on()/fire() so charts.js and app.js can
 * subscribe without knowing anything about the transport.
 * Falls back to 1 Hz REST polling if the Socket.IO client is unavailable.
 */
(function () {
  "use strict";

  var handlers = {};
  var SERVER_EVENTS = ["telemetry", "stages", "incident", "incident_cleared",
                       "agent", "audit", "console"];

  var Z = window.ZPhoenix = {
    connected: false,

    /* ------------------------------------------------------------ events */
    on: function (event, fn) {
      (handlers[event] = handlers[event] || []).push(fn);
      return fn;
    },
    off: function (event, fn) {
      var list = handlers[event] || [];
      var idx = list.indexOf(fn);
      if (idx >= 0) list.splice(idx, 1);
    },
    fire: function (event, data) {
      var list = handlers[event] || [];
      for (var i = 0; i < list.length; i++) {
        try { list[i](data); } catch (err) { console.error("[Z-PHOENIX]", event, err); }
      }
    },

    /* --------------------------------------------------------------- REST */
    api: function (path, options) {
      options = options || {};
      var init = {
        method: options.method || (options.body ? "POST" : "GET"),
        headers: { "Content-Type": "application/json" }
      };
      if (options.body) init.body = JSON.stringify(options.body);
      return fetch(path, init).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (data) {
          if (!response.ok) {
            var err = new Error(data.error || ("HTTP " + response.status));
            err.status = response.status;
            err.data = data;
            throw err;
          }
          return data;
        });
      });
    },

    /* ------------------------------------------------------------- format */
    clockString: function (ts) {
      var d = ts ? new Date(ts * 1000) : new Date();
      if (isNaN(d.getTime())) d = new Date();
      function p(n) { return (n < 10 ? "0" : "") + n; }
      return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
    },

    fmt: function (value, digits) {
      if (value === null || value === undefined || isNaN(value)) return "-";
      return Number(value).toFixed(digits === undefined ? 0 : digits);
    },

    /* -------------------------------------------------------------- toast */
    toast: function (message, kind) {
      var host = document.getElementById("toast");
      if (!host) return;
      var item = document.createElement("div");
      item.className = "toast-item " + (kind || "");
      item.textContent = message;
      host.appendChild(item);
      setTimeout(function () {
        item.style.opacity = "0";
        item.style.transition = "opacity .3s";
        setTimeout(function () { if (item.parentNode) item.parentNode.removeChild(item); }, 320);
      }, 5200);
    }
  };

  /* --------------------------------------------------------- transport */
  if (typeof io !== "undefined") {
    var socket = io({ transports: ["websocket", "polling"], reconnection: true });
    Z.socket = socket;
    SERVER_EVENTS.forEach(function (event) {
      socket.on(event, function (data) { Z.fire(event, data); });
    });
    socket.on("connect", function () {
      Z.connected = true;
      Z.fire("connection", { connected: true });
    });
    socket.on("disconnect", function () {
      Z.connected = false;
      Z.fire("connection", { connected: false });
    });
  } else {
    /* Degraded mode: 1 Hz REST polling keeps the console alive. */
    setInterval(function () {
      Z.api("/api/telemetry").then(function (data) { Z.fire("telemetry", data); }).catch(function () {});
    }, 1000);
    Z.fire("connection", { connected: false, degraded: true });
  }
})();
