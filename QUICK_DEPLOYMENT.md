# 🚀 QUICK DEPLOYMENT GUIDE - Universal Perturbation Attack

## ⚡ 30-Second Setup

```bash
# Terminal 1: MCP Server
cd /home/mehdi/VANET_Project/Docker_files
python3 MCP_server.py

# Terminal 2: Dashboard
cd /home/mehdi/VANET_Project/Docker_files/node_dashboard
npm start

# Terminal 3: Run Verification
python3 verify_universal_perturbation.py
```

## 📱 Direct cURL Examples

### Example 1: Basic Attack (30 seconds)
```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "universal_perturbation_attack",
      "arguments": {
        "duration": 30
      }
    }
  }'
```

### Example 2: Intense Attack (100 seconds, high epsilon)
```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "universal_perturbation_attack",
      "arguments": {
        "duration": 100,
        "epsilon": 0.8,
        "scale_position": 1.0,
        "scale_velocity": 0.7
      }
    }
  }'
```

### Example 3: Light Attack (15 seconds, low epsilon)
```bash
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "universal_perturbation_attack",
      "arguments": {
        "duration": 15,
        "epsilon": 0.15,
        "scale_position": 0.3,
        "scale_velocity": 0.1
      }
    }
  }'
```

## 🔄 Complete Test Workflow

```bash
# Step 1: Launch SUMO simulation
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"launch_basic_simulation","arguments":{}}}'

# Wait 8 seconds
sleep 8

# Step 2: Start simulation loop
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"start_simulation","arguments":{}}}'

# Wait 5 seconds for vehicles to enter
sleep 5

# Step 3: Launch Universal Perturbation Attack
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"universal_perturbation_attack","arguments":{"duration":60,"epsilon":0.5}}}'

# Step 4: Monitor for 65 seconds
sleep 65

# Step 5: Get final metrics
curl http://localhost:3100/api/baseline/current | jq '.' | tail -20
```

## 📊 Parameter Tuning Guide

### Light Perturbation (Subtle Impact)
- `epsilon`: 0.1 - 0.2
- `scale_position`: 0.2 - 0.4
- `scale_velocity`: 0.05 - 0.15
- **Result**: ~5-10% metric degradation

### Medium Perturbation (Moderate Impact)
- `epsilon`: 0.3 - 0.5
- `scale_position`: 0.5 - 0.7
- `scale_velocity`: 0.3 - 0.5
- **Result**: ~20-40% metric degradation

### Heavy Perturbation (Severe Impact)
- `epsilon`: 0.6 - 1.0
- `scale_position`: 0.8 - 1.0
- `scale_velocity`: 0.6 - 0.9
- **Result**: ~50-80% metric degradation

## 🎬 Execution Scenarios

### Scenario 1: Single Attack
```bash
# Just Universal Perturbation
universal_perturbation_attack(duration=60, epsilon=0.5)
```

### Scenario 2: Sequential Attacks
```bash
# Universal Perturbation for 30s
universal_perturbation_attack(duration=30, epsilon=0.5)

# After 15s, add Traffic Light Tampering
# (while universal perturbation still active)
sleep 15
traffic_light_tampering_attack(duration=15)

# Result: Combined effect for 15s, then just TL tampering
```

### Scenario 3: Rapid Succession
```bash
# Attack 1
universal_perturbation_attack(duration=20)
sleep 25

# Attack 2
sybil_attack(params={...})
sleep 25

# Attack 3
fake_safety_message_attack(params={...})
```

## 📈 Dashboard Navigation

1. **Top Cards**:
   - Simulated Time: Current simulation time
   - Vehicles: Active vehicle count
   - Active Attacks: Currently running attacks (shows "universal_perturbation")
   - Avg Speed: Average vehicle speed
   - Stop Ratio: Percentage of stopped vehicles

2. **Metric Grid** (10 metrics):
   - Shows **Live Avg** (during attack)
   - Shows **Baseline Avg** (baseline simulation)
   - Directional arrows: ↑ red (worse) or ↓ green (better)

