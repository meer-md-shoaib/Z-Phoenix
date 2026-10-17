/**
 * Z-PHOENIX  base.js
 * Shared UI helpers, API adapter, connection status, toasts.
 * No page-specific logic belongs here.
 *
 * Integration assumptions (update when backend contract is confirmed):
 *   - Flask app serves on the same origin, port 8080
 *   - All API endpoints return JSON
 *   - Socket.IO events: "telemetry", "agent_event", "incident_update"
 *   - DEMO_MODE = true when /api/health is unreachable (auto-detected)
 */

/* ─────────────────────────────────────────
   Namespace
───────────────────────────────────────── */
window.ZP = window.ZP || {};

(function (ZP) {
  'use strict';

  /* ── 1. Configuration ──────────────────────────────────────────────────── */
  const API = {
    health:     '/api/health',
    telemetry:  '/api/telemetry',
    attack:     '/api/attack',
    analyze:    '/api/analyze',
    validate:   '/api/validate',
    approve:    '/api/approve',
    recover:    '/api/recover',
    playbooks:  '/api/playbooks',
    replay:     '/api/replay',
    passport:   '/api/recovery_passport',   // + /{id}
    audit:      '/api/audit',
    incidents:  '/api/incidents',
  };

  /** Socket.IO event names expected from the backend */
  const SOCKET_EVENTS = {
    telemetry:       'telemetry',
    agentEvent:      'agent_event',
    incidentUpdate:  'incident_update',
  };

  /** Demo mode is active by default until backend /api/health confirms connectivity */
  let DEMO_MODE = true;

  /** Request timeout in milliseconds */
  const REQUEST_TIMEOUT_MS = 12000;

  ZP.API            = API;
  ZP.SOCKET_EVENTS  = SOCKET_EVENTS;
  ZP.isDemoMode     = () => DEMO_MODE;

  /* ── 2. Connection state ───────────────────────────────────────────────── */
  const connEl    = document.getElementById('connIndicator');
  const connLabel = document.getElementById('connLabel');

  function setConnectionState(state) {
    if (!connEl) return;
    connEl.className = 'conn-indicator';
    switch (state) {
      case 'connected':
        connEl.classList.add('conn-connected'); connLabel.textContent = 'CONNECTED'; DEMO_MODE = false; break;
      case 'reconnecting':
        connEl.classList.add('conn-reconnecting'); connLabel.textContent = 'RECONNECTING'; break;
      case 'offline':
        connEl.classList.add('conn-offline'); connLabel.textContent = 'OFFLINE'; break;
      case 'demo':
      default:
        connEl.classList.add('conn-demo'); connLabel.textContent = 'DEMO MODE'; DEMO_MODE = true; break;
    }
    document.querySelectorAll('.demo-label, #demoNotice, #chaosDemoLabel').forEach(el => {
      el.style.display = DEMO_MODE ? '' : 'none';
    });
  }

  ZP.setConnectionState = setConnectionState;

  /* ── 3. System state badge ─────────────────────────────────────────────── */
  const stateBadge = document.getElementById('systemStateBadge');
  const stateLabels = {
    healthy:    '● HEALTHY',
    incident:   '⚠ INCIDENT ACTIVE',
    degraded:   '⚠ DEGRADED',
    recovering: '↻ RECOVERING',
    approval:   '⏳ AWAITING APPROVAL',
  };

  function setSystemState(state) {
    if (!stateBadge) return;
    stateBadge.className = 'incident-badge state-' + state;
    stateBadge.textContent = stateLabels[state] || state.toUpperCase();
  }

  ZP.setSystemState = setSystemState;

  /* ── 4. Last-updated time ─────────────────────────────────────────────── */
  const lastUpdatedEl = document.getElementById('lastUpdatedTime');

  function markUpdated() {
    if (!lastUpdatedEl) return;
    const now = new Date();
    lastUpdatedEl.textContent = 'Updated ' + now.toLocaleTimeString();
    lastUpdatedEl.classList.remove('stale');
  }

  function markStale() {
    if (!lastUpdatedEl) return;
    lastUpdatedEl.classList.add('stale');
    lastUpdatedEl.textContent = '⚠ Data stale';
  }

  ZP.markUpdated = markUpdated;
  ZP.markStale   = markStale;

  /* ── 5. Toast notifications ────────────────────────────────────────────── */
  const toastContainer = document.getElementById('toastContainer');
  const TOAST_ICONS = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ', ai: '🤖' };

  function showToast(type, title, message, durationMs) {
    if (!toastContainer) return;
    const duration = durationMs !== undefined ? durationMs : (type === 'error' ? 8000 : 5000);
    const toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.setAttribute('role', 'alert');
    toast.innerHTML =
      '<span class="toast-icon" aria-hidden="true">' + (TOAST_ICONS[type] || 'ℹ') + '</span>' +
      '<div class="toast-body">' +
        '<div class="toast-title">' + ZP.escapeHtml(title) + '</div>' +
        (message ? '<div class="toast-msg">' + ZP.escapeHtml(message) + '</div>' : '') +
      '</div>' +
      '<button class="toast-close" aria-label="Dismiss notification">×</button>';
    toastContainer.appendChild(toast);
    toast.querySelector('.toast-close').addEventListener('click', () => dismissToast(toast));
    if (duration > 0) setTimeout(() => dismissToast(toast), duration);
  }

  function dismissToast(toast) {
    toast.style.animation = 'toast-out 200ms ease forwards';
    setTimeout(() => toast.remove(), 200);
  }

  ZP.toast = {
    success: (title, msg)  => showToast('success', title, msg),
    error:   (title, msg)  => showToast('error',   title, msg),
    warning: (title, msg)  => showToast('warning', title, msg),
    info:    (title, msg)  => showToast('info',    title, msg),
    ai:      (title, msg)  => showToast('ai',      title, msg),
  };

  /* ── 6. HTML escaping (prevent XSS from API text) ──────────────────────── */
  const escapeMap = { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' };
  ZP.escapeHtml = function(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/[&<>"']/g, c => escapeMap[c]);
  };

  /* ── 7. API helper ─────────────────────────────────────────────────────── */
  /**
   * ZP.request(method, url, [body], [opts])
   * Returns a Promise resolving to parsed JSON.
   * Throws { status, message, raw } on error.
   * Never puts secrets in URLs or bodies — all auth is handled server-side.
   */
  async function request(method, url, body, opts) {
    opts = opts || {};
    const controller = new AbortController();
    const timerId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    const fetchOpts = {
      method:  method.toUpperCase(),
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      signal:  controller.signal,
    };
    if (body !== undefined && body !== null) {
      fetchOpts.body = JSON.stringify(body);
    }
    try {
      const res = await fetch(url, fetchOpts);
      clearTimeout(timerId);
      let data;
      const ct = res.headers.get('content-type') || '';
      try { data = ct.includes('json') ? await res.json() : await res.text(); }
      catch(_) { data = null; }
      if (!res.ok) {
        const msg = (data && data.error) ? data.error : ('HTTP ' + res.status);
        throw { status: res.status, message: msg, raw: data };
      }
      return data;
    } catch(err) {
      clearTimeout(timerId);
      if (err.name === 'AbortError') throw { status: 0, message: 'Request timed out', raw: null };
      if (err.status !== undefined) throw err;
      throw { status: 0, message: err.message || 'Network error', raw: null };
    }
  }

  ZP.get    = (url, opts)       => request('GET',    url, null, opts);
  ZP.post   = (url, body, opts) => request('POST',   url, body, opts);
  ZP.delete = (url, opts)       => request('DELETE', url, null, opts);
  ZP.request = request;

  /* ── 8. Button loading state helpers ──────────────────────────────────── */
  ZP.setButtonLoading = function(btn, isLoading, originalLabel) {
    if (!btn) return;
    if (isLoading) {
      btn.dataset.originalLabel = btn.innerHTML;
      btn.innerHTML = '<span class="spinner spinner-sm" aria-hidden="true"></span> ' + (originalLabel || 'Loading…');
      btn.disabled = true;
    } else {
      btn.innerHTML = btn.dataset.originalLabel || btn.innerHTML;
      btn.disabled = false;
    }
  };

  /* ── 9. Health check and demo-mode detection ──────────────────────────── */
  async function checkHealth() {
    try {
      await ZP.get(API.health);
      setConnectionState('connected');
      return true;
    } catch(_) {
      setConnectionState('demo');
      return false;
    }
  }

  ZP.checkHealth = checkHealth;

  /* ── 10. Format helpers ────────────────────────────────────────────────── */
  ZP.fmt = {
    /** Format ISO date string to readable local time */
    ts: function(isoOrMs) {
      if (!isoOrMs) return '—';
      const d = new Date(isoOrMs);
      return isNaN(d) ? isoOrMs : d.toLocaleString();
    },
    /** Format duration in seconds to readable form */
    duration: function(seconds) {
      if (seconds === null || seconds === undefined) return '—';
      if (seconds < 60) return seconds + 's';
      const m = Math.floor(seconds / 60), s = seconds % 60;
      return m + 'm ' + (s > 0 ? s + 's' : '');
    },
    /** Truncate a string with ellipsis */
    truncate: function(str, max) {
      if (!str) return '';
      return str.length > max ? str.slice(0, max) + '…' : str;
    },
    /** Risk-level to pill class */
    riskPill: function(risk) {
      const map = { low: 'pill-healthy', medium: 'pill-warning', high: 'pill-critical' };
      const cls = map[(risk || '').toLowerCase()] || 'pill-muted';
      return '<span class="pill ' + cls + ' pill-no-dot">' + ZP.escapeHtml((risk || 'unknown').toUpperCase()) + '</span>';
    },
    /** Status text to pill class */
    statusPill: function(status) {
      const map = {
        healthy: 'pill-healthy', ok: 'pill-healthy', resolved: 'pill-healthy', approved: 'pill-approval',
        degraded: 'pill-warning', warning: 'pill-warning', validating: 'pill-ai',
        incident: 'pill-critical', critical: 'pill-critical', failed: 'pill-critical',
        draft: 'pill-muted', validated: 'pill-info',
      };
      const cls = map[(status || '').toLowerCase()] || 'pill-muted';
      return '<span class="pill ' + cls + '">' + ZP.escapeHtml((status || '—').toUpperCase()) + '</span>';
    },
  };

  /* ── 11. Socket.IO wrapper (safe to call even if SocketIO unavailable) ── */
  let _socket = null;

  ZP.connectSocket = function(onTelemetry, onAgentEvent, onIncidentUpdate) {
    if (typeof io === 'undefined') {
      // Socket.IO not available; caller should use polling fallback
      return null;
    }
    try {
      _socket = io({ transports: ['websocket', 'polling'], reconnectionAttempts: 5 });
      _socket.on('connect', () => setConnectionState('connected'));
      _socket.on('disconnect', () => setConnectionState('reconnecting'));
      _socket.on('connect_error', () => setConnectionState('offline'));
      if (onTelemetry)       _socket.on(SOCKET_EVENTS.telemetry,      onTelemetry);
      if (onAgentEvent)      _socket.on(SOCKET_EVENTS.agentEvent,     onAgentEvent);
      if (onIncidentUpdate)  _socket.on(SOCKET_EVENTS.incidentUpdate, onIncidentUpdate);
      return _socket;
    } catch(e) {
      console.warn('ZP: Socket.IO connection failed', e);
      return null;
    }
  };

  ZP.getSocket = () => _socket;

  /* ── 12. Auto health check on page load ──────────────────────────────── */
  document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
  });

})(window.ZP);