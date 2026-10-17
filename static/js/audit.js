/**
 * Z-PHOENIX  audit.js
 * Audit Log: load, filter, render. Read-only — no edit/delete UI.
 */
(function () {
  'use strict';

  const DEMO_EVENTS = [
    { ts:'2026-10-17T10:02:10Z', actor:'system',   action:'chaos.inject',      target:'cpu_spike · PID 4821',              risk:'medium', details:'Fault started: cpu_spike, 30s, intensity=high' },
    { ts:'2026-10-17T10:02:57Z', actor:'system',   action:'anomaly.detect',    target:'CPU 94% for 47s',                   risk:'high',   details:'Threshold breached: CPU > 80%' },
    { ts:'2026-10-17T10:03:10Z', actor:'agent',    action:'diagnosis.done',    target:'Runaway batch PID 4821 + Db2 chain', risk:'medium', details:'Confidence: 91%, fallback: false' },
    { ts:'2026-10-17T10:03:28Z', actor:'agent',    action:'recovery.proposed', target:'kill -15 + Db2 rollback',           risk:'medium', details:'4 steps proposed, allowlisted' },
    { ts:'2026-10-17T10:03:45Z', actor:'agent',    action:'validation.pass',   target:'Sandbox replay OK',                 risk:'low',    details:'CPU normalised to 23% in 18s' },
    { ts:'2026-10-17T10:03:59Z', actor:'user',     action:'playbook.approve',  target:'INC-20261017-001',                  risk:'medium', details:'Human approval: operator' },
    { ts:'2026-10-17T10:04:11Z', actor:'system',   action:'recovery.execute',  target:'PB-001 v1',                         risk:'medium', details:'kill -15 4821 OK; Db2 rollback OK' },
    { ts:'2026-10-17T10:04:52Z', actor:'system',   action:'incident.resolved', target:'INC-20261017-001',                  risk:'low',    details:'CPU 22%, TPS 112, Db2 locks 4' },
    { ts:'2026-10-17T10:05:00Z', actor:'system',   action:'playbook.saved',    target:'PB-001 v1',                         risk:'low',    details:'Playbook saved to library' },
    { ts:'2026-10-17T11:30:00Z', actor:'system',   action:'chaos.inject',      target:'db2_lock_contention',               risk:'medium', details:'Fault started: db2_lock_contention' },
    { ts:'2026-10-17T11:31:00Z', actor:'system',   action:'anomaly.detect',    target:'Db2 locks 201',                     risk:'high',   details:'Threshold breached: Db2 locks > 50' },
    { ts:'2026-10-17T11:31:30Z', actor:'system',   action:'playbook.replay',   target:'PB-002 v2',                         risk:'low',    details:'Replay matched: 82% similarity. Recovered in 1.8s.' },
    { ts:'2026-10-17T11:32:20Z', actor:'system',   action:'incident.resolved', target:'INC-20261017-002',                  risk:'low',    details:'Replay recovery successful' },
  ];

  const ACTOR_COLORS = { agent:'var(--clr-ai)', user:'var(--clr-approval)', system:'var(--clr-info)' };

  let _allEvents   = [];
  let _filters     = { actor:'', action:'', risk:'' };

  /* ── Filter helpers ── */
  function applyFilters(events) {
    return events.filter(ev => {
      if (_filters.actor  && (ev.actor  ||'').toLowerCase() !== _filters.actor)  return false;
      if (_filters.action && !(ev.action||'').toLowerCase().includes(_filters.action)) return false;
      if (_filters.risk   && (ev.risk   ||'').toLowerCase() !== _filters.risk)   return false;
      return true;
    });
  }

  /* ── Render ── */
  function renderAudit(events) {
    const el  = document.getElementById('auditListEl');
    const cnt = document.getElementById('auditCountPill');
    if (!el) return;

    showState(events.length === 0 ? 'empty' : 'list');
    if (!events.length) return;

    if (cnt) cnt.textContent = events.length + ' event' + (events.length !== 1 ? 's' : '');
    el.innerHTML = '';
    // Most recent first
    [...events].reverse().forEach(ev => {
      const row = document.createElement('div');
      row.className = 'audit-row';
      row.setAttribute('role', 'row');

      const actorColor = ACTOR_COLORS[(ev.actor||'').toLowerCase()] || 'var(--clr-text-secondary)';
      const riskPill   = ZP.fmt.riskPill(ev.risk);

      row.innerHTML =
        '<span class="audit-ts">' + ZP.escapeHtml(ZP.fmt.ts(ev.ts)) + '</span>' +
        '<span class="audit-actor" style="color:' + actorColor + ';">' + ZP.escapeHtml(ev.actor || '—') + '</span>' +
        '<span class="audit-action">' + ZP.escapeHtml(ev.action || '—') + '</span>' +
        '<span class="audit-target">' +
          ZP.escapeHtml(ev.target || '') +
          (ev.details ? '<br><span class="text-muted">' + ZP.escapeHtml(ZP.fmt.truncate(ev.details, 100)) + '</span>' : '') +
        '</span>' +
        '<span class="audit-risk">' + riskPill + '</span>';

      el.appendChild(row);
    });

    const lu = document.getElementById('auditLastUpdated');
    if (lu) { lu.textContent = 'Updated ' + new Date().toLocaleTimeString(); lu.classList.remove('stale'); }
  }

  function showState(which) {
    const map = { loading:'auditLoading', list:'auditListEl', empty:'auditEmpty', error:'auditError' };
    Object.entries(map).forEach(([k,id]) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.style.display = k === which ? (k === 'list' ? '' : 'flex') : 'none';
    });
    if (which === 'list') { const el = document.getElementById('auditListEl'); if (el) el.style.display = ''; }
  }

  /* ── Load ── */
  async function loadAudit() {
    showState('loading');
    try {
      const data = await ZP.get(ZP.API.audit + '?limit=200');
      _allEvents = Array.isArray(data) ? data : (data.events || data.items || []);
      renderAudit(applyFilters(_allEvents));
      ZP.markUpdated();
    } catch(err) {
      const errEl = document.getElementById('auditErrorMsg');
      if (errEl) errEl.textContent = 'Backend unavailable (' + (err.message||'error') + '). Showing demo events.';
      _allEvents = DEMO_EVENTS;
      renderAudit(applyFilters(_allEvents));
      showState('list');
      document.getElementById('auditError').style.display = 'flex';
    }
  }

  /* ── Filters wiring ── */
  function wireFilters() {
    const actorSel  = document.getElementById('filterActor');
    const actionSel = document.getElementById('filterAction');
    const riskSel   = document.getElementById('filterRisk');
    const clearBtn  = document.getElementById('btnClearFilters');

    [actorSel, actionSel, riskSel].forEach(sel => {
      if (!sel) return;
      sel.addEventListener('change', () => {
        _filters.actor  = actorSel  ? actorSel.value  : '';
        _filters.action = actionSel ? actionSel.value : '';
        _filters.risk   = riskSel   ? riskSel.value   : '';
        renderAudit(applyFilters(_allEvents));
      });
    });

    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        [actorSel, actionSel, riskSel].forEach(sel => { if (sel) sel.value = ''; });
        _filters = { actor:'', action:'', risk:'' };
        renderAudit(applyFilters(_allEvents));
      });
    }
  }

  /* ── Init ── */
  document.addEventListener('DOMContentLoaded', () => {
    wireFilters();
    loadAudit();
    const refresh = document.getElementById('btnRefreshAudit');
    if (refresh) refresh.addEventListener('click', loadAudit);
    const retry   = document.getElementById('btnRetryAudit');
    if (retry)   retry.addEventListener('click', loadAudit);

    // Auto-refresh every 30 seconds
    setInterval(loadAudit, 30000);

    // Socket.IO agent events → add to audit
    ZP.connectSocket(null, (ev) => {
      if (ev && ev.action) {
        _allEvents.push({ ts: new Date().toISOString(), actor: 'agent', action: ev.action, target: ev.target || '', risk: ev.risk || 'low', details: ev.details || '' });
        renderAudit(applyFilters(_allEvents));
      }
    }, null);
  });

})();