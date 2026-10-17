/*
 * Z-PHOENIX :: app.js
 * Console behaviour: KPI cards, incident banner, golden-path stepper,
 * AI agent console, [APPROVE]/[REJECT] gate, chaos lab controls,
 * playbook replay buttons and the live audit table.
 * Every feature is feature-detected so this file can be loaded on all pages.
 */
(function () {
  "use strict";

  var Z = window.ZPhoenix;
  var pendingProposal = null;
  var lastValidation = null;
  var approvalContext = null;

  function approvalToken() {
    var input = $("approvalToken");
    return input ? input.value.trim() : "";
  }

  function $(id) { return document.getElementById(id); }

  function setText(id, value) {
    var node = $(id);
    if (node) node.textContent = value;
  }

  function pretty(obj) {
    try { return JSON.stringify(obj, null, 2); } catch (e) { return String(obj); }
  }

  function handleError(err, prefix) {
    var message = (prefix || "Request failed") + ": " + (err && err.message ? err.message : err);
    Z.toast(message, err && err.status === 403 ? "error" : "warn");
    logLine(err && err.status === 403 ? "error" : "warn", message);
  }

  /* --------------------------------------------------------- console log */
  function logLine(level, message, ts) {
    var host = $("consoleLog");
    if (!host) return;
    var line = document.createElement("div");
    line.className = "console-line " + (level || "");
    var stamp = document.createElement("span");
    stamp.className = "ts";
    stamp.textContent = "[" + Z.clockString(ts) + "]";
    var msg = document.createElement("span");
    msg.className = "msg";
    msg.textContent = message;
    line.appendChild(stamp);
    line.appendChild(msg);
    host.appendChild(line);
    while (host.childNodes.length > 160) host.removeChild(host.firstChild);
    host.scrollTop = host.scrollHeight;
  }

  Z.on("console", function (entry) {
    logLine(entry.level, entry.message, entry.ts);
  });

  /* ---------------------------------------------------------- KPI cards */
  function healthClass(health) {
    if (health === "CRITICAL") return "crit";
    if (health === "DEGRADED") return "warn";
    return "ok";
  }

  function kpiAccent(health) {
    if (health === "CRITICAL") return "#EF4444";
    if (health === "DEGRADED") return "#F59E0B";
    return "#10B981";
  }

  function renderKpis(sample) {
    var cards = [
      { id: "kpiCpu", state: "kpiCpuState", dot: "kpiCpuDot", bar: "kpiCpuBar",
        value: sample.cpu, suffix: "%", digits: 1,
        health: sample.cpu >= 90 ? "CRITICAL" : (sample.cpu >= 60 ? "DEGRADED" : "HEALTHY"),
        max: 100, accent: null },
      { id: "kpiRam", state: "kpiRamState", dot: "kpiRamDot", bar: "kpiRamBar",
        value: sample.ram_gb, suffix: " GB", digits: 2,
        health: sample.ram_gb >= 8 ? "DEGRADED" : "HEALTHY",
        max: 16, accent: null },
      { id: "kpiTps", state: "kpiTpsState", dot: "kpiTpsDot", bar: "kpiTpsBar",
        value: sample.tps, suffix: " txn/s", digits: 0,
        health: sample.tps <= 150 ? "CRITICAL" : (sample.tps <= 600 ? "DEGRADED" : "HEALTHY"),
        max: 1200, accent: "#06B6D4" },
      { id: "kpiLocks", state: "kpiLocksState", dot: "kpiLocksDot", bar: "kpiLocksBar",
        value: sample.locks, suffix: " waits", digits: 0,
        health: sample.locks >= 300 ? "CRITICAL" : (sample.locks >= 50 ? "DEGRADED" : "HEALTHY"),
        max: 520, accent: "#F59E0B" }
    ];

    cards.forEach(function (card) {
      var node = $(card.id);
      if (!node) return;
      setText(card.id, Z.fmt(card.value, card.digits) + card.suffix);
      setText(card.state, card.health);
      var dot = $(card.dot);
      if (dot) dot.className = "dot " + healthClass(card.health);
      var bar = $(card.bar);
      if (bar) bar.style.width = Math.max(2, Math.min(100, (card.value / card.max) * 100)) + "%";
      var accent = card.accent || kpiAccent(card.health);
      node.closest(".kpi").style.setProperty("--accent", accent);
    });

    var sysBadge = $("sysBadge");
    if (sysBadge) {
      sysBadge.textContent = "SYSTEM " + sample.health;
      sysBadge.className = "badge " + (sample.health === "CRITICAL" ? "badge-crit"
        : sample.health === "DEGRADED" ? "badge-warn" : "badge-ok");
    }
    setText("topbarFault", sample.chaos_active ? ("FAULT: " + sample.fault) : "NO ACTIVE FAULT");
  }

  Z.on("telemetry", function (payload) {
    var sample = payload && payload.telemetry;
    if (sample) renderKpis(sample);
    if (payload && payload.incident) renderIncident(payload.incident);
    else if (payload && payload.stages && !payload.incident) hideIncident();
    if (payload && payload.stages) renderStages(payload.stages);
    updateLabStatus(payload && payload.chaos, sample);
  });

  /* ------------------------------------------------------ incident banner */
  function renderIncident(incident) {
    var banner = $("incidentBanner");
    if (!banner) return;
    banner.classList.remove("hidden");
    setText("incidentText",
      "FAULT " + incident.fault_type + " — " + (incident.reasons || []).join(" · "));
    setText("incidentId", "incident#" + incident.id + " · " + incident.severity);
  }

  function hideIncident() {
    var banner = $("incidentBanner");
    if (banner) banner.classList.add("hidden");
  }

  Z.on("incident", function (incident) {
    renderIncident(incident);
    logLine("critical", "Incident #" + incident.id + " opened: " + incident.fault_type);
  });

  Z.on("incident_cleared", function (payload) {
    hideIncident();
    logLine("ok", "Incident #" + (payload && payload.id) + " cleared.");
  });

  /* -------------------------------------------------------------- stepper */
  function renderStages(flow) {
    var host = $("stepper");
    if (!host || !flow || !flow.stages) return;
    host.innerHTML = "";
    flow.stages.forEach(function (stage) {
      var li = document.createElement("li");
      li.className = "step " + (stage.status || "pending");
      var idx = document.createElement("span");
      idx.className = "step-index";
      idx.textContent = stage.id;
      var body = document.createElement("div");
      var title = document.createElement("div");
      title.className = "step-title";
      title.textContent = stage.id + ". " + stage.title;
      var detail = document.createElement("div");
      detail.className = "step-detail";
      detail.textContent = stage.detail || "";
      body.appendChild(title);
      body.appendChild(detail);
      li.appendChild(idx);
      li.appendChild(body);
      host.appendChild(li);
    });
    setText("flowMessage", flow.message || "");
    setText("pipelineState", (flow.pipeline || "idle").toUpperCase());
  }

  Z.on("stages", renderStages);

  /* ---------------------------------------------------- AI agent console */
  Z.on("agent", function (payload) {
    if (!payload) return;
    var body = payload.payload || {};
    if (payload.stage === "diagnosis") {
      renderDiagnosis(body);
    } else if (payload.stage === "proposal") {
      pendingProposal = body;
      renderProposal(body);
    } else if (payload.stage === "validation") {
      lastValidation = body;
      renderValidation(body);
    }
  });

  function setAgentEmpty(blockId, outId, text) {
    var out = $(outId);
    if (out) out.textContent = text;
    var block = $(blockId);
    if (block) block.classList.remove("hidden");
  }

  function renderDiagnosis(diag) {
    var out = $("diagOut");
    if (!out) return;
    setText("diagSource", (diag.source || "unknown").toUpperCase());
    setText("diagConfidence", "CONF " + Math.round((diag.confidence || 0) * 100) + "%");
    setText("diagRootCause", diag.root_cause || "");
    setText("diagEvidence", (diag.evidence || []).map(function (e) { return "• " + e; }).join("\n"));
    out.textContent = pretty({
      fault_type: diag.fault_type,
      root_cause: diag.root_cause,
      confidence: diag.confidence,
      risk: diag.risk,
      evidence: diag.evidence,
      recommendation: diag.recommendation,
      source: diag.source,
      reason: diag.reason
    });
  }

  function renderProposal(prop) {
    var out = $("propOut");
    if (!out) return;
    setText("propTitle", prop.title || "");
    var riskBadge = $("propRisk");
    if (riskBadge) {
      riskBadge.textContent = "RISK " + (prop.risk_tier || "?");
      riskBadge.className = "badge " + (prop.risk_tier === "HIGH" ? "badge-crit"
        : prop.risk_tier === "MEDIUM" ? "badge-warn" : "badge-ok");
    }
    var list = $("propSteps");
    if (list) {
      list.innerHTML = "";
      (prop.steps || []).forEach(function (step) {
        var li = document.createElement("li");
        var code = document.createElement("code");
        code.textContent = step.command;
        var label = document.createElement("span");
        label.textContent = step.title;
        li.appendChild(code);
        li.appendChild(label);
        list.appendChild(li);
      });
    }
    out.textContent = pretty({
      title: prop.title,
      fault_type: prop.fault_type,
      risk_tier: prop.risk_tier,
      rationale: prop.rationale,
      steps: prop.steps,
      estimated_duration_sec: prop.estimated_duration_sec,
      source: prop.source
    });
  }

  function renderValidation(result) {
    var out = $("valOut");
    if (!out) return;
    var verdict = result.result || "?";
    var badge = $("valBadge");
    if (badge) {
      badge.textContent = verdict;
      badge.className = "badge " + (verdict === "PASS" ? "badge-ok" : "badge-crit");
    }
    setText("valSummary", (result.commands_checked || 0) + " commands checked · "
      + (result.blocked_commands || []).length + " blocked · sandbox "
      + (result.sandbox || ""));
    out.textContent = pretty({
      result: result.result,
      sandbox: result.sandbox,
      commands_checked: result.commands_checked,
      blocked_commands: result.blocked_commands,
      log: result.log,
      source: result.source
    });
  }

  /* -------------------------------------------------- approval gate UI */
  function setGate(state, detail) {
    var panel = $("gatePanel");
    if (!panel) return;
    setText("gateState", state);
    setText("gateDetail", detail || "");
  }

  function disableGateButtons(disabled) {
    ["btnApprove", "btnReject"].forEach(function (id) {
      var btn = $(id);
      if (btn) btn.disabled = !!disabled;
    });
  }

  Z.on("stages", function (flow) {
    if (!flow || !flow.stages) return;
    var gate = flow.stages.filter(function (s) { return s.id === 7; })[0];
    if (!gate) return;
    if (gate.status === "gate") {
      setGate("AWAITING APPROVAL", gate.detail || "");
      disableGateButtons(false);
    } else if (gate.status === "done") {
      setGate("APPROVED & EXECUTED", gate.detail || "");
      disableGateButtons(true);
    } else if (gate.status === "pending" && flow.pipeline === "rejected") {
      setGate("REJECTED", gate.detail || "");
      disableGateButtons(true);
    } else {
      setGate("IDLE", "No proposal is waiting at the gate.");
      disableGateButtons(true);
    }
  });

  /* ------------------------------------------------------- action wiring */
  function bind(id, handler) {
    var node = $(id);
    if (node) node.addEventListener("click", handler);
  }

  bind("btnDiagnose", function () {
    Z.api("/api/diagnose", { method: "POST", body: {} })
      .then(function (data) {
        renderDiagnosis(data.diagnosis);
        Z.toast("Diagnosis complete (source: " + data.diagnosis.source + ")", "ok");
      })
      .catch(function (err) { handleError(err, "Diagnosis failed"); });
  });

  bind("btnPropose", function () {
    Z.api("/api/propose", { method: "POST", body: {} })
      .then(function (data) {
        pendingProposal = data.proposal;
        renderProposal(data.proposal);
        Z.toast("Proposal staged at the human approval gate", "warn");
      })
      .catch(function (err) { handleError(err, "Proposal failed"); });
  });

  bind("btnValidate", function () {
    Z.api("/api/validate", { method: "POST", body: {} })
      .then(function (data) {
        lastValidation = data.validation;
        approvalContext = data.ok ? {
          approval_id: data.approval_id,
          incident_id: data.incident_id
        } : null;
        renderValidation(data.validation);
        Z.toast("Sandbox verdict: " + data.validation.result,
                data.validation.result === "PASS" ? "ok" : "error");
      })
      .catch(function (err) { handleError(err, "Validation failed"); });
  });

  bind("btnApprove", function () {
    var token = approvalToken();
    if (!token) {
      handleError(new Error("Enter the configured operator token first."), "Approval failed");
      return;
    }
    if (!approvalContext || !approvalContext.approval_id || approvalContext.incident_id == null) {
      handleError(new Error("Validate the current plan successfully before approval."), "Approval failed");
      return;
    }
    disableGateButtons(true);
    setGate("APPROVING", "Recording approval; recovery has not started.");
    var approvalRecorded = false;
    var requestBody = {
      actor: "sre.oncall",
      incident_id: approvalContext.incident_id,
      approval_id: approvalContext.approval_id
    };
    Z.api("/api/approve", { method: "POST", body: requestBody, approvalToken: token })
      .then(function (data) {
        approvalRecorded = true;
        setGate("APPROVED", "Approval recorded. Sending the separate recovery request...");
        Z.toast("Approval recorded; recovery is a separate request.", "warn");
        return Z.api("/api/recover", {
          method: "POST", body: requestBody, approvalToken: token
        });
      })
      .then(function (result) {
        approvalContext = null;
        setGate("RECOVERED", result.playbook_id + " v" + result.playbook_version
          + " · simulated recovery completed");
        Z.toast("Simulated recovery completed. Playbook " + result.playbook_id
          + " v" + result.playbook_version + " stored.", "ok");
      })
      .catch(function (err) {
        if (approvalRecorded) {
          setGate("APPROVAL RECORDED", "Recovery did not complete. Review status before retrying.");
        } else {
          setGate("APPROVAL FAILED", "No recovery request was sent.");
        }
        disableGateButtons(false);
        handleError(err, "Approval or recovery failed");
      });
  });

  bind("btnReject", function () {
    var token = approvalToken();
    if (!token) {
      handleError(new Error("Enter the configured operator token first."), "Reject failed");
      return;
    }
    disableGateButtons(true);
    Z.api("/api/reject", { method: "POST", body: { actor: "sre.oncall" }, approvalToken: token })
      .then(function () {
        setGate("REJECTED", "Nothing was executed. Chaos remains active.");
        Z.toast("Proposal rejected - nothing executed", "warn");
      })
      .catch(function (err) {
        disableGateButtons(false);
        handleError(err, "Reject failed");
      });
  });

  bind("btnExecuteDirect", function () {
    Z.api("/api/execute", { method: "POST", body: { actor: "ai.autopilot" } })
      .then(function () {
        Z.toast("Unexpectedly executed", "warn");
      })
      .catch(function (err) {
        var code = err && err.status ? err.status : "?";
        Z.toast("Blocked as designed: HTTP " + code + " - " + (err.message || ""), "error");
        logLine("error", "DIRECT EXECUTION BLOCKED (HTTP " + code + "): "
          + (err.message || ""));
      });
  });

  /* ---------------------------------------------------------- chaos lab */
  var chosenFault = "CPU_RUNAWAY";

  function selectFault(fault) {
    chosenFault = fault;
    ["btnFaultCpu", "btnFaultDb2"].forEach(function (id) {
      var btn = $(id);
      if (btn) btn.classList.toggle("active", btn.dataset.fault === fault);
    });
    setText("selectedFault", fault);
  }

  bind("btnFaultCpu", function () { selectFault("CPU_RUNAWAY"); });
  bind("btnFaultDb2", function () { selectFault("DB2_LOCK_CONTENTION"); });

  var slider = $("intensity");
  if (slider) {
    slider.addEventListener("input", function () {
      setText("intensityVal", slider.value + "%");
    });
  }

  bind("btnInject", function () {
    var intensity = slider ? parseInt(slider.value, 10) : 60;
    Z.api("/api/chaos/inject", { method: "POST", body: { fault_type: chosenFault, intensity: intensity } })
      .then(function (data) {
        Z.toast(data.chaos.message, "warn");
      })
      .catch(function (err) { handleError(err, "Injection failed"); });
  });

  bind("btnKill", function () {
    Z.api("/api/chaos/stop", { method: "POST", body: { reason: "EMERGENCY_KILL_SWITCH" } })
      .then(function (data) {
        Z.toast(data.chaos.message, "ok");
        logLine("critical", "EMERGENCY KILL SWITCH engaged.");
      })
      .catch(function (err) { handleError(err, "Kill switch failed"); });
  });

  bind("btnKillTop", function () {
    Z.api("/api/chaos/stop", { method: "POST", body: { reason: "EMERGENCY_KILL_SWITCH" } })
      .then(function (data) { Z.toast(data.chaos.message, "ok"); })
      .catch(function (err) { handleError(err, "Kill switch failed"); });
  });

  function updateLabStatus(chaos, sample) {
    setText("labState", chaos ? (chaos.active ? "ACTIVE" : "IDLE") : "-");
    setText("labFault", chaos && chaos.fault ? chaos.fault : "NONE");
    setText("labWorkers", chaos ? (chaos.workers_alive + " / " + chaos.max_workers) : "0");
    setText("labElapsed", chaos ? (chaos.elapsed_sec + "s / " + chaos.max_duration_sec + "s") : "0s");
    setText("labAutoKill", chaos ? (chaos.auto_kill_in_sec + "s") : "-");
    var banner = $("labBanner");
    if (banner) banner.classList.toggle("hidden", !(chaos && chaos.active));
    if (sample) {
      setText("labCpu", Z.fmt(sample.cpu, 1) + "%");
      setText("labTps", Z.fmt(sample.tps) + " txn/s");
      setText("labLocks", Z.fmt(sample.locks));
    }
  }

  /* ---------------------------------------------------------- playbooks */
  function renderPlaybooks(playbooks) {
    var body = $("pbBody");
    if (!body) return;
    body.innerHTML = "";
    playbooks.forEach(function (pb) {
      var tr = document.createElement("tr");

      var tdId = document.createElement("td");
      tdId.className = "mono";
      tdId.textContent = pb.id;

      var tdName = document.createElement("td");
      tdName.textContent = pb.name;

      var tdVersion = document.createElement("td");
      tdVersion.className = "mono";
      tdVersion.textContent = "v" + pb.version;

      var tdTrigger = document.createElement("td");
      var triggerChip = document.createElement("span");
      triggerChip.className = "badge " + (pb.trigger_signature === "CPU_RUNAWAY" ? "badge-crit" : "badge-warn");
      triggerChip.textContent = pb.trigger_signature;
      tdTrigger.appendChild(triggerChip);

      var tdSteps = document.createElement("td");
      tdSteps.className = "mono";
      tdSteps.textContent = (pb.steps || []).length;

      var tdValidation = document.createElement("td");
      var vBadge = document.createElement("span");
      vBadge.className = "badge " + ((pb.validation_result || "").toUpperCase() === "PASS" ? "badge-ok" : "badge-crit");
      vBadge.textContent = pb.validation_result || "-";
      tdValidation.appendChild(vBadge);

      var tdStatus = document.createElement("td");
      tdStatus.textContent = pb.status;

      var tdApprover = document.createElement("td");
      tdApprover.textContent = pb.approved_by || "-";

      var tdCreated = document.createElement("td");
      tdCreated.className = "mono";
      tdCreated.textContent = (pb.created_at || "").slice(0, 19);

      var tdAction = document.createElement("td");
      var replay = document.createElement("button");
      replay.className = "btn btn-sm";
      replay.textContent = "[1-Click Replay (~2s)]";
      replay.addEventListener("click", function () {
        replay.disabled = true;
        replay.textContent = "[REPLAYING...]";
        var started = performance.now();
        Z.api("/api/replay/" + pb.id, { method: "POST", body: {} })
          .then(function (data) {
            replay.textContent = "[REPLAYED " + data.elapsed_ms + "ms]";
            Z.toast("Replay " + data.playbook_id + " v" + data.playbook_version
              + " completed in " + data.elapsed_ms + " ms (AI bypassed)", "ok");
            setTimeout(function () {
              replay.disabled = false;
              replay.textContent = "[1-Click Replay (~2s)]";
            }, 4000);
          })
          .catch(function (err) {
            replay.disabled = false;
            replay.textContent = "[1-Click Replay (~2s)]";
            handleError(err, "Replay failed");
            void started;
          });
      });
      tdAction.appendChild(replay);

      [tdId, tdName, tdVersion, tdTrigger, tdSteps, tdValidation, tdStatus,
       tdApprover, tdCreated, tdAction].forEach(function (td) { tr.appendChild(td); });
      body.appendChild(tr);
    });
  }

  function loadPlaybooks() {
    if (!$("pbBody")) return;
    Z.api("/api/playbooks").then(function (data) { renderPlaybooks(data.playbooks); })
      .catch(function (err) { handleError(err, "Could not load playbooks"); });
  }

  bind("btnRefreshPlaybooks", loadPlaybooks);

  /* --------------------------------------------------------- audit table */
  function auditRow(entry) {
    var tr = document.createElement("tr");

    var tdTs = document.createElement("td");
    tdTs.className = "mono";
    tdTs.textContent = entry.ts;

    var tdActor = document.createElement("td");
    tdActor.className = "mono";
    tdActor.textContent = entry.actor;

    var tdAction = document.createElement("td");
    tdAction.textContent = entry.action;

    var tdTarget = document.createElement("td");
    tdTarget.className = "mono";
    tdTarget.textContent = entry.target || "-";

    var tdRisk = document.createElement("td");
    var risk = (entry.risk_level || "LOW").toUpperCase();
    var badge = document.createElement("span");
    badge.className = "badge " + (risk === "HIGH" ? "badge-crit"
      : risk === "MEDIUM" ? "badge-warn" : "badge-ok");
    badge.textContent = risk;
    tdRisk.appendChild(badge);

    var tdDetails = document.createElement("td");
    var details = document.createElement("div");
    details.className = "json";
    try {
      details.textContent = JSON.stringify(JSON.parse(entry.details_json || "{}"));
    } catch (e) {
      details.textContent = entry.details_json || "";
    }
    tdDetails.appendChild(details);

    [tdTs, tdActor, tdAction, tdTarget, tdRisk, tdDetails].forEach(function (td) { tr.appendChild(td); });
    return tr;
  }

  function prependAudit(entry) {
    var body = $("auditBody");
    if (!body) return;
    body.insertBefore(auditRow(entry), body.firstChild);
    while (body.childNodes.length > 250) body.removeChild(body.lastChild);
  }

  Z.on("audit", prependAudit);

  function loadAudit() {
    var body = $("auditBody");
    if (!body) return;
    Z.api("/api/audit?limit=200").then(function (data) {
      body.innerHTML = "";
      (data.audit || []).forEach(function (entry) { body.appendChild(auditRow(entry)); });
    }).catch(function (err) { handleError(err, "Could not load audit log"); });
  }

  bind("btnRefreshAudit", loadAudit);

  /* ------------------------------------------------- connection + clock */
  Z.on("connection", function (state) {
    var badge = $("connBadge");
    if (!badge) return;
    badge.textContent = state.connected ? "LIVE SOCKET" : "OFFLINE";
    badge.className = "badge " + (state.connected ? "badge-ok" : "badge-crit");
  });

  setInterval(function () {
    setText("clock", Z.clockString());
  }, 1000);
  setText("clock", Z.clockString());

  /* ------------------------------------------------------------ bootstrap */
  selectFault(chosenFault);
  loadPlaybooks();
  loadAudit();

  Z.api("/api/flow").then(function (data) {
    renderStages(data.stages);
    updateLabStatus(data.chaos, null);
    var pending = data.pending || {};
    if (pending.state === "AWAITING_APPROVAL") {
      pendingProposal = pending.proposal;
      if (pending.proposal) renderProposal(pending.proposal);
      setGate("AWAITING APPROVAL", pending.title || "");
      disableGateButtons(false);
    }
    logLine("ok", "Z-PHOENIX console attached (pipeline: "
      + (data.stages.pipeline || "idle") + ").");
  }).catch(function (err) { handleError(err, "Bootstrap failed"); });
})();
