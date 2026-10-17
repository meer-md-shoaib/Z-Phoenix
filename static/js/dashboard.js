/**
 * Z-PHOENIX  dashboard.js
 * Command Center: KPI cards, live telemetry chart, incident banner,
 * event timeline, Golden Path stage tracker.
 *
 * Demo data is used automatically when the backend is unavailable.
 * All demo values are labelled DEMO DATA in the UI — never presented
 * as live mainframe readings.
 */
(function () {
  'use strict';

  /* ── Chart state (kept alive; never recreated on refresh) ── */
  let _chart = null;
  const MAX_POINTS = 60;   // 60 seconds of history
  const chartLabels = [];
  const cpuData     = [];
  const tpsData     = [];

  /* ── Polling timer ── */
  let _pollTimer   = null;
  let _staleTimer  = null;
  const POLL_MS    = 2000;   // fallback polling interval
  const STALE_MS   = 15000;  // warn stale after 15 s without data

  /* ── Demo data generator ── */
  let _demoTime    = 0;
  let _demoPhase   = 'baseline';  // baseline | incident | recovering | recovered

  function demoCpu() {
    switch (_demoPhase) {
      case 'incident':   return Math.min(98, 72 + Math.random() * 22);
      case 'recovering': return 60 - _demoTime * 4 + Math.random() * 8;
      case 'recovered':  return 22 + Math.random() * 8;
      default:           return 22 + Math.random() * 8;
    }
  }
  function demoTps() {
    switch (_demoPhase) {
      case 'incident':   return Math.max(5, 118 - 90 - Math.random() * 10);
      case 'recovering': return 20 + _demoTime * 15 + Math.random() * 10;
      case 'recovered':  return 110 + Math.random() * 20;
      default:           return 110 + Math.random() * 20;
    }
  }
  function demoRam() { return (1.8 + Math.random() * 0.6).toFixed(1); }
  function demoLocks() {
    switch (_demoPhase) {
      case 'incident':   return Math.round(250 + Math.random() * 80);
      case 'recovering': return Math.round(100 - _demoTime * 15);
      default:           return Math.round(5 + Math.random() * 10);
    }
  }

  const DEMO_EVENTS = [
    { type:'dot-healthy',  label:'Baseline established',             ts: -300 },
    { type:'dot-critical', label:'chaos.inject → CPU + Db2 spike',   ts: -180 },
    { type:'dot-warning',  label:'anomaly.detect → threshold breach', ts: -170 },
    { type:'dot-ai',       label:'diagnosis.done → runaway batch job',ts: -150 },
    { type:'dot-ai',       label:'recovery.proposed → kill -15 + throttle', ts:-130},
    { type:'dot-info',     label:'validation.pass → sandbox replay OK',ts:-110 },
    { type:'dot-approval', label:'human.approved → playbook saved',   ts: -90  },
    { type:'dot-healthy',  label:'recovery.complete → metrics normal', ts: -80 },
    { type:'dot-info',     label:'playbook.replay → 2s recovery',      ts: -30 },
  ];

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 1. Chart initialisation                                                  */
  /* ─────────────────────────────────────────────────────────────────────── */
  function initChart() {
    const ctx = document.getElementById('telemetryChart');
    if (!ctx || typeof Chart === 'undefined') return;

    _chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: chartLabels,
        datasets: [
          {
            label: 'CPU % (host)',
            data:  cpuData,
            borderColor:     '#ef4444',
            backgroundColor: 'rgba(239,68,68,0.08)',
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.35,
            yAxisID: 'yCpu',
            fill: true,
          },
          {
            label: 'TPS (simulated)',
            data:  tpsData,
            borderColor:     '#38bdf8',
            backgroundColor: 'rgba(56,189,248,0.06)',
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.35,
            yAxisID: 'yTps',
            fill: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend:  { display: false },
          tooltip: {
            backgroundColor: '#111827',
            borderColor: '#2d3748',
            borderWidth: 1,
            callbacks: {
              label: ctx => {
                const lbl = ctx.dataset.label || '';
                const val = ctx.parsed.y !== null ? ctx.parsed.y.toFixed(1) : '—';
                const suffix = lbl.includes('TPS') ? ' tx/s' : '%';
                return ' ' + lbl + ': ' + val + suffix;
              },
            },
          },
        },
        scales: {
          x: {
            ticks: { color: '#4b5563', maxTicksLimit: 8, font: { size: 10, family: 'JetBrains Mono' } },
            grid:  { color: '#1f2937' },
          },
          yCpu: {
            type: 'linear', position: 'left',
            min: 0, max: 100,
            ticks: { color: '#ef4444', callback: v => v + '%', font: { size: 10 } },
            grid:  { color: '#1f2937' },
          },
          yTps: {
            type: 'linear', position: 'right',
            min: 0, max: 200,
            ticks: { color: '#38bdf8', callback: v => v, font: { size: 10 } },
            grid:  { drawOnChartArea: false },
          },
        },
      },
    });
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 2. Push a data point to the chart                                        */
  /* ─────────────────────────────────────────────────────────────────────── */
  function pushChartPoint(cpu, tps, label) {
    if (!_chart) return;
    const ts = label || new Date().toLocaleTimeString();
    chartLabels.push(ts);
    cpuData.push(cpu);
    tpsData.push(tps);
    if (chartLabels.length > MAX_POINTS) {
      chartLabels.shift(); cpuData.shift(); tpsData.shift();
    }
    _chart.update('none');
    const tsEl = document.getElementById('chartLastTs');
    if (tsEl) { tsEl.textContent = 'Last point: ' + ts; tsEl.classList.remove('stale'); }
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 3. Update KPI cards                                                      */
  /* ─────────────────────────────────────────────────────────────────────── */
  function updateKpis(cpu, ram, ramTotal, tps, locks) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

    // CPU
    set('valCpu', typeof cpu === 'number' ? cpu.toFixed(1) : '—');
    const cpuCard = document.getElementById('kpiCpu');
    if (cpuCard) {
      cpuCard.className = 'kpi-card ' + (cpu > 80 ? 'kpi-critical' : cpu > 60 ? 'kpi-warning' : 'kpi-healthy');
    }
    set('metaCpu', typeof cpu === 'number' ? (cpu > 80 ? '⚠ High load' : cpu > 60 ? 'Moderate load' : 'Normal') : '—');

    // RAM
    const ramStr = (typeof ram === 'number' && typeof ramTotal === 'number')
      ? ram.toFixed(1) + '/' + ramTotal.toFixed(1)
      : (typeof ram === 'number' ? ram.toFixed(1) : '—');
    document.getElementById('valRam').innerHTML = ZP.escapeHtml(ramStr) + '<span class="kpi-unit">GB</span>';
    set('metaRam', typeof ram === 'number' ? ((ram / (ramTotal||4) * 100).toFixed(0) + '% used') : '—');

    // TPS
    set('valTps', typeof tps === 'number' ? Math.round(tps) : '—');
    const tpsCard = document.getElementById('kpiTps');
    if (tpsCard) {
      tpsCard.className = 'kpi-card ' + (tps < 20 ? 'kpi-critical' : tps < 60 ? 'kpi-warning' : 'kpi-ai');
    }
    set('metaTps', typeof tps === 'number' ? (tps < 20 ? '⚠ Very low — possible incident' : 'CICS workload simulation') : '—');

    // Locks
    set('valLocks', typeof locks === 'number' ? Math.round(locks) : '—');
    const lockCard = document.getElementById('kpiLocks');
    if (lockCard) {
      lockCard.className = 'kpi-card ' + (locks > 200 ? 'kpi-critical' : locks > 50 ? 'kpi-warning' : 'kpi-healthy');
    }
    set('metaLocks', typeof locks === 'number' ? (locks > 200 ? '⚠ High contention' : 'Db2 lock contention simulation') : '—');
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 4. Render event timeline                                                 */
  /* ─────────────────────────────────────────────────────────────────────── */
  function renderTimeline(events) {
    const container = document.getElementById('timelineContainer');
    const countEl   = document.getElementById('eventCount');
    if (!container) return;
    if (!events || events.length === 0) {
      container.innerHTML = '<div class="state-empty"><div class="state-icon">📭</div><div class="state-title">No events yet</div><div class="state-desc">Events will appear as the system runs.</div></div>';
      if (countEl) countEl.textContent = '0 events';
      return;
    }
    const recent = events.slice(-10).reverse();
    if (countEl) countEl.textContent = events.length + ' event' + (events.length !== 1 ? 's' : '');
    let html = '<div class="timeline">';
    recent.forEach(ev => {
      const dotCls = ev.type || 'dot-info';
      const label  = ZP.escapeHtml(ev.label || ev.action || ev.event || 'Event');
      const ts     = ev.ts ? ZP.fmt.ts(ev.ts) : (ev.seconds_ago !== undefined ? Math.abs(ev.seconds_ago) + 's ago' : '—');
      html += '<div class="timeline-item">' +
        '<div class="timeline-dot ' + dotCls + '" aria-hidden="true"></div>' +
        '<div class="timeline-content">' +
          '<div class="timeline-title">' + label + '</div>' +
          '<div class="timeline-ts">' + ZP.escapeHtml(ts) + '</div>' +
        '</div></div>';
    });
    html += '</div>';
    container.innerHTML = html;
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 5. Golden Path stage update                                              */
  /* ─────────────────────────────────────────────────────────────────────── */
  const STAGE_MAP = { baseline:1, chaos_inject:2, detect:3, diagnose:4, validate:5, approve:6, recover:7, replay:8 };

  function setGoldenPathStage(stageName) {
    const stageNum = STAGE_MAP[stageName] || 1;
    const stageEl = document.getElementById('goldenPathStage');
    if (stageEl) stageEl.textContent = stageName.replace(/_/g,' ').toUpperCase();
    for (let i = 1; i <= 8; i++) {
      const dot = document.getElementById('gp-dot-' + i);
      if (!dot) continue;
      if (i < stageNum)       { dot.style.opacity = '1'; dot.style.boxShadow = ''; }
      else if (i === stageNum){ dot.style.opacity = '1'; dot.style.boxShadow = '0 0 0 3px rgba(59,130,246,.4)'; }
      else                    { dot.style.opacity = '0.25'; dot.style.boxShadow = ''; }
    }
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 6. Incident banner                                                        */
  /* ─────────────────────────────────────────────────────────────────────── */
  function setIncidentBanner(active, text, ts) {
    const banner = document.getElementById('incidentBanner');
    if (!banner) return;
    banner.style.display = active ? 'flex' : 'none';
    if (active) {
      const textEl = document.getElementById('incidentBannerText');
      const timeEl = document.getElementById('incidentBannerTime');
      if (textEl) textEl.textContent = text || 'Incident detected.';
      if (timeEl) timeEl.textContent = ts ? ZP.fmt.ts(ts) : '';
    }
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 7. Apply a telemetry payload to the UI                                   */
  /* ─────────────────────────────────────────────────────────────────────── */
  function applyTelemetry(data, isDemo) {
    if (!data) return;
    const cpu   = data.cpu   !== undefined ? data.cpu   : null;
    const ram   = data.ram   !== undefined ? data.ram   : null;
    const total = data.ram_total !== undefined ? data.ram_total : 4;
    const tps   = data.tps   !== undefined ? data.tps   : null;
    const locks = data.db2_locks !== undefined ? data.db2_locks : null;

    updateKpis(cpu, ram, total, tps, locks);
    if (cpu !== null && tps !== null) {
      const label = new Date().toLocaleTimeString();
      pushChartPoint(cpu, tps, label);
    }

    // Demo label visibility
    const dl = document.getElementById('chartDemoLabel');
    if (dl) dl.style.display = isDemo ? '' : 'none';

    // System state
    const state = data.system_state || (cpu > 80 ? 'incident' : cpu > 60 ? 'degraded' : 'healthy');
    ZP.setSystemState(state);

    // Incident banner
    const hasIncident = state === 'incident' || state === 'degraded';
    setIncidentBanner(hasIncident, data.incident_text || 'Anomaly detected — CPU and Db2 lock threshold breached.', data.incident_ts);

    // Golden Path
    if (data.golden_path_stage) setGoldenPathStage(data.golden_path_stage);

    ZP.markUpdated();
    resetStaleTimer();
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 8. Stale-data detection                                                  */
  /* ─────────────────────────────────────────────────────────────────────── */
  function resetStaleTimer() {
    clearTimeout(_staleTimer);
    _staleTimer = setTimeout(() => {
      ZP.markStale();
      ZP.toast.warning('Data stale', 'No telemetry received in the last 15 seconds.');
    }, STALE_MS);
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 9. Demo data pump                                                        */
  /* ─────────────────────────────────────────────────────────────────────── */
  const demoEvents = DEMO_EVENTS.map(e => ({ ...e, ts: new Date(Date.now() + e.ts * 1000).toISOString() }));
  let _demoInterval = null;
  let _demoCycle = 0;

  function startDemoPump() {
    // Show initial demo data immediately
    applyTelemetry({
      cpu: 24, ram: 1.9, ram_total: 4, tps: 114, db2_locks: 7, system_state: 'healthy',
    }, true);
    renderTimeline(demoEvents);
    setGoldenPathStage('baseline');

    _demoInterval = setInterval(() => {
      _demoCycle++;
      // Phase transitions for demo storytelling
      if (_demoCycle === 12) { _demoPhase = 'incident'; ZP.setSystemState('incident'); setIncidentBanner(true,'⚠ INCIDENT: CPU spike and Db2 lock contention detected.'); setGoldenPathStage('chaos_inject'); }
      if (_demoCycle === 16) { ZP.setSystemState('incident'); setGoldenPathStage('detect'); }
      if (_demoCycle === 20) { ZP.setSystemState('recovering'); setGoldenPathStage('diagnose'); }
      if (_demoCycle === 24) { setGoldenPathStage('validate'); }
      if (_demoCycle === 28) { ZP.setSystemState('approval'); setGoldenPathStage('approve'); }
      if (_demoCycle === 32) { _demoPhase = 'recovering'; ZP.setSystemState('recovering'); setGoldenPathStage('recover'); _demoTime = 0; }
      if (_demoCycle === 38) { _demoPhase = 'recovered'; ZP.setSystemState('healthy'); setGoldenPathStage('replay'); setIncidentBanner(false); }
      if (_demoCycle === 50) { _demoCycle = 0; _demoPhase = 'baseline'; ZP.setSystemState('healthy'); setGoldenPathStage('baseline'); setIncidentBanner(false); }

      _demoTime++;
      const cpu   = Math.max(5, Math.min(99, demoCpu()));
      const tps   = Math.max(2, Math.min(180, demoTps()));
      const ram   = parseFloat(demoRam());
      const locks = Math.max(0, demoLocks());

      applyTelemetry({ cpu, ram, ram_total:4, tps, db2_locks: locks, system_state: _demoPhase === 'incident' ? 'incident' : _demoPhase === 'recovering' ? 'recovering' : 'healthy' }, true);
    }, POLL_MS);
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 10. Live polling (fallback when no Socket.IO)                             */
  /* ─────────────────────────────────────────────────────────────────────── */
  async function fetchTelemetry() {
    try {
      const data = await ZP.get(ZP.API.telemetry);
      applyTelemetry(data, false);
    } catch(err) {
      console.warn('Dashboard: telemetry fetch failed', err);
      ZP.markStale();
    }
  }

  async function fetchAuditEvents() {
    try {
      const data = await ZP.get(ZP.API.audit + '?limit=10');
      if (data && data.events) renderTimeline(data.events);
    } catch(_) { /* timeline stays as-is */ }
  }

  function startPolling() {
    fetchTelemetry();
    fetchAuditEvents();
    _pollTimer = setInterval(() => { fetchTelemetry(); }, POLL_MS);
    setInterval(fetchAuditEvents, 10000);
  }

  /* ─────────────────────────────────────────────────────────────────────── */
  /* 11. Init                                                                  */
  /* ─────────────────────────────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', async () => {
    initChart();

    // Manual refresh button
    const refreshBtn = document.getElementById('btnRefreshChart');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => {
        if (ZP.isDemoMode()) return;
        fetchTelemetry();
        ZP.toast.info('Refreshing', 'Requesting latest telemetry…');
      });
    }

    // Try real Socket.IO first
    const socket = ZP.connectSocket(
      // onTelemetry
      (data) => applyTelemetry(data, false),
      // onAgentEvent
      (data) => {
        if (data && data.stage) setGoldenPathStage(data.stage);
      },
      // onIncidentUpdate
      (data) => {
        if (data && data.state) ZP.setSystemState(data.state);
      }
    );

    if (socket) {
      // Socket connected; telemetry flows via events
      startPolling();   // also poll REST for audit events
    } else {
      // No Socket.IO — check if real backend is reachable
      const live = await ZP.checkHealth();
      if (live) {
        startPolling();
      } else {
        startDemoPump();
      }
    }
  });

})();