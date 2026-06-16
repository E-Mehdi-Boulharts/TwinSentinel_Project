#!/usr/bin/env python3
"""
Quick verification script to check Universal Perturbation Attack is available
and can be called via MCP.

Usage:
    python3 verify_universal_perturbation.py
"""

import requests
import json
import sys

MCP_URL = "http://localhost:8000/mcp/"
DASHBOARD_URL = "http://localhost:3100/api/health"

def check_mcp_health():
    """Check if MCP server is running."""
    try:
        resp = requests.get("http://localhost:8000/api/health", timeout=2)
        if resp.status_code == 200:
            print("✅ MCP Server: RUNNING")
            return True
        else:
            print("❌ MCP Server: NOT RESPONDING")
            return False
    except Exception as e:
        print(f"❌ MCP Server: {e}")
        return False

def check_dashboard_health():
    """Check if Dashboard is running."""
    try:
        resp = requests.get(DASHBOARD_URL, timeout=2)
        if resp.status_code == 200:
            print("✅ Dashboard: RUNNING")
            return True
        else:
            print("⚠️  Dashboard: NOT RESPONDING")
            return False
    except:
        print("⚠️  Dashboard: NOT RUNNING (will start during test)")
        return None

def test_attack_tool():
    """Test that universal_perturbation_attack tool is available."""
    print("\n📋 Testing Universal Perturbation Attack Tool...")
    
    # First, launch basic simulation
    print("\n  [1/3] Launching SUMO Basic simulation...")
    launch_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "launch_basic_simulation",
            "arguments": {}
        }
    }
    
    try:
        resp = requests.post(MCP_URL, json=launch_payload, timeout=5)
        if resp.status_code == 200:
            print("  ✅ Simulation launch initiated")
        else:
            print(f"  ⚠️  Simulation launch returned {resp.status_code}")
            print(f"     Response: {resp.text[:200]}")
    except Exception as e:
        print(f"  ❌ Failed to launch simulation: {e}")
        return False
    
    # Wait for SUMO to start
    import time
    print("  ⏳ Waiting 8 seconds for SUMO to initialize...")
    time.sleep(8)
    
    # Start simulation
    print("\n  [2/3] Starting simulation loop...")
    start_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "start_simulation",
            "arguments": {}
        }
    }
    
    try:
        resp = requests.post(MCP_URL, json=start_payload, timeout=5)
        if resp.status_code == 200:
            print("  ✅ Simulation loop started")
        else:
            print(f"  ⚠️  Simulation start returned {resp.status_code}")
    except Exception as e:
        print(f"  ❌ Failed to start simulation: {e}")
        return False
    
    # Wait for vehicles to enter
    print("  ⏳ Waiting 5 seconds for vehicles to enter...")
    time.sleep(5)
    
    # Test universal perturbation attack
    print("\n  [3/3] Launching Universal Perturbation Attack...")
    attack_payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "universal_perturbation_attack",
            "arguments": {
                "duration": 10,
                "epsilon": 0.5,
                "scale_position": 0.7,
                "scale_velocity": 0.4
            }
        }
    }
    
    try:
        resp = requests.post(MCP_URL, json=attack_payload, timeout=5)
        if resp.status_code == 200:
            result = resp.json()
            print("  ✅ Attack tool executed successfully!")
            print(f"\n  Response:")
            print(f"    Status: {result.get('result', {}).get('status', 'N/A')}")
            print(f"    Target Count: {result.get('result', {}).get('target_count', 'N/A')}")
            print(f"    Duration: {result.get('result', {}).get('duration', 'N/A')}s")
            print(f"    Epsilon: {result.get('result', {}).get('epsilon', 'N/A')}")
            
            perturbation = result.get('result', {}).get('perturbation', {})
            if perturbation:
                print(f"\n  Perturbation δ_u:")
                print(f"    Position: {perturbation.get('position', 'N/A')}")
                print(f"    Velocity: {perturbation.get('velocity', 'N/A')}")
                print(f"    Heading: {perturbation.get('heading', 'N/A'):.4f} rad")
            
            return True
        else:
            print(f"  ❌ Attack tool returned {resp.status_code}")
            print(f"     Response: {resp.text}")
            return False
    except Exception as e:
        print(f"  ❌ Failed to call attack tool: {e}")
        return False

def main():
    """Main verification routine."""
    print("═" * 60)
    print("  UNIVERSAL PERTURBATION ATTACK - VERIFICATION SCRIPT")
    print("═" * 60)
    
    print("\n🔍 Checking system components...")
    
    # Check services
    mcp_ok = check_mcp_health()
    dashboard_ok = check_dashboard_health()
    
    if not mcp_ok:
        print("\n❌ MCP Server is not running!")
        print("   Start it with: python3 MCP_server.py")
        sys.exit(1)
    
    # Test attack tool
    if test_attack_tool():
        print("\n" + "═" * 60)
        print("  ✅ UNIVERSAL PERTURBATION ATTACK - VERIFIED!")
        print("═" * 60)
        print("\n🎯 Next Steps:")
        print("   1. Open Dashboard: http://localhost:3100")
        print("   2. Watch for vehicle color changes (orange)")
        print("   3. Observe metric changes in charts")
        print("   4. Check logs for '🔵 ATTACK STARTED' messages")
        print("\n📚 Documentation:")
        print("   - ATTACKS_TESTING_GUIDE.md")
        print("   - UNIVERSAL_PERTURBATION_SUMMARY.md")
        print("   - test_universal_perturbation.sh")
        print()
        return 0
    else:
        print("\n❌ UNIVERSAL PERTURBATION ATTACK - VERIFICATION FAILED!")
        print("   Check MCP server logs for details.")
        sys.exit(1)

if __name__ == "__main__":
    main()
