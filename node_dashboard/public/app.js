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
const compareTableBody = document.querySelector("#compareTable tbody");
const mapSelect = document.getElementById("mapSelect");

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

function downsampleToSeconds(history) {
  if (!history || !history.length) return [];
  const sampled = [];
  for (let i = 0; i < history.length; i++) {
    const pt = history[i];
    const t = getPointTime(pt);
    const sec = Math.floor(t);

    const nextPt = history[i + 1];
    const nextSec = nextPt ? Math.floor(getPointTime(nextPt)) : -1;

    if (nextSec !== sec) {
      sampled.push(pt);
    }
  }
  return sampled;
}

function averageMetricUpToTime(history, metric, maxTime) {
  const limit = Number(maxTime);
  if (!Number.isFinite(limit)) return 0;

  const sampled = downsampleToSeconds(history);
  const filtered = sampled.filter((point) => getPointTime(point) <= limit);
  if (!filtered.length) return 0;

  const total = filtered.reduce((sum, point) => sum + getMetricValue(point, metric), 0);
  return total / filtered.length;
}

function getAverageSnapshot(history, metric, maxTime) {
  return averageMetricUpToTime(history, metric, maxTime);
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
    const trendClass = trend === "better" ? "trend-better" : trend === "worse" ? "trend-worse" : "trend-same";
    const arrow = delta < 0 ? "▼" : delta > 0 ? "▲" : "→";
    const arrowColor = delta < 0 ? "ok" : delta > 0 ? "bad" : "";
    const deltaPercentText = formatDeltaPercent(liveValue, baselineValue);
    const comparisonText = deltaPercentText === "n/a" ? `${arrow} n/a` : `${arrow} ${deltaPercentText}`;
    const isActive = metric === state.selectedMetric;

    const row = document.createElement("tr");
    if (isActive) {
      row.classList.add("active-row");
    }
    row.innerHTML = `
      <td style="font-weight: 500;">${config.label}</td>
      <td>${formatValue(metric, liveValue)}</td>
      <td>${formatValue(metric, baselineValue)}</td>
      <td class="${arrowColor}">${comparisonText}</td>
      <td><span class="${trendClass}">${trend}</span></td>
    `;
    row.addEventListener("click", () => {
      state.selectedMetric = metric;
      localStorage.setItem(SELECTED_METRIC_KEY, metric);
      applyAllViews();
    });
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
          borderColor: "#06b6d4",
          borderWidth: 2.5,
          pointRadius: 0,
          tension: 0.1,
          fill: false,
        },
        {
          label: "Attack",
          data: [],
          borderColor: "#ec4899",
          borderWidth: 2.5,
          pointRadius: 0,
          tension: 0.1,
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
          title: {
            display: true,
            text: "Time (s)",
            color: "#475569",
            font: { family: "Outfit", size: 13, weight: "bold" }
          },
          grid: {
            color: "rgba(0, 0, 0, 0.08)"
          },
          ticks: {
            color: "#475569",
            font: { family: "Outfit" }
          },
          min: 0,
          max: 300,
        },
        y: {
          title: {
            display: true,
            text: "Metric Value",
            color: "#475569",
            font: { family: "Outfit", size: 13, weight: "bold" }
          },
          grid: {
            color: "rgba(0, 0, 0, 0.08)"
          },
          ticks: {
            color: "#475569",
            font: { family: "Outfit" }
          }
        },
      },
      plugins: {
        legend: {
          display: true,
          position: "top",
          labels: {
            color: "#0f172a",
            font: { family: "Outfit", size: 12, weight: "500" }
          }
        },
        zoom: {
          pan: {
            enabled: true,
            mode: 'x',
            modifierKey: 'shift',
          },
          zoom: {
            drag: {
              enabled: true,
              backgroundColor: 'rgba(99, 102, 241, 0.15)',
              borderColor: 'rgba(99, 102, 241, 0.4)',
              borderWidth: 1,
            },
            wheel: {
              enabled: true,
              speed: 0.05,
            },
            pinch: {
              enabled: true
            },
            mode: 'x',
          }
        }
      },
    },
  });

  // Provide visual feedback for cursor shift
  window.addEventListener("keydown", (e) => {
    if (e.key === "Shift") {
      canvas.style.cursor = "grab";
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.key === "Shift") {
      canvas.style.cursor = "default";
    }
  });

  // Trackpad horizontal swipe to pan horizontally
  canvas.addEventListener("wheel", (e) => {
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      e.preventDefault();
      if (metricChart) {
        const xMin = metricChart.options.scales.x.min ?? 0;
        const xMax = metricChart.options.scales.x.max ?? 300;
        const range = xMax - xMin;
        const shift = (e.deltaX / canvas.clientWidth) * range * 0.5;
        metricChart.options.scales.x.min = xMin + shift;
        metricChart.options.scales.x.max = xMax + shift;
        metricChart.update("none");
      }
    }
  }, { passive: false });
}

