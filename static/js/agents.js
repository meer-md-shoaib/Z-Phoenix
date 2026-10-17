/**
 * Z-PHOENIX  agents.js
 * AI Agent Console: diagnosis → recovery → validate → approve → recover.
 * Gate controls: recovery is disabled until validation passes and explicit
 * human approval is recorded. Backend enforces authorization; UI gating
 * is for usability only.
 */
(function () {
  'use strict';

  /* ── Workflow state machine ── */
  const STATE = { IDLE:0, DIAGNOSING:1, DIAGNOSED:2, VALIDATING:3, VALIDATED:4, APPROVED:5, RECOVERING:6, DONE:7 };
  let _state = STATE.IDLE;
  let _incidentId = null;
  let _proposal   = null;  // last recovery proposal from backend
  let _inFlight   = false;

  /* ── DOM refs ── */
  const btnAnalyze   = document.getElementById('btnAnalyze');
  const btnValidate  = document.getElementById('btnValidate');
  const btnApprove   = document.getElementById('btnApprove');
  const btnReject    = document.getElementById('btnReject');
  const btnRecover   = document.getElementById('btnRecover');
  const agentPill    = document.getElementById('agentStatusPill');
  const fallbackEl   = document.getElementById('fallbackNotice');
  const demoEl       = document.getElementById('demoNotice');

  function section(id) { return document.getElementById('sectionDiagnosis,sectionRecovery,sectionValidation,approvalControls,sectionRecover'.split(',').find(s=>s===id) || id); }

  function setSection(id, enabled) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.opacity = enabled ? '1' : '0.4';
    el.style.pointerEvents = enabled ? '' : 'none';
  }

  function setAgentPill(text, cls) {
    if (agentPill) { agentPill.className = 'pill pill-no-dot ' + (cls || 'pill-ai'); agentPill.textContent = text; }
  }

  /* ── Render helpers ── */
  function renderDiagnosis(data) {
    const body = document.getElementById('diagnosisBody');
    const pill = document.getElementById('diagnosisPill');
    if (!body) return;
    if (data && data.is_fallback && fallbackEl) fallbackEl.style.display = '';
    if (pill) { pill.className = 'pill pill-ai pill-no-dot'; pill.textContent = 'COMPLETE'; }

    const confidence = data.confidence_score !== undefined ? data.confidence_score : null;
    const confClass  = confidence >= 0.7 ? 'high' : confidence >= 0.4 ? 'medium' : 'low';
    const confPct    = confidence !== null ? Math.round(confidence * 100) : null;

    let html = '<div class="agent-field"><div class="agent-field-label">Root Cause Hypothesis</div>' +
      '<div class="agent-field-value text-ai">' + ZP.escapeHtml(data.root_cause || '—') + '</div></div>';

    if (data.evidence) {
      html += '<div class="agent-field"><div class="agent-field-label">Supporting Evidence</div>' +
        '<div class="agent-field-value text-mono">' + ZP.escapeHtml(data.evidence) + '</div></div>';
    }

    if (confPct !== null) {
      html += '<div class="agent-field"><div class="agent-field-label">Confidence</div>' +
        '<div class="confidence-bar" role="meter" aria-valuenow="' + confPct + '" aria-valuemin="0" aria-valuemax="100" aria-label="Confidence ' + confPct + '%">' +
        '<div class="confidence-track"><div class="confidence-fill ' + confClass + '" style="width:' + confPct + '%;"></div></div>' +
        '<div class="confidence-value text-' + confClass.replace('high','healthy') + '">' + confPct + '%</div></div></div>';
    }

    if (data.risk_level) {
      html += '<div class="agent-field"><div class="agent-field-label">Risk Level</div><div class="agent-field-value">' + ZP.fmt.riskPill(data.risk_level) + '</div></div>';
    }
    if (data.blast_radius) {
      html += '<div class="agent-field"><div class="agent-field-label">Estimated Blast Radius</div>' +
        '<div class="agent-field-value">' + ZP.escapeHtml(data.blast_radius) + '</div></div>';
    }
    if (data.is_fallback) {
      html += '<div class="fallback-notice" style="display:inline-flex;">⚠ Deterministic fallback — Granite API unavailable</div>';
    }
    body.innerHTML = html;
  }

  function renderRecovery(data) {
    const body = document.getElementById('recoveryBody');
    const pill = document.getElementById('recoveryPill');
    if (!body) return;
    if (pill) { pill.className = 'pill pill-ai pill-no-dot'; pill.textContent = 'PROPOSED'; }

    let html = '';
    const steps = data.recovery_steps || data.steps || [];
    if (steps.length > 0) {
      steps.forEach((step, i) => {
        const action = typeof step === 'string' ? step : (step.action || step.command || JSON.stringify(step));
        const desc   = typeof step === 'object' ? (step.description || step.desc || '') : '';
        html += '<div class="recovery-step">' +
          '<div class="step-num" aria-label="Step ' + (i+1) + '">' + (i+1) + '</div>' +
          '<div class="step-content"><div class="step-action">' + ZP.escapeHtml(action) + '</div>' +
          (desc ? '<div class="step-desc">' + ZP.escapeHtml(desc) + '</div>' : '') +
          '</div></div>';
      });
    } else {
      html = '<div class="agent-field-value text-muted">No recovery steps returned.</div>';
    }

    if (data.rollback_conditions) {
      html += '<div class="agent-field" style="margin-top:var(--sp-4);"><div class="agent-field-label">Rollback Conditions</div>' +
        '<div class="agent-field-value text-mono">' + ZP.escapeHtml(data.rollback_conditions) + '</div></div>';
    }
    if (data.allowlisted_action_ids && data.allowlisted_action_ids.length > 0) {
      html += '<div class="agent-field"><div class="agent-field-label">Allowlisted Action IDs</div>' +
        '<div class="agent-field-value text-mono">' + ZP.escapeHtml(data.allowlisted_action_ids.join(', ')) + '</div></div>';
    }
    body.innerHTML = html;
  }

  function renderValidation(data) {
    const body = document.getElementById('validationBody');
    const pill = document.getElementById('validationPill');
    if (!body) return;
    const passed = data.result === 'pass' || data.passed === true;
    if (pill) {
      pill.className = 'pill pill-no-dot ' + (passed ? 'pill-healthy' : 'pill-critical');
      pill.textContent = passed ? 'PASS' : 'FAIL';
    }
    let html = '<div class="agent-field"><div class="agent-field-label">Sandbox Replay Result</div>' +
      '<div class="agent-field-value ' + (passed ? 'text-healthy' : 'text-critical') + '">' +
      (passed ? '✓ VALIDATION PASSED — safe to proceed to approval' : '✕ VALIDATION FAILED — recovery not allowed') +
      '</div></div>';
    if (data.details) {
      html += '<div class="agent-field"><div class="agent-field-label">Details</div>' +
        '<div class="agent-field-value text-mono">' + ZP.escapeHtml(data.details) + '</div></div>';
    }
    body.innerHTML = html;
    return passed;
  }

  function renderRecoveryResult(data) {
    const body = document.getElementById('recoverBody');
    const pill = document.getElementById('recoverPill');
    const ok   = data.success || data.status === 'ok';
    if (pill) { pill.className = 'pill pill-no-dot ' + (ok ? 'pill-healthy' : 'pill-critical'); pill.textContent = ok ? 'COMPLETE' : 'FAILED'; }
    if (body) {
      body.innerHTML = '<div class="agent-field" style="padding:var(--sp-4);"><div class="agent-field-label">Result</div>' +
        '<div class="agent-field-value ' + (ok ? 'text-healthy' : 'text-critical') + '">' +
        (ok ? '✓ Recovery executed successfully. Telemetry should normalise.' : '✕ Recovery failed: ' + ZP.escapeHtml(data.error || 'Unknown error')) +
        '</div></div>';
    }
    if (ok) { ZP.setSystemState('healthy'); ZP.toast.success('Recovery Complete', 'System returning to baseline.'); }
    else     { ZP.toast.error('Recovery Failed', data.error || 'Unknown error.'); }
  }

  /* ── Demo data ── */
  const DEMO_DIAGNOSIS = {
    root_cause: 'Runaway batch job (PID 4821) consuming 94% CPU, creating Db2 lock chain on DSNDB07.SYSLGRNX, cascading to 312 lock waits.',
    evidence:   'CPU 94% for 47s | TPS dropped from 118 to 8 | Db2 locks 312 (threshold 50) | Audit event chaos.inject at 15:02:10',
    confidence_score: 0.91,
    risk_level: 'medium',
    blast_radius: 'Single runaway process + 5 Db2 transactions; host stable.',
    is_fallback: false,
    incident_id: 'INC-20261017-001',
  };
  const DEMO_PROPOSAL = {
    incident_id: 'INC-20261017-001',
    recovery_steps: [
      { action: 'kill -15 4821', description: 'Gracefully terminate runaway batch job (SIGTERM).' },
      { action: 'SET CURRENT DEGREE = 1', description: 'Reduce Db2 query parallelism to release lock contention.' },
      { action: 'ROLLBACK', description: 'Roll back uncommitted transactions holding Db2 locks.' },
      { action: 'systemctl restart cics-workload-sim', description: 'Restart CICS workload simulator at baseline TPS.' },
    ],
    rollback_conditions: 'If CPU does not drop below 40% within 30s, revert to checkpoint.',
    allowlisted_action_ids: ['kill-15', 'db2-rollback', 'cics-restart'],
    risk_level: 'medium',
  };
  const DEMO_VALIDATION = { result: 'pass', passed: true, details: 'Sandbox replay: CPU normalised to 23% within 18s. TPS recovered to 112. Db2 locks cleared.' };
  const DEMO_RECOVERY   = { success: true, message: 'Recovery executed. CPU: 23%, TPS: 112, Db2 locks: 4. Playbook PB-004 saved.' };

  /* ── Main flow ── */
  async function doAnalyze() {
    if (_inFlight) return;
    _inFlight = true;
    _state = STATE.DIAGNOSING;
    setAgentPill('DIAGNOSING…', 'pill-ai');
    ZP.setButtonLoading(btnAnalyze, true, 'Diagnosing…');
    setSection('sectionDiagnosis', true);

    const diagBody = document.getElementById('diagnosisBody');
    if (diagBody) diagBody.innerHTML = '<div class="state-loading"><span class="spinner spinner-ai"></span><span class="state-title">IBM Granite reasoning over telemetry…</span></div>';

    try {
      let data;
      if (ZP.isDemoMode()) {
        if (demoEl) demoEl.style.display = '';
        await new Promise(r => setTimeout(r, 2200));
        data = DEMO_DIAGNOSIS;
      } else {
        data = await ZP.post(ZP.API.analyze, { incident_id: _incidentId });
      }
      _proposal = data;
      _state = STATE.DIAGNOSED;
      renderDiagnosis(data);
      setAgentPill('DIAGNOSED', 'pill-ai');
      ZP.toast.ai('Diagnosis Complete', 'Root cause identified. Review and proceed to validation.');
      ZP.setSystemState('recovering');

      // Show recovery section
      setSection('sectionRecovery', true);
      renderRecovery(data);

      // Enable validate button
      setSection('sectionValidation', true);
      const vf = document.getElementById('validateFooter');
      if (vf) vf.style.display = 'flex';
      if (btnValidate) btnValidate.disabled = false;

    } catch(err) {
      if (diagBody) diagBody.innerHTML = '<div class="state-error"><div class="state-icon">✕</div><div class="state-title">Diagnosis Failed</div><div class="state-desc">' + ZP.escapeHtml(err.message || 'Unknown error') + '</div></div>';
      setAgentPill('ERROR', 'pill-critical');
      ZP.toast.error('Diagnosis Failed', err.message || 'Could not reach backend.');
    } finally {
      _inFlight = false;
      ZP.setButtonLoading(btnAnalyze, false);
    }
  }

  async function doValidate() {
    if (_inFlight || _state < STATE.DIAGNOSED) return;
    _inFlight = true;
    _state = STATE.VALIDATING;
    setAgentPill('VALIDATING…', 'pill-ai');
    ZP.setButtonLoading(btnValidate, true, 'Running sandbox…');

    const valBody = document.getElementById('validationBody');
    if (valBody) valBody.innerHTML = '<div class="state-loading"><span class="spinner spinner-ai"></span><span class="state-title">Running sandbox replay…</span></div>';

    try {
      let data;
      if (ZP.isDemoMode()) {
        await new Promise(r => setTimeout(r, 2800));
        data = DEMO_VALIDATION;
      } else {
        data = await ZP.post(ZP.API.validate, { incident_id: _incidentId, proposal: _proposal });
      }
      const passed = renderValidation(data);
      _state = STATE.VALIDATED;
      setAgentPill('VALIDATED', passed ? 'pill-healthy' : 'pill-critical');

      if (passed) {
        ZP.toast.success('Validation Passed', 'Sandbox replay successful. Awaiting human approval.');
        // Show approval gate
        const ac = document.getElementById('approvalControls');
        if (ac) ac.style.display = 'flex';
        if (btnApprove) btnApprove.disabled = false;
        if (btnReject)  btnReject.disabled  = false;
      } else {
        ZP.toast.error('Validation Failed', 'Sandbox replay failed — recovery blocked.');
      }
    } catch(err) {
      if (valBody) valBody.innerHTML = '<div class="state-error"><div class="state-icon">✕</div><div class="state-title">Validation Error</div><div class="state-desc">' + ZP.escapeHtml(err.message) + '</div></div>';
      ZP.toast.error('Validation Error', err.message);
    } finally {
      _inFlight = false;
      ZP.setButtonLoading(btnValidate, false);
    }
  }

  async function doApprove() {
    if (_inFlight || _state < STATE.VALIDATED) return;
    if (!confirm('Approve this recovery plan? This will enable recovery execution.\n\nOnly approved, allowlisted actions will be executed.')) return;
    _inFlight = true;
    ZP.setButtonLoading(btnApprove, true, 'Recording approval…');

    try {
      if (!ZP.isDemoMode()) {
        await ZP.post(ZP.API.approve, { incident_id: _incidentId, approved: true });
      }
      _state = STATE.APPROVED;
      ZP.setSystemState('approval');
      setAgentPill('APPROVED', 'pill-approval');
      ZP.toast.ai('Approved', 'Human approval recorded. Recovery execution is now enabled.');
      // Enable recovery section
      setSection('sectionRecover', true);
      const rf = document.getElementById('recoverFooter');
      if (rf) { rf.style.removeProperty('display'); }
      if (btnRecover) btnRecover.disabled = false;
      if (btnApprove) btnApprove.disabled = true;
      if (btnReject)  btnReject.disabled  = true;
    } catch(err) {
      ZP.toast.error('Approval Failed', err.message);
    } finally {
      _inFlight = false;
      ZP.setButtonLoading(btnApprove, false);
    }
  }

  function doReject() {
    if (_state < STATE.VALIDATED) return;
    if (!confirm('Reject this recovery proposal?')) return;
    _state = STATE.DIAGNOSED;
    setAgentPill('REJECTED', 'pill-critical');
    const ac = document.getElementById('approvalControls');
    if (ac) ac.style.display = 'none';
    ZP.toast.warning('Rejected', 'Recovery proposal rejected. Re-diagnose or adjust the plan.');
    ZP.setSystemState('incident');
  }

  async function doRecover() {
    if (_inFlight || _state < STATE.APPROVED) return;
    if (!confirm('Execute recovery now? The Governance Engine will run only allowlisted actions.')) return;
    _inFlight = true;
    _state = STATE.RECOVERING;
    setAgentPill('RECOVERING…', 'pill-ai');
    ZP.setButtonLoading(btnRecover, true, 'Executing…');

    const rb = document.getElementById('recoverBody');
    if (rb) rb.innerHTML = '<div class="state-loading"><span class="spinner spinner-ai"></span><span class="state-title">Executing allowlisted recovery actions…</span></div>';

    try {
      let data;
      if (ZP.isDemoMode()) {
        await new Promise(r => setTimeout(r, 2000));
        data = DEMO_RECOVERY;
      } else {
        data = await ZP.post(ZP.API.recover, { incident_id: _incidentId });
      }
      renderRecoveryResult(data);
      _state = STATE.DONE;
      setAgentPill('DONE', 'pill-healthy');
    } catch(err) {
      renderRecoveryResult({ success: false, error: err.message });
      _state = STATE.APPROVED;
    } finally {
      _inFlight = false;
      ZP.setButtonLoading(btnRecover, false);
    }
  }

  /* ── Wire buttons ── */
  if (btnAnalyze) btnAnalyze.addEventListener('click', doAnalyze);
  if (btnValidate) btnValidate.addEventListener('click', doValidate);
  if (btnApprove)  btnApprove.addEventListener('click', doApprove);
  if (btnReject)   btnReject.addEventListener('click', doReject);
  if (btnRecover)  btnRecover.addEventListener('click', doRecover);

  /* ── Socket.IO agent events ── */
  ZP.connectSocket(null, (ev) => {
    if (!ev) return;
    if (ev.type === 'diagnosis' && _state < STATE.DIAGNOSED) {
      renderDiagnosis(ev.data || ev);
      _state = STATE.DIAGNOSED;
      setAgentPill('DIAGNOSED', 'pill-ai');
    }
  }, null);

  /* ── On load ── */
  document.addEventListener('DOMContentLoaded', () => {
    if (ZP.isDemoMode() && demoEl) demoEl.style.display = '';
  });

})();