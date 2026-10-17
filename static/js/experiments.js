/**
 * Z-PHOENIX  experiments.js
 * Chaos Lab: scenario selection, fault start/stop, kill-switch,
 * progress/status UI. Never runs stress commands from the browser;
 * only POSTs requests to the backend.
 */
(function () {
  'use strict';

  const SCENARIO_LABELS = {
    cpu_spike:          'CPU Runaway Job',
    db2_lock_contention:'Db2 Lock Contention',
    memory_pressure:    'Memory Pressure',
  };

  let _faultActive   = false;
  let _pendingRequest = false;
  let _faultTimer    = null;
  let _faultStartTs  = null;
  let _pollTimer     = null;

  /* ── DOM refs ── */
  const btnStart      = document.getElementById('btnStartFault');
  const btnKill       = document.getElementById('btnKillSwitch');
  const btnReset      = document.getElementById('btnResetFault');
  const btnClearLog   = document.getElementById('btnClearLog');
  const statusPill    = document.getElementById('faultStatusPill');
  const statusContent = document.getElementById('faultStatusContent');
  const activeBanner  = document.getElementById('faultActiveBanner');
  const bannerText    = document.getElementById('faultActiveBannerText');
  const bannerTime    = document.getElementById('faultActiveTime');
  const faultLog      = document.getElementById('faultLog');
  const demoLabel     = document.getElementById('chaosDemoLabel');
  const durationSlider = document.getElementById('faultDuration');
  const durationDisplay = document.getElementById('durationDisplay');
  const selectedLabel = document.getElementById('selectedScenarioLabel');

  /* ── Scenario card selection ── */
  document.querySelectorAll('input[name="scenario"]').forEach(radio => {
    radio.addEventListener('change', () => {
      document.querySelectorAll('.scenario-card').forEach(c => c.classList.remove('selected'));
      if (radio.checked) {
        radio.closest('.scenario-card').classList.add('selected');
        selectedLabel.textContent = SCENARIO_LABELS[radio.value] || radio.value;
      }
    });
    if (radio.checked) {
      radio.closest('.scenario-card').classList.add('selected');
      selectedLabel.textContent = SCENARIO_LABELS[radio.value] || radio.value;
    }
  });

  /* ── Duration slider ── */
  if (durationSlider) {
    durationSlider.addEventListener('input', () => {
      durationDisplay.textContent = durationSlider.value;
    });
  }

  /* ── Log helpers ── */
  function appendLog(msg, cls) {
    if (!faultLog) return;
    const line = document.createElement('div');
    line.style.color = cls === 'error' ? 'var(--clr-critical)' : cls === 'success' ? 'var(--clr-healthy)' : cls === 'warn' ? 'var(--clr-warning)' : 'var(--clr-text-secondary)';
    line.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg;
    faultLog.appendChild(line);
    faultLog.scrollTop = faultLog.scrollHeight;
  }

  if (btnClearLog) {
    btnClearLog.addEventListener('click', () => {
      if (faultLog) faultLog.innerHTML = '<span style="color:var(--clr-text-muted);">— Log cleared —</span>';
    });
  }

  /* ── Set fault state ── */
  function setFaultActive(active, scenarioLabel, result) {
    _faultActive = active;
    if (statusPill) {
      statusPill.className = 'pill pill-no-dot ' + (active ? 'pill-critical' : 'pill-healthy');
      statusPill.textContent = active ? 'FAULT ACTIVE' : 'IDLE';
    }
    if (activeBanner) activeBanner.style.display = active ? 'flex' : 'none';
    if (active && bannerText) bannerText.textContent = '🔥 FAULT ACTIVE: ' + (scenarioLabel || 'Unknown');
    if (active && bannerTime) { _faultStartTs = new Date(); bannerTime.textContent = _faultStartTs.toLocaleTimeString(); }
    if (btnKill) btnKill.disabled = !active;
    if (btnStart) btnStart.disabled = active;

    if (!active && statusContent) {
      statusContent.innerHTML = result
        ? '<div class="agent-field" style="padding:var(--sp-4);"><div class="agent-field-label">Last Result</div><div class="agent-field-value text-healthy">✓ ' + ZP.escapeHtml(result) + '</div></div>'
        : '<div class="state-empty" style="padding:var(--sp-8) var(--sp-4);"><div class="state-icon">🟢</div><div class="state-title">System Healthy</div><div class="state-desc">No fault active. Ready for next scenario.</div></div>';
    } else if (active && statusContent) {
      statusContent.innerHTML =
        '<div style="padding:var(--sp-4);">' +
        '<div class="agent-field"><div class="agent-field-label">Scenario</div><div class="agent-field-value text-critical">' + ZP.escapeHtml(scenarioLabel || '') + '</div></div>' +
        '<div class="agent-field"><div class="agent-field-label">Started</div><div class="agent-field-value td-mono">' + (_faultStartTs ? _faultStartTs.toLocaleTimeString() : '—') + '</div></div>' +
        '<div class="agent-field"><div class="agent-field-label">Status</div><div class="agent-field-value"><span class="spinner spinner-sm spinner-ai" aria-hidden="true"></span> Running…</div></div>' +
        '</div>';
    }
  }

  /* ── Start Fault ── */
  if (btnStart) {
    btnStart.addEventListener('click', async () => {
      if (_pendingRequest || _faultActive) return;

      const scenarioRadio = document.querySelector('input[name="scenario"]:checked');
      const scenario   = scenarioRadio ? scenarioRadio.value : 'cpu_spike';
      const duration   = parseInt(durationSlider ? durationSlider.value : '30', 10);
      const intensity  = document.getElementById('faultIntensity') ? document.getElementById('faultIntensity').value : 'medium';
      const label      = SCENARIO_LABELS[scenario] || scenario;

      if (ZP.isDemoMode()) {
        // Demo mode — simulate without real request
        if (demoLabel) demoLabel.style.display = '';
        appendLog('DEMO MODE: Simulating fault start for ' + label, 'warn');
        appendLog('No real request sent to backend.', 'warn');
        setFaultActive(true, label);
        ZP.toast.warning('Demo Mode', 'Real fault not started — backend not connected.');
        ZP.setSystemState('incident');

        _faultTimer = setTimeout(() => {
          setFaultActive(false, null, 'Simulated fault completed (demo)');
          appendLog('DEMO: Fault simulation ended after ' + duration + 's', 'success');
          ZP.setSystemState('healthy');
          _faultTimer = null;
        }, duration * 1000);
        return;
      }

      _pendingRequest = true;
      ZP.setButtonLoading(btnStart, true, 'Starting…');
      appendLog('Sending fault request: ' + label + ' (' + duration + 's, ' + intensity + ')');

      try {
        const res = await ZP.post(ZP.API.attack, { scenario, duration, intensity });
        appendLog('Backend accepted: ' + (res.message || 'OK'), 'success');
        setFaultActive(true, label);
        ZP.toast.warning('Fault Started', label + ' fault is now active.');
        ZP.setSystemState('incident');

        // Poll for completion or wait for incident_update socket event
        _pollTimer = setInterval(async () => {
          try {
            const status = await ZP.get('/api/attack/status');
            if (status && status.active === false) {
              clearInterval(_pollTimer);
              setFaultActive(false, null, status.result || 'Fault completed');
              appendLog('Fault ended: ' + (status.result || 'completed'), 'success');
              ZP.setSystemState('healthy');
            }
          } catch(_) { /* ignore interim errors */ }
        }, 3000);

      } catch(err) {
        appendLog('Error: ' + (err.message || JSON.stringify(err)), 'error');
        ZP.toast.error('Fault Failed', err.message || 'Could not start fault. Check backend.');
      } finally {
        _pendingRequest = false;
        ZP.setButtonLoading(btnStart, false);
      }
    });
  }

  /* ── Kill Switch ── */
  if (btnKill) {
    btnKill.addEventListener('click', async () => {
      if (!_faultActive || _pendingRequest) return;

      if (!confirm('Stop the active fault now? This will terminate fault processes.')) return;

      _pendingRequest = true;
      ZP.setButtonLoading(btnKill, true, 'Stopping…');
      appendLog('Kill-switch activated — sending stop request.');

      try {
        if (ZP.isDemoMode()) {
          clearTimeout(_faultTimer); _faultTimer = null;
          clearInterval(_pollTimer);
          setFaultActive(false, null, 'Killed (demo)');
          appendLog('DEMO: Fault stopped via kill-switch.', 'success');
          ZP.setSystemState('healthy');
        } else {
          const res = await ZP.post('/api/attack/stop', {});
          appendLog('Kill confirmed: ' + (res.message || 'OK'), 'success');
          clearInterval(_pollTimer);
          setFaultActive(false, null, 'Killed by operator');
          ZP.toast.success('Fault Stopped', 'Kill-switch confirmed. Fault processes terminated.');
          ZP.setSystemState('recovering');
        }
      } catch(err) {
        appendLog('Kill error: ' + (err.message || 'Unknown'), 'error');
        ZP.toast.error('Kill Failed', err.message || 'Could not stop fault. Check backend.');
      } finally {
        _pendingRequest = false;
        ZP.setButtonLoading(btnKill, false);
      }
    });
  }

  /* ── Reset ── */
  if (btnReset) {
    btnReset.addEventListener('click', () => {
      if (_faultActive) { ZP.toast.warning('Cannot Reset', 'Stop the active fault first.'); return; }
      if (demoLabel) demoLabel.style.display = 'none';
      setFaultActive(false);
      if (faultLog) faultLog.innerHTML = '<span style="color:var(--clr-text-muted);">— Reset —</span>';
    });
  }

  /* ── Socket.IO for incident updates ── */
  ZP.connectSocket(null, null, (data) => {
    if (data && data.state === 'healthy' && _faultActive) {
      clearInterval(_pollTimer);
      setFaultActive(false, null, 'Backend confirmed recovery');
      appendLog('Incident resolved by backend.', 'success');
    }
  });

  /* ── Demo label on load ── */
  document.addEventListener('DOMContentLoaded', () => {
    if (ZP.isDemoMode() && demoLabel) demoLabel.style.display = '';
    appendLog('Chaos Lab ready. Sandbox limits enforced by backend.');
  });

})();