function renderChart() {
  if (!metricChart) return;

  const metric = state.selectedMetric;
  const config = METRICS[metric];
  const chartTitleEl = document.getElementById("chartTitle");
  if (chartTitleEl && config) {
    chartTitleEl.textContent = `Attack vs Baseline - ${config.label}`;
  }

  const baselinePoints = seriesToPoints(state.baselineHistory, metric);
  const livePoints = seriesToPoints(state.liveHistory, metric);

  metricChart.data.datasets[0].data = baselinePoints;
  metricChart.data.datasets[1].data = livePoints;

  if (metricChart.options.plugins.zoom) {
    // If the chart isn't zoomed, update scale bounds to match simulation length
    const maxTime = Math.max(
      baselinePoints.length ? baselinePoints[baselinePoints.length - 1].x : 300,
      livePoints.length ? livePoints[livePoints.length - 1].x : 300
    );
    if (metricChart.scales.x.min === 0 && metricChart.scales.x.max === 300 && maxTime > 305) {
      metricChart.options.scales.x.max = Math.ceil(maxTime / 100) * 100;
    }
  }

  metricChart.update("none");
}

function latestPoint(history) {
  return history && history.length ? history[history.length - 1] : null;
}

function applyAllViews() {
  updateTopCards();
  renderChart();
  renderComparisonTable();
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

    if (mapSelect) {
      mapSelect.value = normalized;
    }

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

    // Load baseline
    await loadBaselineForMap(startupMap);

    // Populate dropdown
    if (mapSelect) {
      mapSelect.innerHTML = maps
        .map(
          (m) =>
            `<option value="${m.map_name}" ${m.map_name === state.currentMap ? "selected" : ""
            }>${m.map_name.toUpperCase()}</option>`
        )
        .join("");
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

    const currentTime = getPointTime(snapshot);
    const rocPrCurrentTimeEl = document.getElementById("rocPrCurrentTime");
    if (rocPrCurrentTimeEl) {
      rocPrCurrentTimeEl.textContent = `${currentTime.toFixed(1)}s`;
    }

    const rocPrWarningEl = document.getElementById("rocPrWarning");
    const computeRocPrBtnEl = document.getElementById("computeRocPrBtn");

    if (currentTime >= 200.0) {
      if (rocPrWarningEl) rocPrWarningEl.style.display = "none";
      if (computeRocPrBtnEl) computeRocPrBtnEl.disabled = false;
    } else {
      if (rocPrWarningEl) rocPrWarningEl.style.display = "block";
      if (computeRocPrBtnEl) computeRocPrBtnEl.disabled = true;
    }

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


  document.getElementById("zoomInBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (metricChart) metricChart.zoom(1.2);
  });
  document.getElementById("zoomOutBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (metricChart) metricChart.zoom(0.8);
  });
  document.getElementById("resetViewBtn")?.addEventListener("click", () => {
    if (metricChart) {
      metricChart.options.scales.x.min = 0;
      metricChart.options.scales.x.max = 300;
      metricChart.update();
    }
  });

  document.getElementById("saveBaselineBtn")?.addEventListener("click", async () => {
    if (!state.currentMap) {
      alert("No map loaded.");
      return;
    }
    if (state.liveHistory.length === 0) {
      alert("No simulation data to save.");
      return;
    }

    try {
      const res = await fetch("/api/baseline/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ map_name: state.currentMap })
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        throw new Error(data.error || "Save failed.");
      }

      await loadBaselineForMap(state.currentMap, true);
      alert(`Current simulation successfully saved as reference baseline for ${state.currentMap.toUpperCase()}!`);
    } catch (err) {
      console.error(err);
      alert(`Failed to save baseline: ${err.message}`);
    }
  });

  document.getElementById("saveRunBtn")?.addEventListener("click", async () => {
    if (state.liveHistory.length === 0) {
      alert("No simulation data to export.");
      return;
    }
    
    // Determine active attack type if any
    let activeAttackType = "";
    const lastPoint = state.liveHistory[state.liveHistory.length - 1];
    if (lastPoint && lastPoint.active_attack_types && lastPoint.active_attack_types.length > 0) {
      activeAttackType = lastPoint.active_attack_types.join("_");
    }

    let savedOnServer = false;
    let serverPath = "";
    try {
      const res = await fetch("/api/simulation/save_run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          history: state.liveHistory,
          map_name: state.currentMap || "custom",
          attack_type: activeAttackType
        })
      });
      const data = await res.json();
      if (res.ok && !data.error) {
        savedOnServer = true;
        serverPath = data.filepath;
      }
    } catch (err) {
      console.warn("Server-side save failed, falling back to local download:", err);
    }

    try {
      const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(state.liveHistory, null, 2));
      const downloadAnchor = document.createElement('a');
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const attackLabel = activeAttackType ? activeAttackType.replace(/[^a-zA-Z0-9]/g, "_") : "normal";
      
      downloadAnchor.setAttribute("href", dataStr);
      downloadAnchor.setAttribute("download", `run_${state.currentMap || "custom"}_${attackLabel}_${timestamp}.json`);
      document.body.appendChild(downloadAnchor);
      downloadAnchor.click();
      downloadAnchor.remove();

      if (savedOnServer) {
        alert(`Simulation run data exported successfully!\n\n1. Saved on Server: ${serverPath}\n2. Downloaded locally to your computer.`);
      } else {
        alert(`Simulation run data downloaded locally to your computer!\n(Note: Server-side write failed/skipped).`);
      }
    } catch (err) {
      console.error(err);
      alert(`Failed to export run data: ${err.message}`);
    }
  });

  applyAllViews();

  await preloadBaselineAtStartup();

  mapSelect?.addEventListener("change", async (e) => {
    const selectedMap = e.target.value;
    if (selectedMap) {
      await loadBaselineForMap(selectedMap);
    }
  });

  const launchSimBtn = document.getElementById("launchSimBtn");
  launchSimBtn?.addEventListener("click", async () => {
    const selectedMap = mapSelect?.value;
    if (!selectedMap) {
      alert("Please select a map first!");
      return;
    }

    const originalText = launchSimBtn.innerHTML;
    launchSimBtn.disabled = true;
    launchSimBtn.innerHTML = `
      <svg style="animation: spin 1s linear infinite;" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>
      Launching...
    `;

    try {
      const headlessVal = !!document.getElementById("headlessCheckbox")?.checked;
      const res = await fetch("/api/simulation/launch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ map_name: selectedMap, headless: headlessVal })
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        throw new Error(data.error || "Simulation launch failed.");
      }

      // Clear current histories to start fresh with new live data
      state.liveHistory = [];
      state.latestLive = null;

      // Load corresponding baseline
      await loadBaselineForMap(selectedMap, true);

      alert(`Simulation successfully launched for map: ${selectedMap.toUpperCase()}`);
    } catch (err) {
      console.error(err);
      alert(`Failed to launch simulation: ${err.message}`);
    } finally {
      launchSimBtn.disabled = false;
      launchSimBtn.innerHTML = originalText;
    }
  });

  document.querySelectorAll(".attack-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const attackType = btn.getAttribute("data-attack");
      if (!attackType) return;

      const originalText = btn.innerHTML;
      btn.disabled = true;
      btn.classList.add("loading");
      btn.innerHTML = `
        <svg style="animation: spin 1s linear infinite;" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>
        Injecting...
      `;

      try {
        const res = await fetch("/api/simulation/attack", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ 
            type: attackType,
            map_name: state.currentMap || "paris"
          })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
          throw new Error(data.error || "Attack injection failed.");
        }

        alert(`Successfully injected: ${btn.textContent.trim()}`);
      } catch (err) {
        console.error(err);
        alert(`Failed to inject attack: ${err.message}`);
      } finally {
        btn.disabled = false;
        btn.classList.remove("loading");
        btn.innerHTML = originalText;
      }
    });
  });

  bindSocket();

  const computeRocPrBtn = document.getElementById("computeRocPrBtn");
  computeRocPrBtn?.addEventListener("click", () => {
    computeAndPlotRocPr();
  });
});

