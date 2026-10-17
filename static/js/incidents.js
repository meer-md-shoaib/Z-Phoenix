/**
 * Z-PHOENIX  incidents.js
 * Incident list, detail panel, Recovery Passport rendering.
 * Never fabricates evidence or recovery success.
 */
(function () {
  'use strict';

  const DEMO_INCIDENTS = [
    { id:'INC-20261017-001', fault_type:'cpu_spike', status:'resolved',
      started_at:'2026-10-17T10:02:10Z', resolved_at:'2026-10-17T10:04:52Z',
      diagnosis:'Runaway batch job PID 4821 consuming 94% CPU + Db2 lock chain.',
      validation_result:'pass', approved_by:'operator', recovery_result:'success',
      playbook_id:'PB-001', recovery_passport_id:'RP-001',
      baseline:{ cpu:24, tps:118, db2_locks:6, ram:1.9 },
      during:  { cpu:94, tps:8,   db2_locks:312, ram:2.8 },
      after:   { cpu:22, tps:112, db2_locks:4,  ram:1.9 } },
    { id:'INC-20261017-002', fault_type:'db2_lock_contention', status:'resolved',
      started_at:'2026-10-17T11:30:00Z', resolved_at:'2026-10-17T11:32:20Z',
      diagnosis:'Db2 lock chain on DSNDB07, 201 lock waits, TPS collapse.',
      validation_result:'pass', approved_by:'operator', recovery_result:'success',
      playbook_id:'PB-002', recovery_passport_id:'RP-002',
      baseline:{ cpu:26, tps:115, db2_locks:8, ram:2.0 },
      during:  { cpu:68, tps:18,  db2_locks:201, ram:2.4 },
      after:   { cpu:24, tps:109, db2_locks:5,  ram:2.0 } },
  ];

  let _selectedId = null;

  function showListState(which) {
    const map = { loading:'incidentListLoading', list:'incidentListEl', empty:'incidentListEmpty', error:'incidentListError' };
    Object.entries(map).forEach(([k,id]) => {
      const el = document.getElementById(id);
      if (el) el.style.display = k === which ? (k === 'list' ? '' : 'flex') : 'none';
    });
    if (which === 'list') { const el = document.getElementById('incidentListEl'); if (el) el.style.display = ''; }
  }

  function renderList(incidents) {
    const el  = document.getElementById('incidentListEl');
    const cnt = document.getElementById('incidentCountPill');
    if (!el) return;
    if (cnt) { cnt.textContent = incidents.length + ' incident' + (incidents.length !== 1 ? 's' : ''); }
    if (!incidents.length) { showListState('empty'); return; }
    el.innerHTML = '';
    incidents.forEach(inc => {
      const card = document.createElement('div');
      card.className = 'incident-card' + (inc.status !== 'resolved' ? ' active-incident' : '');
      card.setAttribute('role', 'button');
      card.setAttribute('tabindex', '0');
      card.setAttribute('aria-label', 'Incident ' + inc.id + ' - ' + inc.fault_type);
      card.innerHTML =
        '<div style="flex:1;min-width:0;">' +
          '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">' +
            '<span class="td-mono">' + ZP.escapeHtml(inc.id) + '</span>' +
            ZP.fmt.statusPill(inc.status) +
          '</div>' +
          '<div style="font-weight:600;margin-bottom:4px;">' + ZP.escapeHtml((inc.fault_type||'').replace(/_/g,' ').toUpperCase()) + '</div>' +
          '<div class="text-xs text-muted">' + ZP.fmt.ts(inc.started_at) + ' → ' + (inc.resolved_at ? ZP.fmt.ts(inc.resolved_at) : 'Ongoing') + '</div>' +
        '</div>' +
        '<div>' + ZP.fmt.riskPill(inc.fault_type && inc.fault_type.includes('cpu') ? 'medium' : 'medium') + '</div>';
      card.addEventListener('click', () => selectIncident(inc));
      card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') selectIncident(inc); });
      el.appendChild(card);
    });
    showListState('list');
    if (!_selectedId && incidents.length > 0) {
      selectIncident(incidents[0]);
    }
  }

  function selectIncident(inc) {
    _selectedId = inc.id;
    const pill = document.getElementById('passportIdPill');
    if (pill) { pill.textContent = inc.recovery_passport_id || inc.id; pill.className = 'pill pill-info pill-no-dot'; }
    renderPassport(inc);
  }

  function renderPassport(inc) {
    const content = document.getElementById('passportContent');
    if (!content) return;

    const snapshotHtml = (label, data, cls) => {
      if (!data) return '<div class="snapshot-card"><div class="snapshot-label">' + label + '</div><div class="text-muted text-xs">No snapshot available</div></div>';
      const color = cls === 'during' ? 'var(--clr-critical)' : cls === 'after' ? 'var(--clr-healthy)' : 'var(--clr-info)';
      return '<div class="snapshot-card">' +
        '<div class="snapshot-label" style="color:' + color + ';">' + label + '</div>' +
        '<div class="snapshot-metric"><span>CPU</span><span class="snapshot-metric-val">' + (data.cpu !== undefined ? data.cpu + '%' : '—') + '</span></div>' +
        '<div class="snapshot-metric"><span>TPS (sim)</span><span class="snapshot-metric-val">' + (data.tps !== undefined ? data.tps + ' tx/s' : '—') + '</span></div>' +
        '<div class="snapshot-metric"><span>Db2 Locks (sim)</span><span class="snapshot-metric-val">' + (data.db2_locks !== undefined ? data.db2_locks : '—') + '</span></div>' +
        '<div class="snapshot-metric"><span>RAM</span><span class="snapshot-metric-val">' + (data.ram !== undefined ? data.ram + ' GB' : '—') + '</span></div>' +
      '</div>';
    };

    const lifeline =
      '<div class="timeline" style="padding:var(--sp-4);">' +
        (inc.started_at ? '<div class="timeline-item"><div class="timeline-dot dot-critical"></div><div class="timeline-content"><div class="timeline-title">Incident detected</div><div class="timeline-ts">' + ZP.fmt.ts(inc.started_at) + '</div></div></div>' : '') +
        (inc.diagnosis  ? '<div class="timeline-item"><div class="timeline-dot dot-ai"></div><div class="timeline-content"><div class="timeline-title">Diagnosed</div><div class="timeline-ts">' + ZP.escapeHtml(ZP.fmt.truncate(inc.diagnosis, 80)) + '</div></div></div>' : '') +
        (inc.validation_result ? '<div class="timeline-item"><div class="timeline-dot dot-' + (inc.validation_result === 'pass' ? 'info' : 'critical') + '"></div><div class="timeline-content"><div class="timeline-title">Validated: ' + (inc.validation_result.toUpperCase()) + '</div><div class="timeline-ts">Sandbox replay result</div></div></div>' : '') +
        (inc.approved_by ? '<div class="timeline-item"><div class="timeline-dot dot-approval"></div><div class="timeline-content"><div class="timeline-title">Approved by ' + ZP.escapeHtml(inc.approved_by) + '</div><div class="timeline-ts">Human-in-the-loop gate</div></div></div>' : '') +
        (inc.resolved_at ? '<div class="timeline-item"><div class="timeline-dot dot-healthy"></div><div class="timeline-content"><div class="timeline-title">Resolved</div><div class="timeline-ts">' + ZP.fmt.ts(inc.resolved_at) + '</div></div></div>' : '') +
      '</div>';

    content.innerHTML =
      '<div class="passport-header">' +
        '<div>' +
          '<div class="passport-id">' + ZP.escapeHtml(inc.recovery_passport_id || inc.id) + '</div>' +
          '<div style="font-size:var(--fs-sm);font-weight:600;">' + ZP.escapeHtml((inc.fault_type||'').replace(/_/g,' ').toUpperCase()) + '</div>' +
        '</div>' +
        ZP.fmt.statusPill(inc.status) +
        (inc.playbook_id ? '<span class="pill pill-ai pill-no-dot">Playbook: ' + ZP.escapeHtml(inc.playbook_id) + '</span>' : '') +
      '</div>' +
      '<div class="passport-snapshots">' +
        snapshotHtml('📊 BASELINE', inc.baseline, 'baseline') +
        snapshotHtml('🔥 DURING FAULT', inc.during, 'during') +
        snapshotHtml('✅ POST-RECOVERY', inc.after, 'after') +
      '</div>' +
      '<div style="padding:0 var(--sp-5) var(--sp-5);">' +
        '<div class="card-title mb-3">Lifecycle Timeline</div>' +
        lifeline +
      '</div>' +
      '<div class="card-footer"><span class="text-xs text-muted">TPS and Db2 lock values are SIMULATED workload data — not live mainframe production metrics.</span></div>';
  }

  async function loadIncidents() {
    showListState('loading');
    try {
      const data = await ZP.get(ZP.API.incidents + '?limit=20');
      const list = Array.isArray(data) ? data : (data.incidents || data.items || []);
      renderList(list);
      ZP.markUpdated();
    } catch(err) {
      const errEl = document.getElementById('incidentListErrorMsg');
      if (errEl) errEl.textContent = 'Backend unavailable (' + (err.message||'error') + '). Showing demo incidents.';
      renderList(DEMO_INCIDENTS);
      const errBox = document.getElementById('incidentListError');
      if (errBox) errBox.style.display = 'flex';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    loadIncidents();
    const refresh = document.getElementById('btnRefreshIncidents');
    if (refresh) refresh.addEventListener('click', loadIncidents);
    const retry   = document.getElementById('btnRetryIncidents');
    if (retry)   retry.addEventListener('click', loadIncidents);
  });

})();