3. **Comparison Table**:
   - Metric | Live | Baseline | Delta | Trend
   - Scroll to see all 10 metrics

4. **Charts**:
   - Green line: Baseline
   - Orange line: Live (under attack)
   - Visible divergence during attack

## 🔍 Log Output to Watch For

### Attack Start
```
🔵 ATTACK STARTED: Universal Perturbation on 42 vehicles for 60s
   Perturbation δ_u = pos:[-0.15, 0.12], vel:[0.08, -0.06], heading:0.0342
```

### Attack Progress (every 100 steps)
```
📊 Metrics collected: Σ10 metrics, Ø avg_speed: 7.2 m/s, vehicles: 42
   Active attacks: ['universal_perturbation']
   Step: 1200 | Sim time: 60.0s | Agents: 42 | Avg speed: 7.2 m/s | Stop ratio: 0.35
```

### Attack End
```
✓ ATTACK ENDED: Universal Perturbation completed on 42 vehicles
```

## ✅ Verification Checklist

- [ ] MCP server is running (`python3 MCP_server.py`)
- [ ] Dashboard is running (`npm start` in node_dashboard)
- [ ] SUMO simulation launched (check in `/app/results/sumo_logs/`)
- [ ] Dashboard shows "universal_perturbation" in attacks card
- [ ] Vehicle colors change to orange during attack
- [ ] Metrics diverge between baseline and live
- [ ] Attack completes after specified duration
- [ ] Vehicles return to normal color

## 🐛 Troubleshooting

### "No vehicles in simulation"
- Solution: SUMO needs time to load vehicles
- Action: Wait 10-15 seconds after launch_*_simulation before calling attack

### "TraCI connection is not active"
- Solution: Need to launch SUMO first
- Action: Call launch_basic_simulation (or Paris/Berlin/Luxembourg)

### Attack not visible in dashboard
- Solution: Dashboard may need refresh
- Action: Press F5 or close/reopen tab

### Metrics not changing
- Solution: Attack may not be affecting simulation yet
- Action: Check console logs for "ATTACK STARTED" message

### Orange vehicles not appearing
- Solution: SUMO GUI may not be running
- Action: Either launch SUMO with GUI or check logs only

## 📚 Documentation Files

```
/home/mehdi/VANET_Project/Docker_files/
├── ATTACKS_TESTING_GUIDE.md              ← Full testing guide
├── UNIVERSAL_PERTURBATION_SUMMARY.md     ← Architecture & design
├── test_universal_perturbation.sh        ← Automated test script
├── verify_universal_perturbation.py      ← Python verification
└── QUICK_DEPLOYMENT.md                   ← This file
```

## 🎓 Understanding the Attack

**What it does**:
- Generates a single universal perturbation δ_u
- Applies the SAME perturbation to ALL vehicles
- Manifests as reduced speed for each vehicle
- Causes traffic degradation across entire fleet

**Why it's effective**:
- Coordinated (all vehicles affected equally)
- Subtle (not obvious individual vehicle failure)
- Scalable (same δ_u works for any fleet size)
- Realistic (could model sensor attacks or communication jamming)

**Detection Difficulty**:
- Medium: Individual vehicle behaviors are normal
- High: Coordinated fleet degradation is unusual
- Statistical methods can detect the coordinated pattern

## 🚦 Next Attacks to Implement

From your image, priority order:
1. ✅ **UniversalPerturbation** (DONE!)
2. **BadNets (Backdoor)** - Model poisoning
3. **Clean-Label Backdoor** - Data poisoning
4. **HopSkipJump** - Adversarial perturbation refinement
5. **BoundaryAttack** - Decision boundary attacks
6. **SquareAttack** - Black-box attacks

---

**Status**: Ready for operational testing ✅

For detailed parameter explanations, see: **UNIVERSAL_PERTURBATION_SUMMARY.md**
