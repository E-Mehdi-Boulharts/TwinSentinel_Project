# Security-agent-in-VANETs-AI-Based-Intrusion-Detection

This internship project implements AI-driven Red (attack) and Blue (defense) agents for securing Vehicular Ad-hoc Networks (VANETs) using SUMO and Secure Multiparty Computation (MPC).

## 🚨 Latest Fixes (April 29, 2026)

**Three critical issues have been resolved:**

1. ✅ **Sybil Attack Error** - Fixed `traci.vehicle.exists()` API error (Line 634)
2. ✅ **Traffic Light Freeze** - Fixed lights staying frozen after attack expires (Lines 214, 218, 586)
3. ✅ **Defend Ineffective** - Upgraded defense to two-step unlock + optimize (Lines 837+)

See [FIXES_REPORT.md](FIXES_REPORT.md) for details.


## Project Overview

This project simulates cyber attacks and defenses in VANETs through:
- **Red Agent**: Launches multi-layer attacks (Sybil, DDoS, GPS spoofing) across OSI layers.
- **Blue Agent**: AI-based intrusion detection system using ML/DL models.
- **Privacy Layer**: MPC protocols for secure vehicle-to-infrastructure and vehicle-to-vehicle communication.

## Key Features

- **Multi-Layer Attack Simulation**
  - Network, physical, and application layer attacks.
  - Realistic threat modeling in vehicular networks.
  
- **AI-Powered Enhancement**
  - Optimizes traffic flow using AI techniques.

- **Full-Stack Simulation**
  - SUMO for traffic dynamics.

- **Model Context Protocol**
  - Enables agents to have control over the tools.

## Tools & Technologies

| Category              | Technologies                                                                 |
|-----------------------|------------------------------------------------------------------------------|
| **Simulation**        |  SUMO 1.22                                         |
| **AI/ML**            | fast-agent,fast-mcp                                   |
| **Languages**         | HTML, Python 3.10+                                         |
| **Optional Tools**    | Ollama                                     |

## Installation

### Prerequisites
- Python 3.10+
- SUMO 1.22

### Setup
1. **Clone the Repository**
   ```bash
   git clone https://github.com/KyleDottin/Security-agent-in-VANETs-AI-Based-Intrusion-Detection.git
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the MCP Server**
   ```bash
   python MCP/MCP_server.py
   ```

## Contributors
- **Kyle Dottin**
- **Nassim Anemiche**

## Realtime Dashboard (Node.js)

You can now stream live simulation KPIs and compare a baseline run vs an attacked run.

### 1) Start MCP Server

```bash
cd /home/mehdi/VANET_Project/Docker_files
source .venv/bin/activate
python MCP_server.py
```

### 2) Start Node Dashboard

```bash
cd /home/mehdi/VANET_Project/Docker_files/node_dashboard
npm install
npm start
```

Open `http://localhost:3100`.

### 3) Comparison Workflow

1. Launch simulation and let traffic stabilize (no attack).
2. Click **Capture Baseline**.
3. Trigger attack(s) from red agent.
4. Click **Capture Attacked**.
5. Click **Compare** to get KPI deltas.

### Live KPIs exposed by MCP

- `realtime_metrics`: latest + rolling window metrics (speed, stopped ratio, jams, active attacks)
- `capture_benchmark`: stores a named snapshot (e.g. `baseline`, `attacked`)
- `compare_benchmarks`: computes delta and delta % for key KPIs