let rocChart = null;
let prChart = null;

function computeAndPlotRocPr() {
  if (state.liveHistory.length === 0) {
    alert("No live simulation data available.");
    return;
  }
  if (!state.baselineHistory || state.baselineHistory.length === 0) {
    alert("No reference baseline map loaded. Please wait for the baseline data to load.");
    return;
  }
  
  // 1. Get downsampled histories up to 300s
  const baselineSampled = downsampleToSeconds(state.baselineHistory).filter(pt => getPointTime(pt) <= 300);
  const liveSampled = downsampleToSeconds(state.liveHistory).filter(pt => getPointTime(pt) <= 300);
  
  if (baselineSampled.length < 150) {
    alert(`Baseline history only has ${baselineSampled.length} seconds. We need at least 150s to perform classification.`);
    return;
  }
  if (liveSampled.length < 150) {
    alert(`Live history only has ${liveSampled.length} seconds. Please run the simulation longer.`);
    return;
  }

  const maxElapsed = Math.min(300, Math.floor(liveSampled.length));
  const numWindows = Math.floor(maxElapsed / 10);

  const baselineFiltered = baselineSampled.filter(pt => getPointTime(pt) <= maxElapsed);
  const liveFiltered = liveSampled.filter(pt => getPointTime(pt) <= maxElapsed);
  
  // 2. Extract window features dynamically
  const X_baseline = getWindowFeaturesJS(baselineFiltered, numWindows);
  const X_live = getWindowFeaturesJS(liveFiltered, numWindows);
  
  // 3. Compute mean and covariance of baseline
  const d = 5; // 5 features
  const n_base = X_baseline.length;
  
  // Mean vector
  const mu = Array(d).fill(0);
  for (let i = 0; i < n_base; i++) {
    for (let j = 0; j < d; j++) {
      mu[j] += X_baseline[i][j];
    }
  }
  for (let j = 0; j < d; j++) {
    mu[j] /= n_base;
  }
  
  // Covariance matrix
  const cov = [];
  for (let j = 0; j < d; j++) {
    cov[j] = Array(d).fill(0);
  }
  for (let j = 0; j < d; j++) {
    for (let k = 0; k < d; k++) {
      let sum = 0;
      for (let i = 0; i < n_base; i++) {
        sum += (X_baseline[i][j] - mu[j]) * (X_baseline[i][k] - mu[k]);
      }
      cov[j][k] = sum / (n_base - 1);
    }
  }
  // Regularize diagonal
  for (let j = 0; j < d; j++) {
    cov[j][j] += 1e-4;
  }
  
  // Invert covariance matrix
  let covInv;
  try {
    covInv = invertMatrixJS(cov);
  } catch (err) {
    console.error("Covariance inversion failed:", err);
    alert("Could not invert baseline covariance matrix. Regularizing further...");
    for (let j = 0; j < d; j++) {
      cov[j][j] += 1e-2;
    }
    covInv = invertMatrixJS(cov);
  }
  
  // 4. Compute Mahalanobis distances for each of the 20 windows in X_live
  const distances = [];
  const n_live = X_live.length;
  for (let i = 0; i < n_live; i++) {
    const diff = [];
    for (let j = 0; j < d; j++) {
      diff[j] = X_live[i][j] - mu[j];
    }
    const temp = Array(d).fill(0);
    for (let j = 0; j < d; j++) {
      for (let k = 0; k < d; k++) {
        temp[j] += diff[k] * covInv[k][j];
      }
    }
    let val = 0;
    for (let j = 0; j < d; j++) {
      val += temp[j] * diff[j];
    }
    distances.push(Math.sqrt(val));
  }
  
  // 5. Compute ground truth labels dynamically from live history
  const y_true = [];
  for (let w = 0; w < n_live; w++) {
    const windowPoints = liveFiltered.slice(w * 10, (w + 1) * 10);
    const hasAttack = windowPoints.some(pt => (pt.active_attack_count > 0) || (pt.active_attack_types && pt.active_attack_types.length > 0));
    y_true.push(hasAttack ? 1 : 0);
  }

  // Update attack window label dynamically in UI
  const attackTimes = liveFiltered.filter(pt => (pt.active_attack_count > 0) || (pt.active_attack_types && pt.active_attack_types.length > 0)).map(pt => getPointTime(pt));
  let attackLabelText = "No Attack Detected";
  if (attackTimes.length > 0) {
    const minTime = Math.min(...attackTimes);
    const maxTime = Math.max(...attackTimes);
    attackLabelText = `t = ${minTime.toFixed(0)}s to ${maxTime.toFixed(0)}s`;
  }
  const attackDetectionValEl = document.getElementById("attackDetectionVal");
  if (attackDetectionValEl) {
    attackDetectionValEl.textContent = attackLabelText;
  }
  
  // 6. Compute curves
  const thresholds = [];
  for (let i = 0; i <= 200; i++) {
    thresholds.push((i / 200) * 40.0);
  }
  
  const fprs = [];
  const tprs = [];
  const recalls = [];
  const precisions = [];
  
  for (const tau of thresholds) {
    let tp = 0, fp = 0, tn = 0, fn = 0;
    for (let i = 0; i < distances.length; i++) {
      const pred = distances[i] > tau ? 1 : 0;
      const y = y_true[i];
      if (pred === 1 && y === 1) tp++;
      else if (pred === 1 && y === 0) fp++;
      else if (pred === 0 && y === 0) tn++;
      else if (pred === 0 && y === 1) fn++;
    }
    const tpr = (tp + fn) > 0 ? (tp / (tp + fn)) : 0.0;
    const fpr = (fp + tn) > 0 ? (fp / (fp + tn)) : 0.0;
    const precision = (tp + fp) > 0 ? (tp / (tp + fp)) : 1.0;
    const recall = tpr;
    
    fprs.push(fpr);
    tprs.push(tpr);
    recalls.push(recall);
    precisions.push(precision);
  }
  
  // Calculate AUCs
  const aucRoc = computeAucJS(fprs, tprs);
  const aucPr = Math.min(computeAucJS(recalls, precisions), 1.0);
  
  document.getElementById("rocAucVal").textContent = aucRoc.toFixed(3);
  document.getElementById("prAucVal").textContent = aucPr.toFixed(3);
  
  // 7. Render Charts
  renderRocPrCharts(fprs, tprs, recalls, precisions);
  
  // Show container
  document.getElementById("rocPrContainer").style.display = "flex";
}

