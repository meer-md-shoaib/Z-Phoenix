/**
 * Z-PHOENIX  playbooks.js
 * Playbook Library: list, status, version, match score, replay action.
 */
(function () {
  'use strict';

  const DEMO_PLAYBOOKS = [
    { id:'PB-001', name:'CPU Runaway Job', version:3, trigger_signature:'cpu_spike', status:'APPROVED',
      validation_result:'pass', approved_by:'operator', created_at:'2026-10-17T10:15:00Z',
      match_score:0.97, steps_count:4, description:'Terminates runaway process and resets Db2 contention.' },
    { id:'PB-002', name:'Db2 Lock Contention', version:2, trigger_signature:'db2_lock_contention', status:'VALIDATED',
      validation_result:'pass', approved_by:null, created_at:'2026-10-17T11:42:00Z',
      match_score:0.82, steps_count:3, description:'Rolls back uncommitted transactions and reduces parallelism.' },
    { id:'PB-003', name:'Memory Pressure', version:1, trigger_signature:'memory_pressure', status:'DRAFT',
      validation_result:null, approved_by:null, created_at:'2026-10-17T13:00:00Z',
      match_score:0.61, steps_count:2, description:'Clears page cache and reduces workload simulator intensity.' },
  ];

  const STATUS_CLASSES = { DRAFT:'pill-muted', VALIDATED:'pill-info', APPROVED:'pill-approval', FAILED:'pill-critical' };

  let _replayInFlight = false;

  function statusClass(s) { return STATUS_CLASSES[(s||'').toUpperCase()] || 'pill-muted'; }

  function renderPlaybooks(list) {
    const container = document.getElementById('playbookList');
    if (!container) return;
    if (!list || list.length === 0) {
      showState('empty'); return;
    }
    container.innerHTML = '';
    list.forEach(pb => {
      const card = buildPlaybookCard(pb);
      container.appendChild(card);
    });
    showState('list');
  }

  function buildPlaybookCard(pb) {
    const canReplay = (pb.status === 'APPROVED' || pb.status === 'VALIDATED');
    const matchPct  = pb.match_score !== undefined ? Math.round(pb.match_score * 100) : null;

    const div = document.createElement('div');
    div.className = 'playbook-card';
    div.setAttribute('role', 'listitem');

    let headerRight = '<span class="pill pill-no-dot ' + statusClass(pb.status) + '">' + ZP.escapeHtml(pb.status || 'DRAFT') + '</span>';

    let matchHtml = '';
    if (matchPct !== null) {
      const mcls = matchPct >= 80 ? 'var(--clr-healthy)' : matchPct >= 50 ? 'var(--clr-warning)' : 'var(--clr-critical)';
      matchHtml = '<div class="match-score"><span style="color:var(--clr-text-muted);">Match:</span>' +
        '<div class="match-bar"><div class="match-bar-fill" style="width:' + matchPct + '%;"></div></div>' +
        '<span style="color:' + mcls + ';font-weight:600;">' + matchPct + '%</span></div>';
    }

    div.innerHTML =
      '<div class="playbook-card-header">' +
        '<div>' +
          '<div class="playbook-id">' + ZP.escapeHtml(pb.id || '—') + ' · v' + ZP.escapeHtml(String(pb.version || 1)) + '</div>' +
          '<div class="playbook-name">' + ZP.escapeHtml(pb.name || 'Untitled') + '</div>' +
        '</div>' +
        headerRight +
      '</div>' +
      '<div class="playbook-meta">' +
        '<span>Trigger: <strong>' + ZP.escapeHtml(pb.trigger_signature || '—') + '</strong></span>' +
        (pb.steps_count !== undefined ? '<span>Steps: ' + pb.steps_count + '</span>' : '') +
        '<span>Validation: ' + (pb.validation_result ? ZP.escapeHtml(pb.validation_result.toUpperCase()) : '—') + '</span>' +
        '<span>Approved by: ' + ZP.escapeHtml(pb.approved_by || 'Not yet') + '</span>' +
        '<span>Saved: ' + ZP.fmt.ts(pb.created_at) + '</span>' +
      '</div>' +
      (pb.description ? '<div class="text-secondary text-sm mb-3">' + ZP.escapeHtml(pb.description) + '</div>' : '') +
      matchHtml +
      '<div class="playbook-actions" style="margin-top:var(--sp-3);">' +
        '<button class="btn btn-ghost btn-sm" data-id="' + ZP.escapeHtml(pb.id) + '" data-action="view" aria-label="View playbook details for ' + ZP.escapeHtml(pb.name) + '">👁 View</button>' +
        (canReplay
          ? '<button class="btn btn-ai btn-sm" data-id="' + ZP.escapeHtml(pb.id) + '" data-action="replay" aria-label="Replay playbook ' + ZP.escapeHtml(pb.id) + '">⚡ Replay</button>'
          : '<button class="btn btn-ghost btn-sm" disabled title="Requires APPROVED or VALIDATED status">⚡ Replay</button>') +
      '</div>';

    // Wire action buttons
    div.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.id;
        if (btn.dataset.action === 'replay') { doReplay(id, pb); }
        else if (btn.dataset.action === 'view') { showPlaybookModal(pb); }
      });
    });
    return div;
  }

  function showPlaybookModal(pb) {
    const body =
      '<div class="agent-field"><div class="agent-field-label">ID / Version</div><div class="agent-field-value td-mono">' + ZP.escapeHtml(pb.id || '—') + ' · v' + (pb.version||1) + '</div></div>' +
      '<div class="agent-field"><div class="agent-field-label">Name</div><div class="agent-field-value">' + ZP.escapeHtml(pb.name||'—') + '</div></div>' +
      '<div class="agent-field"><div class="agent-field-label">Status</div><div class="agent-field-value">' + ZP.fmt.statusPill(pb.status) + '</div></div>' +
      '<div class="agent-field"><div class="agent-field-label">Trigger Signature</div><div class="agent-field-value td-mono">' + ZP.escapeHtml(pb.trigger_signature||'—') + '</div></div>' +
      '<div class="agent-field"><div class="agent-field-label">Validation</div><div class="agent-field-value">' + ZP.escapeHtml(pb.validation_result||'—') + '</div></div>' +
      '<div class="agent-field"><div class="agent-field-label">Approved By</div><div class="agent-field-value">' + ZP.escapeHtml(pb.approved_by||'Not yet approved') + '</div></div>' +
      '<div class="agent-field"><div class="agent-field-label">Created</div><div class="agent-field-value td-mono">' + ZP.fmt.ts(pb.created_at) + '</div></div>';
    ZP.openModal('Playbook: ' + (pb.name || pb.id), body);
  }

  async function doReplay(id, pb) {
    if (_replayInFlight) { ZP.toast.warning('Replay', 'A replay is already in progress.'); return; }
    const label = (pb && pb.name) ? pb.name : id;
    if (!confirm('Replay playbook "' + label + '"?\n\nReplay uses the validated playbook and still respects governance and approval rules.')) return;

    _replayInFlight = true;
    ZP.toast.ai('Replay Requested', 'Sending replay request for ' + label + '…');

    try {
      let data;
      if (ZP.isDemoMode()) {
        await new Promise(r => setTimeout(r, 800));
        data = { success: true, message: 'Replay simulated (demo). Playbook ' + id + ' v' + (pb.version||1) + ' applied in ~1.8s.' };
      } else {
        data = await ZP.post(ZP.API.replay, { playbook_id: id });
      }
      if (data.success || data.status === 'ok') {
        ZP.toast.success('Replay Complete', data.message || 'Playbook applied successfully.');
        ZP.setSystemState('healthy');
      } else {
        ZP.toast.error('Replay Failed', data.error || 'Unknown error.');
      }
    } catch(err) {
      ZP.toast.error('Replay Failed', err.message || 'Could not contact backend.');
    } finally {
      _replayInFlight = false;
    }
  }

  function showState(which) {
    const states = { loading:'playbookLoading', list:'playbookList', empty:'playbookEmpty', error:'playbookError' };
    Object.entries(states).forEach(([k, id]) => {
      const el = document.getElementById(id);
      if (el) el.style.display = k === which ? (k === 'list' ? '' : 'flex') : 'none';
    });
    if (which === 'list') {
      const listEl = document.getElementById('playbookList');
      if (listEl) listEl.style.display = '';
    }
  }

  async function loadPlaybooks() {
    showState('loading');
    try {
      const data = await ZP.get(ZP.API.playbooks);
      const list = Array.isArray(data) ? data : (data.playbooks || data.items || []);
      renderPlaybooks(list);
      ZP.markUpdated();
    } catch(err) {
      // Show demo data with error notice
      const errEl = document.getElementById('playbookErrorMsg');
      if (errEl) errEl.textContent = 'Backend unavailable (' + (err.message||'error') + '). Showing demo playbooks.';
      showState('error');
      // Also render demo data below the error
      const listEl = document.getElementById('playbookList');
      if (listEl) {
        listEl.style.display = '';
        renderPlaybooks(DEMO_PLAYBOOKS);
        listEl.style.display = '';
        const loadEl = document.getElementById('playbookLoading');
        if (loadEl) loadEl.style.display = 'none';
        const errStateEl = document.getElementById('playbookError');
        if (errStateEl) errStateEl.style.display = 'flex';
      }
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    loadPlaybooks();
    const btn = document.getElementById('btnRefreshPlaybooks');
    if (btn) btn.addEventListener('click', loadPlaybooks);
    const retry = document.getElementById('btnRetryPlaybooks');
    if (retry) retry.addEventListener('click', loadPlaybooks);
  });

})();