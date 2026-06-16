const socket = io();

const METRICS = {
  fuel_consumption: { label: "Fuel Consumption", unit: "L", decimals: 3, better: "lower" },
  co2: { label: "CO2", unit: "g", decimals: 1, better: "lower" },
  noise: { label: "Noise", unit: "dB", decimals: 1, better: "lower" },
  jam: { label: "Jam", unit: "lanes", decimals: 0, better: "lower" },
  emergency_breaking: { label: "Emergency Braking", unit: "events", decimals: 0, better: "lower" },
  pm: { label: "PM", unit: "g", decimals: 3, better: "lower" },
  nox: { label: "NOx", unit: "g", decimals: 3, better: "lower" },
  congestion: { label: "Congestion", unit: "%", decimals: 1, better: "lower", isPercent: true },
  collision: { label: "Collision", unit: "events", decimals: 0, better: "lower" },
  nvmoc: { label: "NVMOC", unit: "g", decimals: 3, better: "lower" },
};

const simTimeEl = document.getElementById("simTime");
const vehiclesEl = document.getElementById("vehicles");
const avgSpeedEl = document.getElementById("avgSpeed");
const stopRatioEl = document.getElementById("stopRatio");
const attacksEl = document.getElementById("attacks");
const metricGrid = document.getElementById("metricGrid");
const compareTableBody = document.querySelector("#compareTable tbody");

const HISTORY_ATTACKED_KEY = "vanetAttackedHistory";
const SELECTED_METRIC_KEY = "vanetSelectedMetric";
const LAST_MAP_KEY = "vanetLastMap";

const state = {
  liveHistory: [],
  baselineHistory: [],
  attackedHistory: loadStoredHistory(HISTORY_ATTACKED_KEY),
  selectedMetric: localStorage.getItem(SELECTED_METRIC_KEY) || "fuel_consumption",
  latestLive: null,
  currentMap: localStorage.getItem(LAST_MAP_KEY) || null,
};

let metricChart = null;
let chartMouseDown = false;
let chartStartX = 0;
let chartStartY = 0;
let chartStartViewMinX = 0;
let chartStartViewMaxX = 600;
let chartStartViewMinY = 0;
let chartStartViewMaxY = 0;
let selectZoneStartX = 0;
let selectZoneStartY = 0;
let overlayCanvas = null;
let overlayCtx = null;
let activeMode = null;

