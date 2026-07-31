# TwinSentinel Project Instructions

## What This Is
A VANET (vehicular network) security research testbed: SUMO traffic simulations of Paris/Berlin/Luxembourg driven live via TraCI, exposed as MCP tools, attacked by adversarial ML/V2X attack modules, monitored via a live Node dashboard, and evaluated offline with a statistical (ROC/PR, Cliff's delta, Holm-Bonferroni) post-processing pipeline. See `README.md` for the full architecture writeup.

## Tech Stack
| Layer | Technology |
|-------|-----------|
| Simulation orchestration | Python + FastMCP (`MCP_server.py`) + SUMO/TraCI |
| Agents | Python + httpx, LLM decisions via local Ollama (`qwen:1.8b`) |
| Live dashboard | Node.js, Express, Socket.IO (`node_dashboard/`) |
| Data/analysis | pandas, numpy, scipy, matplotlib (no sklearn — metrics are hand-rolled) |
| Data storage | Flat JSON files (`runs/`, `baselines/`), tracked via Git LFS |

No formal test suite exists — `scratch/` holds ad hoc verification/debug scripts (`test_*.py`), not a pytest suite. No linter/formatter config is present; match surrounding code style exactly rather than introducing one.

## Build & Run
```bash
source .venv/bin/activate && pip install -r requirements.txt   # Python deps (note: requirements.txt is UTF-16 encoded)
python MCP_server.py                                            # starts SUMO/TraCI + MCP tool server
cd node_dashboard && npm install && npm start                   # dashboard at localhost:3100
```
Post-processing (run after simulation campaigns exist under `runs/`):
```bash
python post_treatment/table/analyze_berlin_campaign.py   # detection metrics + stats
python post_treatment/figure/generate_berlin_figure_and_table.py  # ROC/PR figures + tables
```
(Same pattern for `lux_campaign`/`_lux_` and `analyze_campaign.py`/`generate_figure_and_table.py` for Paris.)

## Project Structure
| Path | Purpose |
|------|---------|
| `MCP_server.py` | Single large file: SUMO/TraCI lifecycle, all `@mcp.tool` endpoints (launch maps, spawn vehicles, run attacks, stream metrics, manage baselines) |
| `agents/redagent.py` / `agents/blueagent.py` | LLM-driven MCP clients for offense/defense decisions |
| `ATTACKS/` | Attack implementations: `threat_models.py` (abstract vehicle ML models + gradient estimation), `universal_perturbation.py`, `adversarial_sensor_spoofing.py`, `sumo_adapter.py` |
| `adversarial_attack/` | Separate small fastagent pair exploring LLM prompt-injection resistance — not part of the main VANET attack flow |
| `node_dashboard/` | `server.js` (Express/Socket.IO backend) + `public/app.js`/`index.html` (frontend); `app_old.js`/`app-old-backup.js` are dead code, don't build on them |
| `maps/{paris,berlin,luxembourg,basic_simulation}/` | SUMO `.net.xml`/`.rou.gz`/`.sumocfg` topology per city |
| `baselines/` | Benign reference runs, ~20 seeds per city, Git LFS |
| `runs/` | Recorded attack-run logs (attack × severity level L1-L3 × seed × city), Git LFS |
| `post_treatment/table/` | Per-city campaign analysis: KPI windowing, anomaly scoring, ROC/PR/AUC, Cliff's delta, Holm-Bonferroni correction |
| `post_treatment/figure/` | Per-city ROC/PR curve and detectability-frontier figure generation |
| `scripts/` | Baseline-generation helper (`generate_baseline_basic.py`) |
| `scratch/` | Dev/debug/one-off scripts — not production code, treat as disposable |

## Conventions
- **Per-city triplication**: Paris/Berlin/Luxembourg each get their own near-duplicate script (`analyze_campaign.py`/`analyze_berlin_campaign.py`/`analyze_lux_campaign.py`, similarly for figure generation). When fixing a bug in the analysis/figure logic, check whether the same bug exists in the other two city variants.
- **MCP tools** are registered with `@mcp.tool("tool_name", description="...")` in `MCP_server.py`; keep new tools consistent with the existing docstring/description style since these descriptions are read by LLM agents at runtime.
- **Metrics** are defined once in `METRIC_KEYS`/`METRIC_DOCUMENTATION` in `MCP_server.py` — add new metrics there, not ad hoc.
- **Attack severity** is expressed as levels L1/L2/L3 baked into run filenames (`run_berlin_fake_emergency_L1_seed_1.json`) — preserve this naming when adding new attack/run types.
- **Large/binary data** (SUMO maps, `runs/*.json`, `baselines/*.json`) must go through Git LFS — check `.gitattributes` before adding new large files.
- **Commits**: descriptive, present-tense multi-clause summaries (e.g. "Refactor workspace organization, add Berlin & Luxembourg campaign results..."), not conventional-commits style.