function getWindowFeaturesJS(sampled, numWindows) {
  const features = [];
  for (let w = 0; w < numWindows; w++) {
    const windowPoints = sampled.slice(w * 10, (w + 1) * 10);
    let sumStopped = 0, sumSpeed = 0, sumEB = 0, sumFuel = 0, sumCol = 0;
    for (const pt of windowPoints) {
      sumStopped += pt.stopped_ratio || 0;
      sumSpeed += pt.avg_speed || 0;
      const m = pt.metrics || {};
      sumEB += m.emergency_breaking || 0;
      sumFuel += m.fuel_consumption || 0;
      sumCol += m.collision || 0;
    }
    const n = windowPoints.length || 1;
    features.push([
      sumStopped / n,
      sumSpeed / n,
      sumEB / n,
      sumFuel / n,
      sumCol / n
    ]);
  }
  return features;
}

function invertMatrixJS(M) {
  const n = M.length;
  const I = [];
  for (let i = 0; i < n; i++) {
    I[i] = [];
    for (let j = 0; j < n; j++) {
      I[i][j] = (i === j) ? 1.0 : 0.0;
    }
  }
  const A = [];
  for (let i = 0; i < n; i++) {
    A[i] = [...M[i]];
  }
  for (let i = 0; i < n; i++) {
    let pivotRow = i;
    for (let r = i + 1; r < n; r++) {
      if (Math.abs(A[r][i]) > Math.abs(A[pivotRow][i])) {
        pivotRow = r;
      }
    }
    if (pivotRow !== i) {
      let temp = A[i]; A[i] = A[pivotRow]; A[pivotRow] = temp;
      temp = I[i]; I[i] = I[pivotRow]; I[pivotRow] = temp;
    }
    const pivot = A[i][i];
    if (Math.abs(pivot) < 1e-12) {
      throw new Error("Matrix is singular");
    }
    for (let j = 0; j < n; j++) {
      A[i][j] /= pivot;
      I[i][j] /= pivot;
    }
    for (let r = 0; r < n; r++) {
      if (r === i) continue;
      const factor = A[r][i];
      for (let j = 0; j < n; j++) {
        A[r][j] -= factor * A[i][j];
        I[r][j] -= factor * I[i][j];
      }
    }
  }
  return I;
}