function loadStoredHistory(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function getMetricValue(point, metric) {
  if (!point) return 0;
  if (point.metrics && Object.prototype.hasOwnProperty.call(point.metrics, metric)) {
    return Number(point.metrics[metric]) || 0;
  }
  if (Object.prototype.hasOwnProperty.call(point, metric)) {
    return Number(point[metric]) || 0;
  }
  return 0;
}

function getPointTime(point) {
  const time = Number(point && (point.simulation_time ?? point.step ?? 0));
  return Number.isFinite(time) ? time : 0;
}

function ensureZeroOrigin(points) {
  if (!points.length) return points;
  if (points[0].x > 0) return [{ x: 0, y: points[0].y }, ...points];
  return points;
}

function seriesToPoints(history, metric) {
  return ensureZeroOrigin(
    (history || [])
      .map((point) => ({ x: getPointTime(point), y: getMetricValue(point, metric) }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y))
  );
}

function averageMetricUpToTime(history, metric, maxTime) {
  const limit = Number(maxTime);
  if (!Number.isFinite(limit)) return 0;

  const filtered = (history || []).filter((point) => getPointTime(point) <= limit);
  if (!filtered.length) return 0;

  const total = filtered.reduce((sum, point) => sum + getMetricValue(point, metric), 0);
  return total / filtered.length;
}

function getAverageSnapshot(history, metric, maxTime) {
  return averageMetricUpToTime(history, metric, maxTime);
}

function getSnapshotAtTime(history, maxTime) {
  const limit = Number(maxTime);
  if (!Number.isFinite(limit)) return null;

  let snapshot = null;
  for (const point of history || []) {
    if (getPointTime(point) <= limit) snapshot = point;
    else break;
  }
  return snapshot;
}

function getValueAtTime(history, metric, maxTime) {
  const snapshot = getSnapshotAtTime(history, maxTime);
  return getMetricValue(snapshot, metric);
}

function latestPoint(history) {
  return history && history.length ? history[history.length - 1] : null;
}

function findBaselinePointAt(time) {
  if (!state.baselineHistory || !state.baselineHistory.length) return null;
  let best = null;
  for (let i = 0; i < state.baselineHistory.length; i += 1) {
    const pt = state.baselineHistory[i];
    const t = getPointTime(pt);
    if (t <= time) best = pt;
    else break;
  }
  return best || state.baselineHistory[0];
}

function formatValue(metric, value) {
  const config = METRICS[metric];
  if (!config) return String(value ?? 0);
  const numeric = Number(value ?? 0);
  if (config.isPercent) return `${(numeric * 100).toFixed(config.decimals)}%`;
  return `${numeric.toFixed(config.decimals)} ${config.unit}`;
}

function formatDeltaPercent(liveValue, baselineValue) {
  const baseline = Number(baselineValue);
  const live = Number(liveValue);
  if (!Number.isFinite(baseline) || !Number.isFinite(live)) return "n/a";
  if (Math.abs(baseline) < 1e-9) return "n/a";
  const deltaPercent = ((live - baseline) / Math.abs(baseline)) * 100;
  const sign = deltaPercent > 0 ? "+" : "";
  return `${sign}${deltaPercent.toFixed(1)}%`;
}

function updateTopCards() {
  const latest = state.latestLive;
  if (!latest) {
    simTimeEl.textContent = "-";
    vehiclesEl.textContent = "-";
    avgSpeedEl.textContent = "-";
    stopRatioEl.textContent = "-";
    attacksEl.textContent = "0";
    return;
  }

  const simulationTime = Number(latest.simulation_time);
  const vehicleCount = Number(latest.vehicle_count);
  const avgSpeed = Number(latest.avg_speed);
  const stoppedRatio = Number(latest.stopped_ratio);
  const attackCount = Number(latest.active_attack_count);

  simTimeEl.textContent = Number.isFinite(simulationTime) ? `${simulationTime.toFixed(1)}s` : "-";
  vehiclesEl.textContent = Number.isFinite(vehicleCount) ? `${vehicleCount}` : "-";
  avgSpeedEl.textContent = Number.isFinite(avgSpeed) ? `${avgSpeed.toFixed(1)} m/s` : "-";
  stopRatioEl.textContent = Number.isFinite(stoppedRatio) ? `${(stoppedRatio * 100).toFixed(1)}%` : "-";
  attacksEl.textContent = Number.isFinite(attackCount) ? `${attackCount}` : "0";
}

function renderMetricGrid() {
  if (!metricGrid) return;

  const latestLive = latestPoint(state.liveHistory);
  const currentTime = latestLive ? getPointTime(latestLive) : 0;

  metricGrid.innerHTML = Object.entries(METRICS)
    .map(([metric, config]) => {
      const liveValue = getAverageSnapshot(state.liveHistory, metric, currentTime);
      const baselineValue = getAverageSnapshot(state.baselineHistory, metric, currentTime);
      const activeClass = metric === state.selectedMetric
        ? 'style="border-color: #d97706; box-shadow: 0 0 0 2px rgba(217,119,6,0.15);"'
        : "";

      return `
        <div class="card metric-card" data-metric="${metric}" ${activeClass}>
          <div class="label">${config.label}</div>
          <div class="value">${formatValue(metric, liveValue)}</div>
          <div class="label">Avg live: ${formatValue(metric, liveValue)}</div>
          <div class="label">Avg baseline: ${formatValue(metric, baselineValue)}</div>
        </div>
      `;
    })
    .join("");

  metricGrid.querySelectorAll(".metric-card").forEach((card) => {
    card.addEventListener("click", () => {
      const metric = card.getAttribute("data-metric");
      if (!metric || !METRICS[metric]) return;
      state.selectedMetric = metric;
      localStorage.setItem(SELECTED_METRIC_KEY, metric);
      applyAllViews();
    });
  });
}

function renderComparisonTable() {
  if (!compareTableBody) return;

  const latestLive = latestPoint(state.liveHistory);
  const currentTime = latestLive ? getPointTime(latestLive) : 0;

  compareTableBody.innerHTML = "";
  Object.entries(METRICS).forEach(([metric, config]) => {
    const liveValue = getAverageSnapshot(state.liveHistory, metric, currentTime);
    const baselineValue = getAverageSnapshot(state.baselineHistory, metric, currentTime);
    const delta = liveValue - baselineValue;
    const trend = delta < 0 ? "better" : delta > 0 ? "worse" : "same";
    const trendClass = trend === "better" ? "ok" : trend === "worse" ? "bad" : "";
    const arrow = delta < 0 ? "▼" : delta > 0 ? "▲" : "→";
    const arrowColor = delta < 0 ? "ok" : delta > 0 ? "bad" : "";
    const deltaPercentText = formatDeltaPercent(liveValue, baselineValue);
    const comparisonText = deltaPercentText === "n/a" ? `${arrow} n/a` : `${arrow} ${deltaPercentText}`;

    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${config.label}</td>
      <td>${formatValue(metric, liveValue)}</td>
      <td>${formatValue(metric, baselineValue)}</td>
      <td class="${arrowColor}">${comparisonText}</td>
      <td class="${trendClass}">${trend}</td>
    `;
    compareTableBody.appendChild(row);
  });
}

function initChart() {
  const canvas = document.getElementById("speedChart");
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  metricChart = new Chart(ctx, {
    type: "line",
    data: {
      datasets: [
        {
          label: "Baseline",
          data: [],
          borderColor: "#22c55e",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
          fill: false,
        },
        {
          label: "Live",
          data: [],
          borderColor: "#f97316",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: "Time (s)" },
          min: 0,
          max: 600,
        },
        y: {
          title: { display: true, text: "Metric Value" },
        },
      },
      plugins: {
        legend: { display: true, position: "top" },
      },
    },
  });

  overlayCanvas = document.createElement("canvas");
  overlayCanvas.style.position = "absolute";
  overlayCanvas.style.cursor = "crosshair";
  overlayCanvas.style.pointerEvents = "none";
  overlayCanvas.style.top = "0";
  overlayCanvas.style.left = "0";
  overlayCanvas.id = "speedChartOverlay";
  overlayCanvas.width = canvas.width;
  overlayCanvas.height = canvas.height;

  canvas.parentElement.style.position = "relative";
  canvas.parentElement.insertBefore(overlayCanvas, canvas.nextSibling);
  overlayCtx = overlayCanvas.getContext("2d");
}

function renderChart() {
  if (!metricChart) return;

  const metric = state.selectedMetric;
  const baselinePoints = seriesToPoints(state.baselineHistory, metric);
  const livePoints = seriesToPoints(state.liveHistory, metric);

  metricChart.data.datasets[0].data = baselinePoints;
  metricChart.data.datasets[1].data = livePoints;
  metricChart.options.plugins.title = {
    display: true,
    text: `${METRICS[metric] ? METRICS[metric].label : metric}`,
  };
  metricChart.update("none");
}

function applyAllViews() {
  updateTopCards();
  renderChart();
  renderMetricGrid();
  renderComparisonTable();
}

function updateButtonStyles() {
  const zoomInBtn = document.getElementById("zoomInBtn");
  const zoomOutBtn = document.getElementById("zoomOutBtn");
  const panBtn = document.getElementById("panBtn");
  const selectZoneBtn = document.getElementById("selectZoneBtn");

  if (zoomInBtn) zoomInBtn.style.opacity = activeMode === "zoomIn" ? "1" : "0.6";
  if (zoomOutBtn) zoomOutBtn.style.opacity = activeMode === "zoomOut" ? "1" : "0.6";
  if (panBtn) panBtn.style.opacity = activeMode === "pan" ? "1" : "0.6";
  if (selectZoneBtn) selectZoneBtn.style.opacity = activeMode === "selectZone" ? "1" : "0.6";
}

function setMode(newMode) {
  activeMode = activeMode === newMode ? null : newMode;
  updateButtonStyles();

  const canvas = document.getElementById("speedChart");
  if (!canvas) return;

  if (activeMode === "selectZone") canvas.style.cursor = "crosshair";
  else if (activeMode === "pan") canvas.style.cursor = "grab";
  else if (activeMode === "zoomIn" || activeMode === "zoomOut") canvas.style.cursor = "zoom-in";
  else canvas.style.cursor = "default";
}

function performZoom(e) {
  if (!metricChart) return;

  const rect = e.target.getBoundingClientRect();
  const currentMinX = Number(metricChart.options.scales.x.min ?? 0);
  const currentMaxX = Number(metricChart.options.scales.x.max ?? 600);
  const currentMinY = metricChart.options.scales.y.min;
  const currentMaxY = metricChart.options.scales.y.max;

  const clickXPercent = (e.clientX - rect.left) / rect.width;
  const clickYPercent = (e.clientY - rect.top) / rect.height;
  const clickedTime = currentMinX + (currentMaxX - currentMinX) * clickXPercent;
  const clickedValue = currentMinY !== undefined && currentMaxY !== undefined
    ? currentMaxY - (currentMaxY - currentMinY) * clickYPercent
    : undefined;

  const rangeX = currentMaxX - currentMinX;
  const zoomFactor = activeMode === "zoomIn" ? 0.9 : 1.1;
  const newRangeX = rangeX * zoomFactor;
  metricChart.options.scales.x.min = clickedTime - newRangeX / 2;
  metricChart.options.scales.x.max = clickedTime + newRangeX / 2;

  if (clickedValue !== undefined && currentMinY !== undefined && currentMaxY !== undefined) {
    const rangeY = currentMaxY - currentMinY;
    const newRangeY = rangeY * zoomFactor;
    metricChart.options.scales.y.min = clickedValue - newRangeY / 2;
    metricChart.options.scales.y.max = clickedValue + newRangeY / 2;
  }

  metricChart.update("none");
}

function startPan(e) {
  if (!metricChart) return;
  chartMouseDown = true;
  chartStartX = e.clientX;
  chartStartY = e.clientY;
  chartStartViewMinX = Number(metricChart.options.scales.x.min ?? 0);
  chartStartViewMaxX = Number(metricChart.options.scales.x.max ?? 600);
  chartStartViewMinY = metricChart.options.scales.y.min;
  chartStartViewMaxY = metricChart.options.scales.y.max;
}

function continuePan(e) {
  if (!chartMouseDown || !metricChart) return;

  const rect = e.target.getBoundingClientRect();
  const dx = e.clientX - chartStartX;
  const dataRangeX = chartStartViewMaxX - chartStartViewMinX;
  const dataShiftX = -(dx / rect.width) * dataRangeX;
  metricChart.options.scales.x.min = chartStartViewMinX + dataShiftX;
  metricChart.options.scales.x.max = chartStartViewMaxX + dataShiftX;

  if (chartStartViewMinY !== undefined && chartStartViewMaxY !== undefined) {
    const dy = e.clientY - chartStartY;
    const dataRangeY = chartStartViewMaxY - chartStartViewMinY;
    const dataShiftY = (dy / rect.height) * dataRangeY;
    metricChart.options.scales.y.min = chartStartViewMinY + dataShiftY;
    metricChart.options.scales.y.max = chartStartViewMaxY + dataShiftY;
  }

  metricChart.update("none");
}

function startSelectZone(e) {
  chartMouseDown = true;
  selectZoneStartX = e.clientX;
  selectZoneStartY = e.clientY;
}

function continueSelectZone(e) {
  if (!chartMouseDown || !overlayCtx || !overlayCanvas) return;

  const canvas = document.getElementById("speedChart");
  const rect = canvas.getBoundingClientRect();
  const x1 = selectZoneStartX - rect.left;
  const y1 = selectZoneStartY - rect.top;
  const x2 = e.clientX - rect.left;
  const y2 = e.clientY - rect.top;

  overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
  overlayCtx.strokeStyle = "#f97316";
  overlayCtx.lineWidth = 2;
  overlayCtx.fillStyle = "rgba(249, 115, 22, 0.1)";
  overlayCtx.fillRect(x1, y1, x2 - x1, y2 - y1);
  overlayCtx.strokeRect(x1, y1, x2 - x1, y2 - y1);
}

function endSelectZone(e) {
  chartMouseDown = false;
  if (!metricChart || !overlayCtx || !overlayCanvas) return;

  const canvas = document.getElementById("speedChart");
  const rect = canvas.getBoundingClientRect();
  const x1 = selectZoneStartX - rect.left;
  const x2 = e.clientX - rect.left;
  const y1 = selectZoneStartY - rect.top;
  const y2 = e.clientY - rect.top;

  const currentMinX = Number(metricChart.options.scales.x.min ?? 0);
  const currentMaxX = Number(metricChart.options.scales.x.max ?? 600);
  const currentMinY = metricChart.options.scales.y.min;
  const currentMaxY = metricChart.options.scales.y.max;

  const minPixelX = Math.min(x1, x2);
  const maxPixelX = Math.max(x1, x2);
  const dataRangeX = currentMaxX - currentMinX;
  metricChart.options.scales.x.min = currentMinX + dataRangeX * (minPixelX / rect.width);
  metricChart.options.scales.x.max = currentMinX + dataRangeX * (maxPixelX / rect.width);

  if (currentMinY !== undefined && currentMaxY !== undefined) {
    const minPixelY = Math.min(y1, y2);
    const maxPixelY = Math.max(y1, y2);
    const dataRangeY = currentMaxY - currentMinY;
    metricChart.options.scales.y.min = currentMaxY - dataRangeY * (maxPixelY / rect.height);
    metricChart.options.scales.y.max = currentMaxY - dataRangeY * (minPixelY / rect.height);
  }

  metricChart.update("none");
  overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
}

function onResetViewClick() {
  if (!metricChart) return;
  metricChart.options.scales.x.min = 0;
  metricChart.options.scales.x.max = 600;
  delete metricChart.options.scales.y.min;
  delete metricChart.options.scales.y.max;
  activeMode = null;
  updateButtonStyles();
  if (overlayCtx && overlayCanvas) overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
  metricChart.update();
}

function setupChartEventListeners() {
  const canvas = document.getElementById("speedChart");
  if (!canvas) return;

  canvas.addEventListener("click", (e) => {
    if (activeMode === "zoomIn" || activeMode === "zoomOut") performZoom(e);
  });

  canvas.addEventListener("mousedown", (e) => {
    if (activeMode === "pan") startPan(e);
    else if (activeMode === "selectZone") startSelectZone(e);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (activeMode === "pan" && chartMouseDown) continuePan(e);
    else if (activeMode === "selectZone" && chartMouseDown) continueSelectZone(e);
  });

  canvas.addEventListener("mouseup", (e) => {
    if (chartMouseDown && activeMode === "selectZone") endSelectZone(e);
    chartMouseDown = false;
  });
}

async function loadBaselineForMap(mapName, forceReload = false) {
  const normalized = String(mapName || "paris").toLowerCase().replace("_simulation", "");
  try {
    const loadRes = await fetch("/api/baseline/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ map_name: normalized, force_reload: !!forceReload }),
    });
    const loadData = await loadRes.json();
    if (!loadRes.ok || loadData.error) throw new Error(loadData.error || `HTTP ${loadRes.status}`);

    const currentRes = await fetch(`/api/baseline/current?map_name=${encodeURIComponent(normalized)}`);
    const currentData = await currentRes.json();
    if (!currentRes.ok || currentData.status !== "loaded" || !Array.isArray(currentData.baseline)) {
      throw new Error(currentData.message || currentData.error || "Baseline unavailable");
    }

    state.baselineHistory = currentData.baseline;
    state.currentMap = normalized;
    localStorage.setItem(LAST_MAP_KEY, normalized);
    applyAllViews();
    return true;
  } catch (err) {
    console.error("[BASELINE] Error:", err.message);
    return false;
  }
}

function pickStartupMap(mapsResponse) {
  const queryMap = new URLSearchParams(window.location.search).get("map");
  if (queryMap) return queryMap;
  if (state.currentMap) return state.currentMap;

  const maps = Array.isArray(mapsResponse && mapsResponse.maps) ? mapsResponse.maps : [];
  const available = maps.map((m) => String(m.map_name || "").toLowerCase());
  if (available.includes("paris2.0")) return "paris2.0";
  if (available.includes("paris")) return "paris";
  if (available.includes("basic")) return "basic";
  return available[0] || "paris";
}

async function preloadBaselineAtStartup() {
  try {
    const mapsRes = await fetch("/api/baseline/maps");
    const mapsData = await mapsRes.json();
    const maps = Array.isArray(mapsData && mapsData.maps) ? mapsData.maps : [];
    const startupMap = pickStartupMap(mapsData);
    const orderedCandidates = [
      startupMap,
      ...maps.map((m) => String(m.map_name || "").toLowerCase()),
      "basic",
      "paris",
    ].filter(Boolean);

    const tried = new Set();
    for (const candidate of orderedCandidates) {
      if (tried.has(candidate)) continue;
      tried.add(candidate);
      // Try multiple candidates so baseline appears even if localStorage contains a stale map.
      // eslint-disable-next-line no-await-in-loop
      const ok = await loadBaselineForMap(candidate);
      if (ok) break;
    }
  } catch (err) {
    console.error("[BASELINE] Startup preload failed:", err.message);
  }
}

function normalizeSnapshotPayload(payload) {
  if (!payload) return null;
  if (payload.latest && typeof payload.latest === "object") return payload.latest;
  if (payload.status === "ok" && payload.latest && typeof payload.latest === "object") return payload.latest;
  if (typeof payload === "object" && (payload.metrics || payload.simulation_time !== undefined || payload.step !== undefined)) {
    return payload;
  }
  return null;
}

function bindSocket() {
  socket.on("metrics", async (payload) => {
    const snapshot = normalizeSnapshotPayload(payload);
    if (!snapshot) return;

    const last = state.liveHistory[state.liveHistory.length - 1];
    if (!last || Number(last.step) !== Number(snapshot.step)) {
      state.liveHistory.push(snapshot);
      if (state.liveHistory.length > 5000) state.liveHistory.shift();
    } else {
      state.liveHistory[state.liveHistory.length - 1] = snapshot;
    }

    state.latestLive = snapshot;
    applyAllViews();

    if (snapshot.map_name) {
      const inferredMap = String(snapshot.map_name).toLowerCase().replace("_simulation", "");
      const baselineMissing = state.baselineHistory.length === 0;
      const mapChanged = !state.currentMap || state.currentMap !== inferredMap;
      if (baselineMissing || mapChanged) {
        await loadBaselineForMap(inferredMap);
      }
    }
  });

  socket.on("metrics_error", (err) => {
    const message = err && err.error ? err.error : "unknown";
    attacksEl.textContent = `error: ${message}`;
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  initChart();
  setupChartEventListeners();

  document.getElementById("zoomInBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    setMode("zoomIn");
  });
  document.getElementById("zoomOutBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    setMode("zoomOut");
  });
  document.getElementById("panBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    setMode("pan");
  });
  document.getElementById("selectZoneBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    setMode("selectZone");
  });
  document.getElementById("resetViewBtn")?.addEventListener("click", onResetViewClick);

  updateButtonStyles();
  applyAllViews();

  await preloadBaselineAtStartup();
  bindSocket();
});