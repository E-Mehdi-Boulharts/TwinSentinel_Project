# TwinSentinel: AI-Driven Intrusion Detection & Adversarial Simulation in VANETs

TwinSentinel is a comprehensive pairs-simulation environment designed to evaluate and secure Vehicular Ad-Hoc Networks (VANETs). The platform enables the orchestration of realistic vehicular networks, the execution of multi-layer cyber attacks (Red Agent), and the real-time detection and mitigation of threats (Blue Agent) using Machine Learning and cryptographic verification.

---

## 🚀 Key Features

*   **Decoupled Map & Seed Workflows**: Run simulations on distinct city topologies (**Paris**, **Berlin**, and **Luxembourg**) while testing against specific baseline seeds ($N=1\text{ to }20$) to maintain experimental consistency.
*   **Adversarial Campaign Simulation (Red vs Blue)**:
    *   **Red Agent (Attacks)**: Multi-layer threat models including Sybil attacks, DDoS, GPS spoofing, and sensor noise injection.
    *   **Blue Agent (Defense)**: Real-time anomaly detection, physical speed deviations tracking, and adaptive network/traffic-light overrides.
*   **TwinSentinel Live Monitor**: A Web-based Node.js dashboard with live telemetry (speed, fuel consumption, stopped vehicle ratio, jam counts) mapped dynamically against selected reference baselines.
*   **Headless & GUI Execution**: Run simulations in headless mode for high-throughput batch experiments, or in GUI mode to visually inspect traffic behavior in SUMO.
*   **Automated Campaign Reporting**: Automated statistical testing (Holm-Bonferroni correction, Cliff's delta effect sizes) and report generation directly to formatted `.docx` and `.pdf` documents.

---

## 📂 Workspace Organization

The workspace is structured to keep data, source code, and post-processing separate and clean:

```bash
/home/mehdi/VANET_Project/Docker_files/
├── MCP_server.py             # Model Context Protocol (MCP) Python orchestration server
├── baselines/                # Stored benign baseline JSON runs (Seeds 1–20) for Paris, Berlin, & Luxembourg
├── runs/                     # Directory where active and historical simulation logs are exported
├── maps/                     # SUMO network topology, trip files, and configurations for each map
├── node_dashboard/           # Node.js backend (server.js) and frontend dashboard (public/app.js)
├── post_treatment/           # Post-processing files, split into:
│   ├── figure/               # ROC curves, detectability frontiers, and figure generators
│   └── table/                # Campaign metrics JSONs, Table V calculations, and statistical scripts
├── report/                   # Scientific analysis reports:
│   ├── scripts/              # Report generation scripts (Docx compilers, MD parsers)
│   └── *.docx, *.pdf, *.md   # Final formatted Word documents and report files
├── scripts/                  # Base simulation execution and helper scripts
├── scratch/                  # Temporary development scratchpad and tests
└── .venv/                    # Python virtual environment containing simulation dependencies
```

---

## ⚙️ Getting Started

### 1. Prerequisites
Ensure you have **SUMO (Simulation of Urban MObility)** version 1.22+ installed on your system.

### 2. Environment Activation & Dependencies
Activate the virtual environment and install standard requirements:
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Launching the TwinSentinel Suite

First, start the Python MCP Server which manages the SUMO subprocesses and handles the TraCI connection:
```bash
python MCP_server.py
```

In a separate terminal, navigate to the dashboard directory and launch the web server:
```bash
cd node_dashboard
npm install
npm start
```

Open your browser and navigate to **`http://localhost:3100`** to view the live dashboard.

---

## 📊 Live Dashboard Workflow

1.  **Select Topology (Map)**: Choose between **Paris**, **Berlin**, **Luxembourg**, or **Basic Simulation**.
2.  **Select Baseline (Reference Seed)**: Choose the specific seed reference you want to compare the live run against (e.g. `BERLIN Seed 5`).
3.  **Simulation Mode**: Toggle **Headless (Fast)** mode off to launch the SUMO GUI interface visually, or leave it checked for background runs.
4.  **Launch**: Click the **Launch** button to start the simulator. The dashboard will automatically stream real-time metrics.
5.  **Inject Attacks**: Trigger adversarial attacks (Sybil, DDoS, GPS noise) dynamically using the Attack Injection controls on the dashboard.
6.  **Evaluate & Save**: Watch the live metrics diverge from the benign reference line on the chart. Save the run data directly using **Export Run Data**.

---

## 📈 Post-Processing & Reporting

Offline evaluation and campaign analysis are run via standalone post-processing scripts:

### 1. Re-run Campaign Anomaly Analysis
Processes all simulation runs, calculates detection accuracy metrics, and exports statistical summaries:
```bash
python post_treatment/table/analyze_berlin_campaign.py
python post_treatment/table/analyze_lux_campaign.py
python post_treatment/table/analyze_campaign.py          # Paris
```

### 2. Regenerate Curve Plots & Results Table
Generates the ROC-PR curves and `detailed_table_results.json` files for paper publication:
```bash
python post_treatment/figure/generate_berlin_figure9_and_table.py
python post_treatment/figure/generate_lux_figure9_and_table.py
python post_treatment/figure/generate_figure9_and_table.py       # Paris
```

### 3. Generate Scientific Word Reports
Compiles the post-processing metrics and figures directly into the official `.docx` report documents:
```bash
python report/scripts/generate_berlin_docx_report.py
python report/scripts/generate_lux_docx_report.py
python report/scripts/generate_docx_report.py            # Paris
```
The final reports are compiled and placed directly inside the `report/` directory.

---

## 📝 Contributors
*   **Nassim Anemiche**
*   **Mehdi Boulharts**