function computeAucJS(x, y) {
  const points = x.map((xv, idx) => ({ x: xv, y: y[idx] }));
  points.sort((a, b) => a.x - b.x);
  
  let auc = 0.0;
  for (let i = 0; i < points.length - 1; i++) {
    const dx = points[i+1].x - points[i].x;
    const meanY = (points[i+1].y + points[i].y) / 2.0;
    auc += dx * meanY;
  }
  return auc;
}

function renderRocPrCharts(fprs, tprs, recalls, precisions) {
  const rocPoints = fprs.map((f, i) => ({ x: f, y: tprs[i] }));
  rocPoints.sort((a, b) => {
    if (Math.abs(a.x - b.x) < 1e-9) return a.y - b.y;
    return a.x - b.x;
  });
  
  const prPoints = recalls.map((r, i) => ({ x: r, y: precisions[i] }));
  prPoints.sort((a, b) => {
    if (Math.abs(a.x - b.x) < 1e-9) return a.y - b.y;
    return a.x - b.x;
  });

  if (rocChart) rocChart.destroy();
  if (prChart) prChart.destroy();

  const ctxRoc = document.getElementById("rocChart").getContext("2d");
  rocChart = new Chart(ctxRoc, {
    type: "line",
    data: {
      datasets: [
        {
          label: "ROC Curve",
          data: rocPoints,
          borderColor: "#e11d48",
          borderWidth: 2.5,
          fill: false,
          tension: 0.1,
          pointRadius: 1.5,
        },
        {
          label: "Random Guess",
          data: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
          borderColor: "#94a3b8",
          borderWidth: 1.5,
          borderDash: [5, 5],
          fill: false,
          pointRadius: 0,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: "False Positive Rate (FPR)", font: { family: "Outfit", size: 10, weight: "bold" } },
          min: 0,
          max: 1,
        },
        y: {
          type: "linear",
          title: { display: true, text: "True Positive Rate (TPR)", font: { family: "Outfit", size: 10, weight: "bold" } },
          min: 0,
          max: 1,
        }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });

  const ctxPr = document.getElementById("prChart").getContext("2d");
  prChart = new Chart(ctxPr, {
    type: "line",
    data: {
      datasets: [
        {
          label: "PR Curve",
          data: prPoints,
          borderColor: "#0284c7",
          borderWidth: 2.5,
          fill: false,
          tension: 0.1,
          pointRadius: 1.5,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          type: "linear",
          title: { display: true, text: "Recall", font: { family: "Outfit", size: 10, weight: "bold" } },
          min: 0,
          max: 1,
        },
        y: {
          type: "linear",
          title: { display: true, text: "Precision", font: { family: "Outfit", size: 10, weight: "bold" } },
          min: 0,
          max: 1,
        }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}