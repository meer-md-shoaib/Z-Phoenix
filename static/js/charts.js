/*
 * Z-PHOENIX :: charts.js
 * Dual-axis live telemetry chart:
 *   left  axis (y)  - CPU %   (critical red #EF4444) + Db2 lock waits (orange)
 *   right axis (y1) - CICS TPS (cyan #06B6D4)
 * Fed by the 1 Hz "telemetry" WebSocket stream; keeps a 60-sample window.
 */
(function () {
  "use strict";

  var MAX_POINTS = 60;
  var Z = window.ZPhoenix;
  var chart = null;
  var labels = [];
  var cpuData = [];
  var tpsData = [];
  var lockData = [];

  function buildChart() {
    if (typeof Chart === "undefined") return null;
    var canvas = document.getElementById("telemetryChart");
    if (!canvas) return null;

    Chart.defaults.font.family = "'Inter', 'Segoe UI', system-ui, sans-serif";
    Chart.defaults.color = "#94A3B8";

    return new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "CPU %",
            data: cpuData,
            borderColor: "#EF4444",
            backgroundColor: "rgba(239,68,68,0.12)",
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 0,
            fill: true,
            yAxisID: "y"
          },
          {
            label: "CICS TPS",
            data: tpsData,
            borderColor: "#06B6D4",
            backgroundColor: "rgba(6,182,212,0.10)",
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 0,
            fill: true,
            yAxisID: "y1"
          },
          {
            label: "Db2 Lock Waits",
            data: lockData,
            borderColor: "#F59E0B",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            borderDash: [5, 4],
            tension: 0.3,
            pointRadius: 0,
            fill: false,
            yAxisID: "y"
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: { color: "#CBD5E1", usePointStyle: true, boxWidth: 8 }
          },
          tooltip: {
            backgroundColor: "#111827",
            borderColor: "#1F2937",
            borderWidth: 1,
            titleColor: "#E5E7EB",
            bodyColor: "#CBD5E1"
          }
        },
        scales: {
          x: {
            ticks: { color: "#64748B", maxTicksLimit: 10 },
            grid: { color: "#1F2937" }
          },
          y: {
            position: "left",
            min: 0,
            max: 100,
            title: { display: true, text: "CPU %  /  Lock Waits", color: "#EF4444" },
            ticks: { color: "#94A3B8" },
            grid: { color: "#1F2937" }
          },
          y1: {
            position: "right",
            min: 0,
            max: 1200,
            title: { display: true, text: "CICS TPS", color: "#06B6D4" },
            ticks: { color: "#06B6D4" },
            grid: { drawOnChartArea: false }
          }
        }
      }
    });
  }

  function push(series, value) {
    series.push(value);
    if (series.length > MAX_POINTS) series.shift();
  }

  Z.on("telemetry", function (payload) {
    if (!chart) return;
    var sample = payload && payload.telemetry;
    if (!sample) return;
    labels.push(Z.clockString(sample.ts));
    if (labels.length > MAX_POINTS) labels.shift();
    push(cpuData, sample.cpu);
    push(tpsData, sample.tps);
    push(lockData, sample.locks);
    chart.update("none");
  });

  /* Boot once the DOM is parsed (scripts are loaded at end of body). */
  chart = buildChart();
  if (!chart) {
    console.warn("[Z-PHOENIX] Chart.js unavailable or no canvas on this page.");
  }
})();
