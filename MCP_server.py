# =============================
#         IMPORTS
# =============================
import os
import socket
socket.setdefaulttimeout(300.0)
import subprocess
import sys
import threading
import math
from datetime import datetime
import traci
from fastmcp import FastMCP
from pydantic import BaseModel
import xml.etree.ElementTree as ET
import random
import time
import pandas as pd
from datetime import datetime
import openpyxl
from collections import deque
import json
import numpy as np
from ATTACKS.threat_models import VehicleSafetyModel, VehicleTrajectoryModel
from ATTACKS.hopskipjump import HopSkipJumpAttack
from ATTACKS.backdoor_attack import create_backdoor_attack
from ATTACKS.clean_label_feature_collision import create_clean_label_feature_collision_attack
from ATTACKS.knockoff_nets import KnockoffNetsAttack
from ATTACKS.attribute_inference_black_box import AttributeInferenceBlackBoxAttack

# =============================
#       GLOBAL VARIABLES
# =============================
latest_data = None
running = False
simulation_thread = None
traci_connection = None
launch_error = None
launching = False
attack_override = False
step_counter = 0
simulation_data = []
energy = 0.0
first_time = 0
traffic = 0
vehicle_stats = {}  # vehicle_id -> dict with stats
location_jams = {}  # edge_id -> jam info
active_attacks = []  # List of active attack states: {'type': str, 'start_time': float, 'duration': float, 'data': dict}
realtime_metrics = deque(maxlen=15000)
benchmark_snapshots = {}
baseline_reference = None
baseline_reference_map = None
baseline_cache = {}
current_map_name = "basic"  # Track which map is currently running
current_simulation_seed = 42  # Track active simulation seed
metrics_lock = threading.Lock()
METRIC_KEYS = [
    "fuel_consumption",
    "co2",
    "noise",
    "jam",
    "emergency_breaking",
    "pm",
    "nox",
    "congestion",
    "collision",
    "nvmoc",
]

METRIC_DOCUMENTATION = {
    "fuel_consumption": {
        "label": "Fuel Consumption",
        "unit": "L",
        "source": "traci.vehicle.getFuelConsumption() / 1000 (sum per step)",
        "description": "Total fuel consumed by all vehicles in liters. Retrieved via TraCI and accumulated across the fleet.",
    },
    "co2": {
        "label": "CO2 Emissions",
        "unit": "g",
        "source": "traci.vehicle.getCO2Emission() (sum per step)",
        "description": "Total CO2 emissions in grams. Sum of all vehicles' CO2 output per simulation step.",
    },
    "noise": {
        "label": "Noise Emission",
        "unit": "dB",
        "source": "traci.vehicle.getNoiseEmission() (sum per step)",
        "description": "Total noise level in dB. Aggregated from all active vehicles in the simulation.",
    },
    "jam": {
        "label": "Jammed Lanes",
        "unit": "count",
        "source": "count of lanes where jam_start is not None",
        "description": "Number of road lanes currently experiencing congestion (speed < 0.5 m/s for >10 sec).",
    },
    "emergency_breaking": {
        "label": "Emergency Braking Events",
        "unit": "events",
        "source": "count of vehicles with acceleration < -3.0 m/s²",
        "description": "Number of vehicles performing emergency braking (deceleration > 3.0 m/s²) per step.",
    },
    "pm": {
        "label": "Particulate Matter",
        "unit": "g",
        "source": "traci.vehicle.getPMxEmission() (sum per step)",
        "description": "Total particulate matter emissions in grams. PM10 and PM2.5 aggregated.",
    },
    "nox": {
        "label": "NOx Emissions",
        "unit": "g",
        "source": "traci.vehicle.getNOxEmission() (sum per step)",
        "description": "Total nitrogen oxides (NOx) emissions in grams from all vehicles.",
    },
    "congestion": {
        "label": "Congestion Ratio",
        "unit": "ratio (0-1)",
        "source": "stopped_count / vehicle_count",
        "description": "Fraction of vehicles moving at <0.5 m/s. High value = severe congestion.",
    },
    "collision": {
        "label": "Collisions",
        "unit": "events",
        "source": "traci.simulation.getCollidingVehiclesNumber()",
        "description": "Number of colliding vehicles detected by SUMO per step.",
    },
    "nvmoc": {
        "label": "NMVOC (Volatile Organic Compounds)",
        "unit": "g",
        "source": "traci.vehicle.getHCEmission() (sum per step)",
        "description": "Total hydrocarbon emissions (HC / VOCs) in grams from all vehicles.",
    },
}

import logging
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
# Get current working directory
current_dir = os.path.dirname(os.path.abspath(__file__))

BASELINE_DIR = os.path.join(current_dir, "baselines")
if not os.path.exists(BASELINE_DIR):
    os.makedirs(BASELINE_DIR, exist_ok=True)

EXTRACTIONS_DIR = os.path.join(current_dir, "extractions")
if not os.path.exists(EXTRACTIONS_DIR):
    os.makedirs(EXTRACTIONS_DIR, exist_ok=True)

# Keep setup simple: inside Docker we connect to remote SUMO, on Windows use local path
if os.path.exists("/.dockerenv"):
    sumo_binary = None  # Don't launch SUMO, just connect to it
    # For Linux Docker, use the host's IP instead of host.docker.internal
    sumo_host = os.getenv("SUMO_HOST", "192.168.64.8")  # Linux host IP - configure if different
    sumo_port = int(os.getenv("SUMO_PORT", "55000"))
elif sys.platform == "win32":
    sumo_binary = r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo-gui.exe"
else:
    sumo_binary = "sumo-gui"  # Linux
    sumo_host = "localhost"
    sumo_port = 55000
# Add paths for files in the different map folders
# For Docker: use /app paths, For local: use actual paths
if os.path.exists("/.dockerenv"):
    map_path_basic = os.path.join(current_dir, "maps", "basic_simulation", "osm.sumocfg")
else:
    # Local Ubuntu: use absolute path or detect from environment
    map_path_basic = os.getenv("SUMO_MAP_BASIC", "/home/mehdi/VANET_Project/Docker_files/maps/basic_simulation/osm.sumocfg")

map_path_paris = os.path.join(current_dir, "maps", "paris", "map.sumocfg")
map_path_berlin = os.path.join(current_dir, "maps", "berlin", "berlin.sumocfg")
map_path_luxembourg = os.path.join(current_dir, "maps", "luxembourg", "dua.static.sumocfg")


# =============================
#     MCP SERVER INIT
# =============================
mcp = FastMCP("Demo")

# =============================
#           CLASSES
# =============================
class Vehicle(BaseModel):
    vehicle_id: str
    time_departure: float
    road_depart: str
    road_arrival: str

class AttackReport(BaseModel):
    attack_id: str
    vehicle_id: str
    agent_id: str
    details: str

# =============================
#         FUNCTIONS
# =============================


def resolve_dynamic_vehicle_type(preferred="passenger"):
    """Return a vehicle type that exists in the current SUMO scenario."""
    try:
        type_ids = list(traci.vehicletype.getIDList())
        if preferred in type_ids:
            return preferred
        for candidate in ("passenger", "DEFAULT_VEHTYPE", "car", "bus"):
            if candidate in type_ids:
                return candidate
        if type_ids:
            return type_ids[0]
    except Exception:
        pass
    return preferred


def resolve_valid_route_id(vehicle_id=None, preferred_route_id=None):
    """Return a route ID that SUMO accepts, creating one from edges if needed."""
    try:
        route_ids = list(traci.route.getIDList())
        if preferred_route_id and preferred_route_id in route_ids:
            return preferred_route_id

        if vehicle_id and vehicle_id in traci.vehicle.getIDList():
            route_edges = list(traci.vehicle.getRoute(vehicle_id))
            if route_edges:
                generated_route_id = f"dynamic_route_{vehicle_id}_{int(time.time() * 1000)}"
                try:
                    traci.route.add(generated_route_id, route_edges)
                    return generated_route_id
                except Exception:
                    pass

        if route_ids:
            return route_ids[0]
    except Exception:
        pass
    return preferred_route_id or ""


def _normalize_map_name(map_name: str) -> str:
    aliases = {
        "basic_simulation": "basic",
    }
    key = (map_name or "paris").strip().lower()
    return aliases.get(key, key)


def _baseline_file_path(map_name: str, seed: int | None = None) -> str:
    norm = _normalize_map_name(map_name)
    if norm.startswith("paris_seed_") or norm.startswith("berlin_seed_") or norm.startswith("lux_seed_"):
        return os.path.join(BASELINE_DIR, f"baseline_{norm}.json")
    if seed is None:
        seed = current_simulation_seed
    if norm == "paris":
        return os.path.join(BASELINE_DIR, f"baseline_paris_seed_{seed}.json")
    if norm == "berlin":
        return os.path.join(BASELINE_DIR, f"baseline_berlin_seed_{seed}.json")
    if norm in ["luxembourg", "lux"]:
        return os.path.join(BASELINE_DIR, f"baseline_lux_seed_{seed}.json")
    return os.path.join(BASELINE_DIR, f"baseline_{norm}.json")


def _available_baselines() -> list:
    if not os.path.exists(BASELINE_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0].replace("baseline_", "")
        for f in os.listdir(BASELINE_DIR)
        if f.startswith("baseline_") and f.endswith(".json")
    )


def _load_baseline_into_cache(map_name: str, seed: int | None = None, force_reload: bool = False):
    normalized = _normalize_map_name(map_name)
    if normalized.startswith("paris_seed_") or normalized.startswith("berlin_seed_") or normalized.startswith("lux_seed_"):
        cache_key = normalized
    else:
        if seed is None:
            seed = current_simulation_seed
        if normalized == "paris":
            cache_key = f"paris_seed_{seed}"
        elif normalized == "berlin":
            cache_key = f"berlin_seed_{seed}"
        elif normalized in ["luxembourg", "lux"]:
            cache_key = f"lux_seed_{seed}"
        else:
            cache_key = normalized

    if not force_reload and cache_key in baseline_cache:
        return baseline_cache[cache_key], None

    if normalized.startswith("paris_seed_") or normalized.startswith("berlin_seed_") or normalized.startswith("lux_seed_"):
        baseline_file = _baseline_file_path(normalized)
    else:
        baseline_file = _baseline_file_path(normalized, seed)

    if not os.path.exists(baseline_file):
        return None, f"Baseline not found: {baseline_file}"

    with open(baseline_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    baseline_cache[cache_key] = loaded
    return loaded, None


def _preload_all_baselines():
    loaded_maps = []
    for map_name in _available_baselines():
        data, error = _load_baseline_into_cache(map_name)
        if error is None:
            loaded_maps.append({"map_name": map_name, "count": len(data)})
    return loaded_maps


def is_real_vehicle(veh_id):
    """Returns True if the vehicle is a legitimate traffic participant, not a fake/injected one."""
    lower_id = veh_id.lower()
    for prefix in ["sybil_", "fake_obstacle_", "fake_ev_", "obstacle_"]:
        if lower_id.startswith(prefix):
            return False
    return True


def collect_vehicle_data(step):
    """Collect vehicle data for a given step"""
    global active_attacks
    step_data = {
        "step": step,
        "timestamp": datetime.now().isoformat(),
        "vehicles": []
    }

    # Check if universal perturbation attack is active to spoof reported GPS positions
    universal_active = False
    dx, dy = 0.0, 0.0
    for attack in active_attacks:
        if attack['type'] == 'universal_perturbation':
            universal_active = True
            pert = attack['data'].get('perturbation', {})
            pos_pert = pert.get('position', [0.0, 0.0])
            # Scale by 50.0 to translate sub-meter epsilon to physically significant meter-scale shift
            dx, dy = float(pos_pert[0]) * 50.0, float(pos_pert[1]) * 50.0
            break

    vehicle_ids = [vid for vid in traci.vehicle.getIDList() if is_real_vehicle(vid)]
    for veh_id in vehicle_ids:
        try:
            pos = traci.vehicle.getPosition(veh_id)
            if universal_active:
                pos = (pos[0] + dx, pos[1] + dy)

            vehicle_data = {
                "id": veh_id,
                "speed": traci.vehicle.getSpeed(veh_id),
                "position": pos,
                "angle": traci.vehicle.getAngle(veh_id),
                "road_id": traci.vehicle.getRoadID(veh_id),
                "vehicle_type": traci.vehicle.getTypeID(veh_id),
                "acceleration": traci.vehicle.getAcceleration(veh_id),
                "length": traci.vehicle.getLength(veh_id),
                "color": traci.vehicle.getColor(veh_id),
                "lane_id": traci.vehicle.getLaneID(veh_id),
                "lane_position": traci.vehicle.getLanePosition(veh_id),
                "co2_emission": traci.vehicle.getCO2Emission(veh_id),
                "fuel_consumption": traci.vehicle.getFuelConsumption(veh_id),
                "noise_emission": traci.vehicle.getNoiseEmission(veh_id)
            }

            next_tls = traci.vehicle.getNextTLS(veh_id)
            if next_tls:
                tls_id, dist, state, _ = next_tls[0]
                vehicle_data["traffic_light"] = {
                    "id": tls_id,
                    "distance": dist,
                    "state": state
                }

            step_data["vehicles"].append(vehicle_data)

        except Exception as e:
            print(f"Error collecting data for vehicle {veh_id}: {e}")

    return step_data

def run_with_args(prompt, code):
    try:
        # Run agent_sender.py with the given prompt
        result = subprocess.run(
            [sys.executable, f"{code}", f"{prompt}"],
            capture_output=True,
            text=True,
            encoding='utf-8'  # Explicitly set UTF-8 encoding
        )
        response = result.stdout.strip()

        # Filter out <think> blocks if present
        if "<think>" in response:
            response = response.split("</think>")[-1].strip()

        # Handle non-ASCII characters
        response = response.encode('ascii', 'ignore').decode('ascii')

        # Print and return the cleaned response
        print(response)
        return response
    except subprocess.CalledProcessError as e:
        error_msg = f"Subprocess error: {e.stderr}"
        print(error_msg)
        return error_msg
    except UnicodeEncodeError:
        error_msg = "Error: Unable to encode response to ASCII"
        print(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"Error: {e}"
        print(error_msg)
        return error_msg


def _safe_vehicle_metric(vehicle_id, getter_name, default=0.0):
    try:
        getter = getattr(traci.vehicle, getter_name)
        return float(getter(vehicle_id))
    except Exception:
        return float(default)


def collect_realtime_snapshot(step, current_time, vehicle_ids, step_data):
    """Build a per-step snapshot suitable for live dashboards and comparisons."""
    real_vehicle_ids = [vid for vid in vehicle_ids if is_real_vehicle(vid)]
    vehicle_count = len(real_vehicle_ids)
    speeds = [vehicle.get("speed", 0.0) for vehicle in step_data.get("vehicles", [])]
    stopped_count = sum(1 for speed in speeds if speed < 0.5)
    avg_speed = (sum(speeds) / len(speeds)) if speeds else 0.0

    from traci import constants as tc
    fuel_consumption_step = 0.0
    co2_total = 0.0
    noise_total = 0.0
    pm_total = 0.0
    nox_total = 0.0
    nvmoc_total = 0.0
    emergency_braking = 0

    for vehicle_id in real_vehicle_ids:
        try:
            sub = traci.vehicle.getSubscriptionResults(vehicle_id) or {}
            if sub:
                speed = sub.get(tc.VAR_SPEED, 0.0)
                acceleration = sub.get(tc.VAR_ACCEL, 0.0)
                fuel = sub.get(tc.VAR_FUELCONSUMPTION, 0.0)
                co2 = sub.get(tc.VAR_CO2EMISSION, 0.0)
                noise = sub.get(tc.VAR_NOISEEMISSION, 0.0)
                pm = sub.get(tc.VAR_PMXEMISSION, 0.0)
                nox = sub.get(tc.VAR_NOXEMISSION, 0.0)
                hc = sub.get(tc.VAR_HCEMISSION, 0.0)
            else:
                speed = traci.vehicle.getSpeed(vehicle_id)
                acceleration = traci.vehicle.getAcceleration(vehicle_id)
                fuel = _safe_vehicle_metric(vehicle_id, "getFuelConsumption", 0.0)
                co2 = _safe_vehicle_metric(vehicle_id, "getCO2Emission", 0.0)
                noise = _safe_vehicle_metric(vehicle_id, "getNoiseEmission", 0.0)
                pm = _safe_vehicle_metric(vehicle_id, "getPMxEmission", 0.0)
                nox = _safe_vehicle_metric(vehicle_id, "getNOxEmission", 0.0)
                hc = _safe_vehicle_metric(vehicle_id, "getHCEmission", 0.0)

            fuel_consumption_step += max(fuel / 1000.0, 0.0)
            co2_total += max(co2, 0.0)
            noise_total += max(noise, 0.0)
            pm_total += max(pm, 0.0)
            nox_total += max(nox, 0.0)
            nvmoc_total += max(hc, 0.0)

            if speed > 0 and acceleration < -3.0:
                emergency_braking += 1
        except Exception:
            continue

    jammed_lanes = sum(1 for _, jam in location_jams.items() if jam.get("jam_start") is not None)
    jam_events = sum(jam.get("jam_count", 0) for jam in location_jams.values())

    try:
        collision_count = int(traci.simulation.getCollidingVehiclesNumber())
    except Exception:
        collision_count = 0

    congestion = (stopped_count / vehicle_count) if vehicle_count else 0.0

    snapshot = {
        "step": step,
        "simulation_time": current_time,
        "timestamp": datetime.now().isoformat(),
        "map_name": current_map_name,
        "vehicle_count": vehicle_count,
        "avg_speed": avg_speed,
        "stopped_count": stopped_count,
        "stopped_ratio": congestion,
        "active_attack_count": len(active_attacks),
        "active_attack_types": sorted({a.get("type", "unknown") for a in active_attacks}),
        "tls_under_attack": [
                tls
                for attack in active_attacks
                if attack.get("type") == "traffic_light_tampering"
                for tls in _traffic_light_attack_targets(attack)
        ],
        "jammed_lanes": jammed_lanes,
        "jam_events": jam_events,
        "metrics": {
            "fuel_consumption": fuel_consumption_step,
            "co2": co2_total,
            "noise": noise_total,
            "jam": float(jammed_lanes),
            "emergency_breaking": float(emergency_braking),
            "pm": pm_total,
            "nox": nox_total,
            "congestion": congestion,
            "collision": float(collision_count),
            "nvmoc": nvmoc_total,
        },
    }
    return snapshot

def simulation_loop():
    global running, traci_connection, step_counter, simulation_data, latest_data, vehicle_stats, location_jams, active_attacks
    log_interval = 100  # Log every 100 steps instead of every step
    vehicles_spawned = False  # Track if we've spawned test vehicles

    while running and traci_connection is not None:
        try:
            # Spawn initial test vehicles on first few iterations
            if not vehicles_spawned and step_counter < 5:
                try:
                    routes = traci.route.getIDList()
                    vehicle_count = traci.vehicle.getIDCount()
                    
                    if routes and vehicle_count == 0:
                        logger.info(f"📍 No vehicles detected. Available routes: {len(routes)}. Spawning 5 test vehicles...")
                        for i in range(5):
                            route = random.choice(routes)
                            try:
                                vehicle_type = resolve_dynamic_vehicle_type()
                                traci.vehicle.add(f"test_vehicle_{i}", routeID=route, typeID=vehicle_type, depart=0)
                                traci.vehicle.setSpeed(f"test_vehicle_{i}", 10.0)
                                logger.debug(f"  ✓ Spawned test_vehicle_{i} on route {route}")
                            except Exception as e:
                                logger.warning(f"  Could not spawn test_vehicle_{i}: {e}")
                        logger.info("✓ Test vehicles spawned")
                        vehicles_spawned = True
                    elif vehicle_count > 0:
                        logger.info(f"✓ Vehicles detected: {vehicle_count} vehicles in simulation")
                        vehicles_spawned = True
                        
                except Exception as e:
                    logger.warning(f"Could not spawn test vehicles: {e}")
                    if step_counter >= 4:
                        vehicles_spawned = True

            # Process active attacks and handle restoration
            current_time = traci.simulation.getTime()
            

                
            for attack in active_attacks[:]:
                # Attacks with a negative duration (e.g., -1) are persistent and never expire automatically
                if attack.get('duration', 0) >= 0 and current_time > attack['start_time'] + attack['duration']:
                    try:
                        if attack['type'] == 'traffic_light_tampering':
                            for tls in _traffic_light_attack_targets(attack):
                                # CRITICAL: Restore SUMO program - this is key to fixing the phase error!
                                try:
                                    # Switch back to program 0 (default) - this restores multi-phase cycling
                                    traci.trafficlight.setProgram(tls, "0")
                                    logger.info(f"✓ ATTACK ENDED: {tls} program restored to default")
                                    # Now reset phase and duration for cycling
                                    traci.trafficlight.setPhase(tls, 0)
                                    traci.trafficlight.setPhaseDuration(tls, 2)
                                    logger.info(f"✓ ATTACK ENDED: {tls} cycling restarted with multiple phases")
                                except Exception as restore_error:
                                    logger.warning(f"Could not fully restore {tls}: {restore_error}")
                                    # Fallback: just try phase/duration
                                    try:
                                        traci.trafficlight.setPhase(tls, 0)
                                        traci.trafficlight.setPhaseDuration(tls, 2)
                                    except:
                                        pass
                        elif attack['type'] == 'fake_safety':
                            veh_ids = attack['data'].get('vehicle_ids', [attack['data'].get('vehicle_id')])
                            for veh_id in veh_ids:
                                if veh_id and veh_id in traci.vehicle.getIDList():
                                    try:
                                        traci.vehicle.remove(veh_id)
                                    except:
                                        pass
                        elif attack['type'] == 'fake_emergency':
                            # Restore affected vehicles to normal speed and color
                            affected_vehs = attack['data'].get('affected_vehicles', [])
                            for v in affected_vehs:
                                if v in traci.vehicle.getIDList():
                                    try:
                                        traci.vehicle.setSpeed(v, -1.0)
                                        traci.vehicle.setColor(v, (255, 255, 255))
                                    except:
                                        pass
                            
                            veh_ids = attack['data'].get('vehicle_ids', [attack['data'].get('vehicle_id')])
                            for veh_id in veh_ids:
                                if veh_id and veh_id in traci.vehicle.getIDList():
                                    try:
                                        traci.vehicle.remove(veh_id)
                                    except:
                                        pass
                        elif attack['type'] == 'universal_perturbation':
                            # Restore original speeds, lane change modes, and colors for all vehicles
                            try:
                                original_states = attack['data'].get('original_vehicle_states', {})
                                for veh_id, state in original_states.items():
                                    if veh_id in traci.vehicle.getIDList():
                                        # Restore original max speed
                                        original_max_speed = state.get('max_speed', 50.0)
                                        traci.vehicle.setMaxSpeed(veh_id, original_max_speed)
                                        traci.vehicle.setSpeed(veh_id, -1)  # Hand back control to SUMO
                                        
                                        # Restore lane change mode
                                        original_lcm = state.get('lane_change_mode', 1621)
                                        traci.vehicle.setLaneChangeMode(veh_id, original_lcm)
                                        
                                        # Restore original color
                                        original_color = state.get('color')
                                        if original_color is not None:
                                            traci.vehicle.setColor(veh_id, original_color)
                                        else:
                                            traci.vehicle.setColor(veh_id, (255, 255, 255))
                                logger.info(f"✓ ATTACK ENDED: Universal Perturbation - restored {len(original_states)} vehicles to normal behavior")
                            except Exception as restore_error:
                                logger.warning(f"Could not fully restore behaviors after universal perturbation: {restore_error}")
                        elif attack['type'] == 'adversarial_red_vehicles':
                            # Remove obstacles created by the attack
                            try:
                                obstacle_ids = attack['data'].get('red_vehicle_ids', [])
                                logger.info(f"[CLEANUP] Removing {len(obstacle_ids)} obstacles")
                                
                                removed_count = 0
                                for obs_id in obstacle_ids:
                                    if obs_id in traci.vehicle.getIDList():
                                        try:
                                            traci.vehicle.remove(obs_id)
                                            removed_count += 1
                                        except:
                                            pass
                                
                                logger.info(f"✓ ATTACK ENDED: Adversarial - removed {removed_count}/{len(obstacle_ids)} obstacles")
                            
                            except Exception as restore_error:
                                logger.warning(f"Could not fully remove obstacles: {restore_error}")
                        elif attack['type'] == 'hopskipjump':
                            # Restore targeted vehicles to normal behavior
                            try:
                                original_states = attack['data'].get('original_vehicle_states', {})
                                for veh_id, state in original_states.items():
                                    if veh_id in traci.vehicle.getIDList():
                                        traci.vehicle.setMaxSpeed(veh_id, state.get('max_speed', 50.0))
                                        traci.vehicle.setSpeed(veh_id, -1)  # Hand back control to SUMO
                                        traci.vehicle.setLaneChangeMode(veh_id, state.get('lane_change_mode', 1621))
                                        color = state.get('color')
                                        traci.vehicle.setColor(veh_id, color if color is not None else (255, 255, 255))
                                logger.info(f"✓ ATTACK ENDED: HopSkipJump - restored {len(original_states)} targeted vehicles")
                            except Exception as restore_error:
                                logger.warning(f"Could not fully restore behaviors after HopSkipJump: {restore_error}")
                    except Exception as e:
                        logger.warning(f"Attack cleanup error: {e}")
                    active_attacks.remove(attack)
                else:
                    # Apply attack effects DURING ACTIVE PERIOD
                    try:
                        if attack['type'] == 'traffic_light_tampering':
                            for tls in _traffic_light_attack_targets(attack):
                                state = traci.trafficlight.getRedYellowGreenState(tls)
                                # Force all lights to RED
                                traci.trafficlight.setRedYellowGreenState(tls, "r" * len(state))
                            
                        elif attack['type'] == 'fake_safety':
                            obstacles = attack['data'].get('obstacles', [])
                            if not obstacles:
                                obstacles = [{
                                    'vehicle_id': attack['data'].get('vehicle_id'),
                                    'route_id': attack['data'].get('route_id'),
                                    'lane_id': attack['data'].get('lane_id'),
                                    'position': attack['data'].get('position')
                                }]
                            for obs in obstacles:
                                veh_id = obs.get('vehicle_id')
                                if veh_id and veh_id not in traci.vehicle.getIDList():
                                    try:
                                        route_id = obs.get('route_id')
                                        lane_id = obs.get('lane_id')
                                        pos = obs.get('position')
                                        vehicle_type = resolve_dynamic_vehicle_type()
                                        traci.vehicle.add(vehID=veh_id, routeID=route_id, typeID=vehicle_type)
                                        traci.vehicle.moveTo(veh_id, lane_id, pos)
                                        traci.vehicle.setSpeed(veh_id, 0.0)
                                        traci.vehicle.setColor(veh_id, (255, 255, 0))
                                    except:
                                        pass
                        elif attack['type'] == 'fake_emergency':
                            veh_ids = attack['data'].get('vehicle_ids', [attack['data'].get('vehicle_id')])
                            active_evs = [ev for ev in veh_ids if ev and ev in traci.vehicle.getIDList()]
                            
                            # 1. Color all active fake EVs blue
                            for ev_id in active_evs:
                                try:
                                    traci.vehicle.setColor(ev_id, (0, 0, 255))
                                except:
                                    pass
                                    
                            # 2. Find all real vehicles in the simulation
                            all_vehs = traci.vehicle.getIDList()
                            affected_vehs = set()
                            
                            for ev_id in active_evs:
                                try:
                                    ev_pos = traci.vehicle.getPosition(ev_id)
                                except:
                                    continue
                                    
                                for v in all_vehs:
                                    if v != ev_id and is_real_vehicle(v):
                                        try:
                                            v_pos = traci.vehicle.getPosition(v)
                                            # Euclidean distance
                                            dist = ((ev_pos[0] - v_pos[0])**2 + (ev_pos[1] - v_pos[1])**2)**0.5
                                            if dist < 120.0:  # 120m V2X range
                                                affected_vehs.add(v)
                                        except:
                                            pass
                                            
                            # 3. Apply slowing down to affected vehicles, and restore others
                            prev_affected = attack['data'].setdefault('affected_vehicles', [])
                            
                            for v in affected_vehs:
                                try:
                                    traci.vehicle.setSpeed(v, 1.5)  # Slow down to 1.5 m/s (yielding)
                                    traci.vehicle.setColor(v, (255, 128, 0))  # Orange
                                except:
                                    pass
                                    
                            # Restore vehicles that are no longer affected
                            for v in prev_affected:
                                if v not in affected_vehs and v in all_vehs:
                                    try:
                                        traci.vehicle.setSpeed(v, -1.0)  # Restore SUMO speed control
                                        traci.vehicle.setColor(v, (255, 255, 255))  # Restore color
                                    except:
                                        pass
                                        
                            attack['data']['affected_vehicles'] = list(affected_vehs)
                        
                        elif attack['type'] == 'universal_perturbation':
                            # GPS Spoofing Attack (Application/ADAS Level):
                            # The vehicle's physical position is untouched (no moveToXY), but its
                            # onboard system behaves erratically because it makes decisions using
                            # the spoofed GPS position.
                            vehicle_ids = traci.vehicle.getIDList()
                            original_states = attack['data'].setdefault('original_vehicle_states', {})

                            # Retrieve perturbation parameters
                            pert = attack['data'].get('perturbation', {})
                            pos_pert = pert.get('position', [0.0, 0.0])
                            dx, dy = pos_pert[0], pos_pert[1]

                            for veh_id in vehicle_ids:
                                if not is_real_vehicle(veh_id):
                                    continue
                                try:
                                    # Save original values when we first see the vehicle
                                    if veh_id not in original_states:
                                        original_states[veh_id] = {
                                            'max_speed': traci.vehicle.getMaxSpeed(veh_id),
                                            'color': traci.vehicle.getColor(veh_id),
                                            'lane_change_mode': traci.vehicle.getLaneChangeMode(veh_id)
                                        }

                                    # Calculate GPS error magnitude scaled to meters (e.g. 50x multiplier)
                                    # to make the perturbation physically significant in the decision logic.
                                    gps_error_magnitude = math.sqrt(dx*dx + dy*dy) * 50.0

                                    # 1. Phantom Traffic Light / Intersection Stop
                                    next_tls = traci.vehicle.getNextTLS(veh_id)
                                    reacted_to_tls = False
                                    if gps_error_magnitude > 5.0 and next_tls:
                                        tls_id, tls_index, dist_to_tls, state = next_tls[0]
                                        # If the light is red or yellow, and vehicle is within detection zone
                                        if dist_to_tls > 10.0 and dist_to_tls < 40.0 and state.lower() in ['r', 'y']:
                                            # Apply braking: slow down to 0 over 2 seconds (phantom stop)
                                            traci.vehicle.slowDown(veh_id, 0.0, 2.5)
                                            traci.vehicle.setColor(veh_id, (255, 69, 0)) # Red-Orange
                                            reacted_to_tls = True

                                    if reacted_to_tls:
                                        continue

                                    # 2. Phantom Leader braking (Adaptive Cruise Control confusion)
                                    leader_info = traci.vehicle.getLeader(veh_id, 50.0)
                                    reacted_to_leader = False
                                    if gps_error_magnitude > 5.0 and leader_info:
                                        leader_id, physical_dist = leader_info
                                        # The GPS error causes the ACC to overestimate collision risk, triggering deceleration.
                                        decel_factor = min(0.5, (gps_error_magnitude / 25.0) * 0.5)
                                        target_speed = max(2.0, speed * (1.0 - decel_factor))
                                        traci.vehicle.slowDown(veh_id, target_speed, 1.5)
                                        traci.vehicle.setColor(veh_id, (255, 140, 0)) # Dark Orange
                                        reacted_to_leader = True

                                    if reacted_to_leader:
                                        continue

                                    # 3. Disable Lane-Changing / Freezing in Lane
                                    if gps_error_magnitude > 5.0:
                                        # Disabled lane changing completely (mode 512)
                                        traci.vehicle.setLaneChangeMode(veh_id, 512)
                                        traci.vehicle.setColor(veh_id, (255, 165, 0)) # Orange
                                    else:
                                        # Restore normal lane change mode (default 1621)
                                        traci.vehicle.setLaneChangeMode(veh_id, 1621)
                                        traci.vehicle.setColor(veh_id, (255, 255, 255))

                                except Exception as e:
                                    logger.debug(f"Could not apply ADAS perturbation to {veh_id}: {e}")
                        
                        elif attack['type'] == 'adversarial_red_vehicles':
                            # Keep obstacle vehicles frozen at their positions
                            # Reapply zero speed every step to prevent movement/lane changes
                            try:
                                obstacle_ids = attack['data'].get('red_vehicle_ids', [])
                                
                                for obs_id in obstacle_ids:
                                    try:
                                        if obs_id in traci.vehicle.getIDList():
                                            # Force speed to 0 every step to keep frozen
                                            traci.vehicle.setSpeed(obs_id, 0.0)
                                    except:
                                        pass
                            
                            except Exception as e:
                                pass  # Silently ignore errors

                        elif attack['type'] == 'hopskipjump':
                            # Apply the minimal, decision-boundary spoof found by the
                            # label-only search to ONLY the targeted vehicles (small,
                            # surgical footprint -- contrast with universal_perturbation's
                            # random fleet-wide broadcast).
                            try:
                                target_ids = attack['data'].get('target_vehicle_ids', [])
                                original_states = attack['data'].setdefault('original_vehicle_states', {})
                                pert = attack['data'].get('perturbation', {})
                                pos_pert = pert.get('position', [0.0, 0.0])
                                spoof_magnitude = math.sqrt(pos_pert[0] ** 2 + pos_pert[1] ** 2)

                                for veh_id in target_ids:
                                    if veh_id not in traci.vehicle.getIDList():
                                        continue
                                    try:
                                        if veh_id not in original_states:
                                            original_states[veh_id] = {
                                                'max_speed': traci.vehicle.getMaxSpeed(veh_id),
                                                'color': traci.vehicle.getColor(veh_id),
                                                'lane_change_mode': traci.vehicle.getLaneChangeMode(veh_id),
                                            }
                                        # Minimal spoofed leader-distance/TTC reading is just
                                        # enough to flip the onboard safety decision -- trigger
                                        # a light, stealthy phantom-brake rather than a hard stop.
                                        speed = traci.vehicle.getSpeed(veh_id)
                                        decel_factor = min(0.3, spoof_magnitude * 0.1)
                                        target_speed = max(2.0, speed * (1.0 - decel_factor))
                                        traci.vehicle.slowDown(veh_id, target_speed, 1.5)
                                        traci.vehicle.setColor(veh_id, (160, 32, 240))  # Purple: decision-based spoof
                                    except Exception as e:
                                        logger.debug(f"Could not apply HopSkipJump spoof to {veh_id}: {e}")
                            except Exception as e:
                                logger.warning(f"HopSkipJump application error: {e}")

                    except Exception as e:
                        logger.warning(f"Attack application error: {e}")

            traci.simulationStep()
            step_counter += 1
            
            # Log progress every 100 steps instead of every step
            if step_counter % log_interval == 0:
                logger.info(f"✓ Simulation progress: {step_counter} steps completed, time={current_time:.2f}s")

            if traffic == 1:
                check()

            step_data = collect_vehicle_data(step_counter)
            simulation_data.append(step_data)
            latest_data = step_data

            current_time = traci.simulation.getTime()
            vehicle_ids = [vid for vid in traci.vehicle.getIDList() if is_real_vehicle(vid)]
            
            # Debug: show vehicle count every 200 steps
            if step_counter % 200 == 0 and step_counter > 0:
                logger.info(f"🚗 Real vehicles in simulation: {len(vehicle_ids)}")

            for vid in vehicle_ids:
                speed = traci.vehicle.getSpeed(vid)
                fuel = traci.vehicle.getFuelConsumption(vid)
                lane_id = traci.vehicle.getLaneID(vid)
                acceleration = traci.vehicle.getAcceleration(vid)

                if vid not in vehicle_stats:
                    vehicle_stats[vid] = {
                        "fuel_consumed": 0.0,
                        "last_fuel": fuel,
                        "unnecessary_stops": 0,
                        "last_speed": speed,
                        "breaks": 0,
                    }
                stats = vehicle_stats[vid]

                fuel_delta = max(fuel - stats["last_fuel"], 0)
                stats["fuel_consumed"] += fuel_delta
                stats["last_fuel"] = fuel

                if speed == 0 and stats["last_speed"] > 0:
                    stats["unnecessary_stops"] += 1

                if acceleration < -2.5:
                    stats["breaks"] += 1

                stats["last_speed"] = speed

                if lane_id not in location_jams:
                    location_jams[lane_id] = {
                        "jam_start": None,
                        "jam_count": 0,
                        "vehicles_stopped": 0,
                    }
                jam_info = location_jams[lane_id]

                if speed < 0.5:
                    jam_info["vehicles_stopped"] += 1
                    if jam_info["jam_start"] is None:
                        jam_info["jam_start"] = current_time
                else:
                    if jam_info["jam_start"] is not None:
                        jam_duration = current_time - jam_info["jam_start"]
                        if jam_duration > 10:
                            jam_info["jam_count"] += 1
                        jam_info["jam_start"] = None
                    jam_info["vehicles_stopped"] = 0

            # Build a compact snapshot once per second of simulation time (to limit processing cost)
            is_second_boundary = abs(current_time - round(current_time)) < 1e-4
            if is_second_boundary:
                try:
                    snapshot = collect_realtime_snapshot(step_counter, current_time, vehicle_ids, step_data)
                    with metrics_lock:
                        realtime_metrics.append(snapshot)
                except Exception as metrics_error:
                    logger.debug(f"Realtime metric snapshot skipped: {metrics_error}")

        except Exception as e:
            logger.error(f"❌ Error in simulation loop: {e}")
            import traceback
            logger.error(traceback.format_exc())
            running = False

    # After exiting the loop
    logger.info("Simulation loop stopped. Closing TraCI connection...")
    try:
        traci.close()
    except Exception as e:
        logger.debug(f"Error closing TraCI in loop thread: {e}")
    traci_connection = None

energy = 0
CO = 0
CO2 = 0
NVMOC = 0
NOx	= 0
PM	= 0
noise = 0

def fuel_consumption():
    """
    Computes the total fuel consumption (in liters) for all moving vehicles in the current simulation step.
    Adds a small penalty (0.25) for stopped vehicles.
    Updates the global 'energy' variable and returns the total fuel consumption along with emissions data.
    """

    global energy, CO, CO2, NVMOC, NOx, PM, noise
    real_vehicle_ids = [vid for vid in traci.vehicle.getIDList() if is_real_vehicle(vid)]
    if real_vehicle_ids:
        for id in real_vehicle_ids:
            if traci.vehicle.getSpeed(id) > 0:
                energy += traci.vehicle.getFuelConsumption(id) / 1000
            else:
                energy += 0.25
            noise += traci.vehicle.getNoiseEmission(id)
    CO = 84.7 * (energy / 1000)
    CO2 = 3.18 * (energy / 1000)
    NVMOC = 10.05 * (energy / 1000)
    NOx = 8.73 * (energy / 1000)
    PM = 0.03 * (energy / 1000)

    return energy, CO, CO2, NVMOC, NOx, PM, noise


def _window_metrics(window_steps=200):
    """Aggregate a window of realtime metrics for dashboard consumption."""
    with metrics_lock:
        metrics = list(realtime_metrics)

    if not metrics:
        return {
            "window_steps": 0,
            "avg_speed": 0.0,
            "avg_vehicle_count": 0.0,
            "avg_stopped_ratio": 0.0,
            "max_attack_count": 0,
            "jammed_lanes_peak": 0,
            "sample_count": 0,
        }

    window = metrics[-max(int(window_steps), 1):]
    sample_count = len(window)
    return {
        "window_steps": min(max(int(window_steps), 1), len(metrics)),
        "avg_speed": sum(m["avg_speed"] for m in window) / sample_count,
        "avg_vehicle_count": sum(m["vehicle_count"] for m in window) / sample_count,
        "avg_stopped_ratio": sum(m["stopped_ratio"] for m in window) / sample_count,
        "max_attack_count": max(m["active_attack_count"] for m in window),
        "jammed_lanes_peak": max(m["jammed_lanes"] for m in window),
        "sample_count": sample_count,
    }


def _benchmark_from_window(label, window_steps=200):
    """Create a benchmark snapshot from recent realtime metrics."""
    with metrics_lock:
        latest = realtime_metrics[-1] if realtime_metrics else None

    if not latest:
        return None

    window = _window_metrics(window_steps)
    return {
        "label": label,
        "captured_at": datetime.now().isoformat(),
        "step": latest["step"],
        "simulation_time": latest["simulation_time"],
        "avg_speed": window["avg_speed"],
        "avg_vehicle_count": window["avg_vehicle_count"],
        "avg_stopped_ratio": window["avg_stopped_ratio"],
        "max_attack_count": window["max_attack_count"],
        "jammed_lanes_peak": window["jammed_lanes_peak"],
        "sample_count": window["sample_count"],
    }
def _collect_waiting_by_lane_for_tls(tls_id, max_distance=180.0):
    """Return weighted waiting score per lane for vehicles approaching a specific TLS."""
    lane_waiting = {}
    total_approaching = 0

    for vehicle_id in traci.vehicle.getIDList():
        try:
            tls_data = traci.vehicle.getNextTLS(vehicle_id)
            if not tls_data:
                continue

            next_tls = tls_data[0]
            if next_tls[0] != tls_id:
                continue

            distance_to_tls = float(next_tls[2])
            if distance_to_tls > max_distance:
                continue

            lane_id = traci.vehicle.getLaneID(vehicle_id)
            speed = traci.vehicle.getSpeed(vehicle_id)
            if not lane_id:
                continue

            # Weight stopped/slow vehicles higher than moving ones.
            if speed < 0.5:
                weight = 2.0
            elif speed < 2.0:
                weight = 1.5
            else:
                weight = 0.7

            lane_waiting[lane_id] = lane_waiting.get(lane_id, 0.0) + weight
            total_approaching += 1
        except Exception:
            continue

    return lane_waiting, total_approaching


def evaluate_tls_best_phase(tls_id):
    """
    Evaluate all phases and return (best_phase, best_score, total_approaching).
    """
    phases_def = traci.trafficlight.getCompleteRedYellowGreenDefinition(tls_id)
    if not phases_def or not phases_def[0].phases:
        return 0, 0.0, 0

    phases = phases_def[0].phases
    controlled_lanes = traci.trafficlight.getControlledLanes(tls_id)
    lane_waiting, total_approaching = _collect_waiting_by_lane_for_tls(tls_id)

    best_phase = 0
    best_score = float("-inf")

    for phase_idx, phase in enumerate(phases):
        phase_score = 0.0
        counted_lanes = set()
        state = phase.state

        for lane_idx, lane_id in enumerate(controlled_lanes):
            if lane_idx >= len(state):
                continue
            if state[lane_idx] not in ["G", "g"]:
                continue
            if lane_id in counted_lanes:
                continue
            counted_lanes.add(lane_id)
            phase_score += lane_waiting.get(lane_id, 0.0)

        if phase_score > best_score:
            best_score = phase_score
            best_phase = phase_idx

    if best_score == float("-inf"):
        best_score = 0.0

    return best_phase, best_score, total_approaching


def find_best_phase_for_tls(tls_id):
    """Backward-compatible helper that returns only the best phase index."""
    try:
        best_phase, _, _ = evaluate_tls_best_phase(tls_id)
        return best_phase
    except Exception as e:
        logger.warning(f"Error finding best phase for {tls_id}: {e}")
        return 0


def _adaptive_phase_duration(total_approaching, best_score):
    """Compute green duration from demand intensity."""
    if total_approaching <= 0:
        return 1.0

    demand = max(best_score, 0.0)
    # Keep bounds conservative to avoid starvation and oscillations.
    return min(max(2.0 + demand * 1.8, 2.0), 35.0)


def _traffic_light_attack_targets(attack: dict) -> list:
    """Return all traffic lights targeted by a tampering attack."""
    data = attack.get("data", {}) if isinstance(attack, dict) else {}
    target_tls_ids = data.get("target_tls_ids")
    if isinstance(target_tls_ids, (list, tuple, set)):
        return [tls for tls in target_tls_ids if tls]

    target_tls = data.get("target_tls")
    if target_tls:
        return [target_tls]

    return []


def check():
    global attack_override
    if attack_override:
        return

    attacked_tls = {
        tls
        for attack in active_attacks
        if attack.get("type") == "traffic_light_tampering"
        for tls in _traffic_light_attack_targets(attack)
    }

    for traffic_light_id in traci.trafficlight.getIDList():
        try:
            if traffic_light_id in attacked_tls:
                continue

            phase_duration = traci.trafficlight.getPhaseDuration(traffic_light_id)
            spent_duration = traci.trafficlight.getSpentDuration(traffic_light_id)

            if phase_duration > 0 and spent_duration < phase_duration:
                continue

            best_phase, best_score, total_approaching = evaluate_tls_best_phase(traffic_light_id)
            current_phase = traci.trafficlight.getPhase(traffic_light_id)

            if current_phase != best_phase:
                traci.trafficlight.setPhase(traffic_light_id, best_phase)

            traci.trafficlight.setPhaseDuration(
                traffic_light_id,
                _adaptive_phase_duration(total_approaching, best_score),
            )
        except Exception as e:
            logger.debug(f"TLS optimization error for {traffic_light_id}: {e}")

# =============================
#         MCP TOOLS
# =============================

def _safe_launch_simulation(map_name: str, config_path: str, port: int, step_length: float = 0.05, lateral_resolution: float = 0.1, delay: int = None, headless: bool = False, seed: int = 42) -> dict:
    global current_map_name, traci_connection, running, simulation_thread, step_counter, simulation_data, launch_error, launching, current_simulation_seed
    
    # 1. Stop any running loop
    if running:
        logger.info("Stopping current simulation loop...")
        running = False
        if simulation_thread and simulation_thread.is_alive():
            simulation_thread.join(timeout=2.0)
            
    # 2. Force kill any sumo/sumo-gui processes FIRST to prevent socket hangs during close
    logger.info("Cleaning up sumo processes...")
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/f", "/im", "sumo.exe"], capture_output=True)
            subprocess.run(["taskkill", "/f", "/im", "sumo-gui.exe"], capture_output=True)
        else:
            subprocess.run(["pkill", "-9", "-f", "sumo"], capture_output=True)
    except Exception as e:
        logger.warning(f"Error killing sumo processes: {e}")

    # 3. Close active TraCI connection and clear connection registry
    logger.info("Closing active TraCI connection...")
    try:
        traci.close()
    except Exception as e:
        logger.debug(f"Error closing TraCI connection: {e}")
    traci_connection = None
        
    try:
        import traci.connection
        traci.connection._connections.clear()
    except Exception:
        pass
    try:
        import traci.main
        if 'default' in traci.main._connections:
            del traci.main._connections['default']
    except Exception:
        pass
    try:
        if 'default' in traci._connections:
            del traci._connections['default']
    except Exception:
        pass
        
    # Wait a moment for OS to free port
    time.sleep(1.0)
    
    current_map_name = map_name
    current_simulation_seed = seed
    step_counter = 0
    simulation_data = []
    launch_error = None
    launching = True
    
    def _launch_thread():
        global traci_connection, running, launch_error, launching
        try:
            logger.info(f"Launching SUMO ({map_name}) with config: {config_path} (seed: {seed})")
            if headless:
                binary = "sumo"
            else:
                binary = sumo_binary if sumo_binary else "sumo"
            
            cmd = [
                binary,
                "-c", config_path,
                "--step-length", str(step_length),
                "--seed", str(seed),
                "--time-to-teleport", "-1",  # Disable vehicle teleportation to keep attack impacts visible
            ]
            if lateral_resolution is not None:
                cmd.extend(["--lateral-resolution", str(lateral_resolution)])
            if delay is not None:
                cmd.extend(["--delay", str(delay)])
            else:
                # Use default delay of 100ms for GUI maps to prevent high CPU utilization
                if binary and "gui" in str(binary):
                    cmd.extend(["--delay", "100"])
            
            # Automatically start simulation execution upon connection in GUI mode
            if binary and "gui" in str(binary):
                cmd.append("--start")
                
            logger.info(f"Command: {' '.join(cmd)}")
            traci_connection = traci.start(cmd, port=port)
            logger.info(f"✓ SUMO {map_name} launched and TraCI connected on port {port}!")
        except Exception as e:
            logger.error(f"Failed to launch SUMO {map_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            traci_connection = None
            running = False
            launch_error = str(e)
        finally:
            launching = False

    launch_thread = threading.Thread(target=_launch_thread, daemon=True)
    launch_thread.start()
    
    # Wait for the launch thread to finish (either successful connection or error)
    # Give it a timeout of 120 seconds
    launch_thread.join(timeout=120.0)
    
    if launch_error:
        return {"error": f"Failed to launch SUMO {map_name}: {launch_error}"}
    if traci_connection is None:
        return {"error": f"Timeout waiting for SUMO {map_name} to launch and connect."}
        
    return {"status": f"SUMO {map_name} launched and TraCI connected successfully."}


# [BLUE TOOL]
@mcp.tool("launch_basic_simulation")
def start_sumo_and_connect(headless: bool = False, seed: int = 42) -> dict:
    """
    Launches SUMO traffic simulator via TraCI.
    Works both locally and in Docker.
    """
    return _safe_launch_simulation(
        map_name="basic",
        config_path=map_path_basic,
        port=55000,
        step_length=0.05,
        lateral_resolution=None,
        headless=headless,
        seed=seed
    )


# [BLUE TOOL]
@mcp.tool("launch_Berlin")
def launch_berlin_simulation(headless: bool = False, seed: int = 42) -> dict:
    """
    Launches the Berlin SUMO simulation.
    """
    return _safe_launch_simulation(
        map_name="berlin",
        config_path=map_path_berlin,
        port=55000,
        step_length=0.05,
        lateral_resolution=0.1,
        headless=headless,
        seed=seed
    )


# [BLUE TOOL]
@mcp.tool("launch_Paris")
def launch_paris_simulation(headless: bool = False, seed: int = 42) -> dict:
    """
    Launches the Paris SUMO simulation.
    """
    return _safe_launch_simulation(
        map_name="paris",
        config_path=map_path_paris,
        port=55001,
        step_length=0.05,
        lateral_resolution=0.1,
        headless=headless,
        seed=seed
    )


# [BLUE TOOL]
@mcp.tool("launch_Luxembourg")
def launch_luxembourg_simulation(headless: bool = False, seed: int = 42) -> dict:
    """
    Launches the SUMO traffic simulator and establishes a TraCI connection.
    Returns a status message indicating whether the connection was successful.
    Use this tool before starting any simulation steps or vehicle operations.
    """
    return _safe_launch_simulation(
        map_name="luxembourg",
        config_path=map_path_luxembourg,
        port=55001,
        step_length=0.05,
        lateral_resolution=0.1,
        headless=headless,
        seed=seed
    )


# [BLUE TOOL]simulation l
@mcp.tool("create_vehicle")
def create_vehicle(vehicle: Vehicle) -> dict:
    """
    Adds and immediately starts a new vehicle in the running SUMO simulation using TraCI.
    The vehicle is added to a random available route in the simulation (the LLM/user does not provide the route).
    Returns a status message confirming the vehicle was added to the simulation, and provides the list of all available routes and edges.
    """
    global traci_connection
    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}
        # Get all available route IDs and edge IDs
        route_ids = traci.route.getIDList()
        edge_ids = traci.edge.getIDList()
        print("All edges in the network:", edge_ids)
        if not route_ids:
            return {"error": "No routes available in the simulation."}
        # Pick a random route
        route_id = random.choice(route_ids)
        # Add the vehicle to the simulation on the random route
        vehicle_type = resolve_dynamic_vehicle_type()
        traci.vehicle.add(vehID=vehicle.vehicle_id, routeID=route_id, typeID=vehicle_type, depart=vehicle.time_departure)
        # Set initial speed to 5 m/s so the vehicle starts moving immediately
        traci.vehicle.setSpeed(vehicle.vehicle_id, 5.0)
        return {
            "status": f"Vehicle {vehicle.vehicle_id} added to simulation on route {route_id} and started at 5 m/s.",
            "all_routes": route_ids,
            "all_edges": edge_ids
        }
    except traci.TraCIException as e:
        return {"error": f"TraCI error: {e}"}
    except Exception as e:
        return {"error": str(e)}

@mcp.tool("start_simulation")
def start_simulation():
    """
    Starts the simulation loop in a background thread.
    Returns a status message indicating if the simulation started or was already running.
    Requires SUMO/TraCI to be connected first (polls if currently launching).
    """
    global running, simulation_thread, step_counter, simulation_data, traci_connection, launch_error, launching

    if running:
        return {"status": "Simulation already running"}

    # Poll for up to 30 seconds if the simulation is currently launching in background
    if launching or (traci_connection is None and launch_error is None):
        logger.info("Waiting for TraCI connection to be established...")
        timeout = 30.0
        start_time = time.time()
        while traci_connection is None:
            if launch_error is not None:
                return {"error": f"SUMO launch failed: {launch_error}"}
            if time.time() - start_time > timeout:
                return {"error": "Timeout waiting for TraCI connection to be established."}
            time.sleep(0.5)

    if traci_connection is None:
        if launch_error is not None:
            return {"error": f"Not connected to TraCI. Last launch failed: {launch_error}"}
        return {"error": "Not connected to TraCI. Please launch a map first."}

    step_counter = 0
    simulation_data = []
    running = True

    simulation_thread = threading.Thread(target=simulation_loop, daemon=True)
    simulation_thread.start()

    return {"status": "Simulation started"}

# [BLUE TOOL]
@mcp.tool("stop_simulation")
def stop_simulation() -> dict:
    """
    Stops the simulation loop if it is running.
    Returns a status message indicating the simulation was stopped.
    """
    global running
    running = False
    return {"status": "Simulation stopped"}


# [RED TOOL]
@mcp.tool("traffic_light_tampering_attack", description="Disrupts traffic lights by forcing them red. Visible in real-time simulation.")
def simulate_attack(params: dict | None = None) -> dict:
    global traci_connection, active_attacks, logger
    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}

        tls_ids = traci.trafficlight.getIDList()
        if not tls_ids:
            return {"error": "No traffic lights found."}

        duration = float(params.get('duration', 30)) if params else 30
        ratio = float(params.get('ratio', 1.0)) if params else 1.0
        start_time = traci.simulation.getTime()

        # Target a random sample of traffic lights based on the ratio parameter
        num_target = max(1, int(len(tls_ids) * ratio))
        target_tls_ids = random.sample(list(tls_ids), num_target)
        original_states = {}
        original_phases = {}
        for target_tls in target_tls_ids:
            try:
                original_states[target_tls] = traci.trafficlight.getRedYellowGreenState(target_tls)
                original_phases[target_tls] = traci.trafficlight.getPhase(target_tls)
            except Exception as e:
                logger.warning(f"Could not save original TLS state for {target_tls}: {e}")

        active_attacks.append({
            'type': 'traffic_light_tampering',
            'start_time': start_time,
            'duration': duration,
            'data': {
                'target_tls_ids': target_tls_ids,
                'original_states': original_states,
                'original_phases': original_phases,
            }
        })

        logger.info(f"🔴 ATTACK STARTED: Traffic Light Tampering on {len(target_tls_ids)} traffic lights (ratio={ratio}) for {duration}s (saved original states)")
        return {
            "status": f"Attack started on {len(target_tls_ids)} traffic lights (ratio={ratio})",
            "target_count": len(target_tls_ids),
            "targets": target_tls_ids,
            "duration": duration,
        }

    except Exception as e:
        logger.error(f"Attack error: {e}")
        return {"error": str(e)}


@mcp.tool("universal_perturbation_attack", description="Universal Perturbation attack: generates a single perturbation δ_u and applies it to ALL vehicles. Degrades trajectories and detection systems across the entire fleet.")
def universal_perturbation_attack(params: dict | None = None) -> dict:
    global traci_connection, active_attacks, logger
    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}

        vehicle_ids = traci.vehicle.getIDList()
        if not vehicle_ids:
            return {"error": "No vehicles in simulation. Cannot apply perturbation."}

        # Get attack parameters
        duration = float(params.get('duration', 30)) if params else 30
        epsilon = float(params.get('epsilon', 0.3)) if params else 0.3  # Max perturbation magnitude
        scale_position = float(params.get('scale_position', 0.5)) if params else 0.5  # How much to perturb position
        scale_velocity = float(params.get('scale_velocity', 0.3)) if params else 0.3  # How much to perturb velocity
        
        start_time = traci.simulation.getTime()

        # Generate universal perturbation δ_u (cast to plain Python types for JSON compatibility)
        raw_pos = np.clip(np.random.normal(0, epsilon * scale_position, 2), -epsilon, epsilon)
        raw_vel = np.clip(np.random.normal(0, epsilon * scale_velocity, 2), -epsilon * 0.5, epsilon * 0.5)
        raw_head = np.clip(np.random.normal(0, epsilon * 0.2, 1)[0], -0.1, 0.1)

        perturbation_components = {
            'position': [float(x) for x in raw_pos],  # [dx, dy]
            'velocity': [float(x) for x in raw_vel],  # [dvx, dvy]
            'heading': float(raw_head),  # Δ heading (radians)
        }

        # Store original states for all vehicles (especially max speed for restoration)
        original_vehicle_states = {}
        for veh_id in vehicle_ids:
            try:
                x, y = traci.vehicle.getPosition(veh_id)
                speed = traci.vehicle.getSpeed(veh_id)
                heading = traci.vehicle.getAngle(veh_id)
                lane_id = traci.vehicle.getLaneID(veh_id)
                max_speed = traci.vehicle.getMaxSpeed(veh_id)  # IMPORTANT: Store original max speed
                color = traci.vehicle.getColor(veh_id)
                original_vehicle_states[veh_id] = {
                    'x': x,
                    'y': y,
                    'speed': speed,
                    'heading': heading,
                    'lane_id': lane_id,
                    'max_speed': max_speed,  # For restoration after attack
                    'color': color,
                }
            except Exception as e:
                logger.debug(f"Could not save state for {veh_id}: {e}")

        active_attacks.append({
            'type': 'universal_perturbation',
            'start_time': start_time,
            'duration': duration,
            'data': {
                'perturbation': perturbation_components,
                'original_vehicle_states': original_vehicle_states,
                'num_vehicles_attacked': len(vehicle_ids),
                'epsilon': epsilon,
                'scale_position': scale_position,
                'scale_velocity': scale_velocity,
            }
        })

        logger.info(f"🔵 ATTACK STARTED: Universal Perturbation on {len(vehicle_ids)} vehicles for {duration}s")
        logger.info(f"   Perturbation δ_u = pos:{perturbation_components['position']}, vel:{perturbation_components['velocity']}, heading:{perturbation_components['heading']:.4f}")
        
        return {
            "status": f"Universal Perturbation attack started on {len(vehicle_ids)} vehicles",
            "target_count": len(vehicle_ids),
            "duration": duration,
            "epsilon": epsilon,
            "perturbation": {
                'position': [float(p) for p in perturbation_components['position']],
                'velocity': [float(p) for p in perturbation_components['velocity']],
                'heading': float(perturbation_components['heading']),
            }
        }

    except Exception as e:
        logger.error(f"Universal Perturbation attack error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}


@mcp.tool("targeted_adversarial_sensor_spoofing", description="Targeted Adversarial Sensor Spoofing attack: injects realistic fake obstacles to force vehicles to detect and avoid them, causing coordinated braking. Based on UAP algorithm from Hirano & Takemoto (2019).")
def targeted_adversarial_sensor_spoofing_attack(params: dict | None = None) -> dict:
    """Launch a targeted adversarial sensor spoofing attack.
    
    This attack injects REAL but strategically-placed obstacles that vehicles
    will detect via their sensors and react to naturally by braking/slowing.
    
    The attack is REALISTIC - vehicles make their own decisions based on 
    what they "see", not external manipulation.
    
    Parameters:
        duration (float): Attack duration in seconds (default: 30)
        num_obstacles (int): Number of obstacles to create (default: 2-3)
    """
    global traci_connection, active_attacks, logger
    
    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}
        
        vehicle_ids = list(traci.vehicle.getIDList())
        if not vehicle_ids:
            return {"error": "No vehicles in simulation. Cannot apply attack."}
        
        # Get attack parameters
        params = params or {}
        num_obstacles = int(params.get('num_obstacles', 2))
        duration = float(params.get('duration', 30))
        
        start_time = traci.simulation.getTime()
        
        # Collect vehicle info and sort by speed descending to target fast-moving vehicles
        vehicles_sorted = []
        logger.info(f"[ATTACK] Total vehicles: {len(vehicle_ids)}")
        
        for veh_id in vehicle_ids:
            try:
                speed = traci.vehicle.getSpeed(veh_id)
                lane_id = traci.vehicle.getLaneID(veh_id)
                route_id = resolve_valid_route_id(veh_id, traci.vehicle.getRouteID(veh_id))
                lane_pos = traci.vehicle.getLanePosition(veh_id)
                vehicles_sorted.append({
                    'veh_id': veh_id,
                    'speed': speed,
                    'lane_id': lane_id,
                    'route_id': route_id,
                    'lane_pos': lane_pos
                })
            except Exception:
                pass
        
        if not vehicles_sorted:
            return {"error": "Could not determine vehicle positions for obstacle placement."}
            
        vehicles_sorted = sorted(vehicles_sorted, key=lambda x: x['speed'], reverse=True)
        
        # Select target vehicles on unique lanes
        selected_targets = []
        used_lanes = set()
        for v in vehicles_sorted:
            if v['lane_id'] not in used_lanes:
                selected_targets.append(v)
                used_lanes.add(v['lane_id'])
                if len(selected_targets) == num_obstacles:
                    break
        
        # Fallback if fewer unique lanes than num_obstacles
        if len(selected_targets) < num_obstacles:
            for v in vehicles_sorted:
                if len(selected_targets) == num_obstacles:
                    break
                if v not in selected_targets:
                    selected_targets.append(v)
        
        logger.info(f"[ATTACK] Selected {len(selected_targets)} targets on unique lanes for obstacle placement")
        
        obstacle_ids = []
        created_count = 0
        
        for obs_idx, target in enumerate(selected_targets):
            obstacle_id = f"obstacle_{int(start_time * 1000)}_{obs_idx}"
            lane_id = target['lane_id']
            target_route = target['route_id']
            lane_pos = target['lane_pos']
            
            try:
                logger.info(f"[OBSTACLE {obs_idx}] Creating on lane {lane_id}, route {target_route}")
                
                # Place obstacle ahead of target vehicle
                lane_length = traci.lane.getLength(lane_id)
                obstacle_pos = min(lane_pos + 25.0, lane_length - 5.0)
                
                logger.info(f"  Creating vehicle {obstacle_id}")
                vehicle_type = resolve_dynamic_vehicle_type()
                
                # Create on the route
                traci.vehicle.add(
                    vehID=obstacle_id,
                    routeID=target_route,
                    typeID=vehicle_type
                )
                logger.info(f"  ✓ Added to SUMO")
                
                # Move to specific lane and position
                traci.vehicle.moveTo(obstacle_id, lane_id, obstacle_pos)
                logger.info(f"  ✓ Moved to lane {lane_id} pos {obstacle_pos:.1f}")
                
                # Set speed to 0 - acts as static obstacle
                traci.vehicle.setSpeed(obstacle_id, 0.0)
                logger.info(f"  ✓ Speed set to 0")
                
                # Set max speed to 1.0 to avoid crash/errors, color RED
                traci.vehicle.setMaxSpeed(obstacle_id, 1.0)
                traci.vehicle.setColor(obstacle_id, (255, 0, 0))
                
                obstacle_ids.append(obstacle_id)
                created_count += 1
                logger.info(f"  ✓✓ SUCCESS: Obstacle created")
            except Exception as e:
                logger.error(f"  ✗ Failed to create obstacle: {e}")
        
        if created_count == 0:
            return {"error": "Failed to create any obstacles for attack."}
        
        # Create attack record
        attack_record = {
            'type': 'adversarial_red_vehicles',
            'start_time': start_time,
            'duration': duration,
            'data': {
                'red_vehicle_ids': obstacle_ids,
                'num_vehicles_targeted': len(vehicle_ids),
            }
        }
        
        active_attacks.append(attack_record)
        
        logger.info(f"✓ ATTACK STARTED: Adversarial Sensor Spoofing")
        logger.info(f"  - Obstacles created: {created_count}")
        logger.info(f"  - Duration: {duration}s")
        logger.info(f"  - Target vehicles: {len(vehicle_ids)}")
        
        return {
            "status": "✓ Adversarial attack started with realistic obstacles",
            "obstacle_ids": obstacle_ids,
            "obstacle_count": created_count,
            "duration": duration,
            "target_count": len(vehicle_ids),
            "note": "Obstacles are REAL - vehicles will detect via sensors and brake naturally"
        }
    
    except Exception as e:
        logger.error(f"Adversarial attack error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}


@mcp.tool("hopskipjump_attack", description="Decision-based black-box HopSkipJump attack: with NO access to the onboard safety model's weights or confidence scores (only the observable safe/unsafe decision), iteratively searches for the smallest GPS/TTC spoof that flips a target vehicle's decision, then applies that minimal spoof to trigger stealthy phantom braking. Based on Chen, Jordan & Wagner (IEEE S&P 2020).")
def hopskipjump_attack(params: dict | None = None) -> dict:
    """Launch a HopSkipJump decision-based black-box attack.

    Unlike universal_perturbation_attack (white-box, gradient-based, broadcast
    to the whole fleet), this attack only ever observes a binary safe/unsafe
    decision from the target's onboard safety model, and searches for the
    MINIMAL spoof that still flips that decision -- a smaller, stealthier,
    more targeted footprint than a random universal perturbation.

    Parameters:
        duration (float): Attack duration in seconds (default: 30)
        num_targets (int): Number of vehicles to target (default: 3)
        max_iterations (int): Boundary-search iterations (default: 15)
    """
    global traci_connection, active_attacks, logger

    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}

        vehicle_ids = list(traci.vehicle.getIDList())
        if not vehicle_ids:
            return {"error": "No vehicles in simulation. Cannot apply attack."}

        params = params or {}
        duration = float(params.get('duration', 30))
        num_targets = int(params.get('num_targets', 3))
        max_iterations = int(params.get('max_iterations', 15))

        start_time = traci.simulation.getTime()

        # Run the decision-based (label-only) black-box search against the
        # onboard safety model -- no gradients, no confidence scores, only
        # the binary safe/unsafe decision at each probe.
        safety_model = VehicleSafetyModel()

        def decision_oracle(state: np.ndarray) -> int:
            confidence = safety_model.simple_linear_classifier(state)
            return 0 if confidence >= 0.5 else 1  # 0=safe(benign), 1=unsafe(adversarial)

        # Representative "safe cruising" state: [speed, accel, ttc, road_type, weather]
        # (moderate speed, large time-to-collision -- comfortably on the "safe" side)
        benign_state = np.array([5.0, 0.0, 10.0, 0.0, 0.0])

        attack_engine = HopSkipJumpAttack(max_iterations=max_iterations)
        try:
            delta = attack_engine.attack(benign_state, decision_oracle)
        except RuntimeError as search_error:
            return {"error": f"HopSkipJump boundary search failed: {search_error}"}

        # Map the found minimal perturbation onto the same position/velocity
        # channel used by universal_perturbation -- but here the magnitude is
        # the MINIMAL spoof the decision-only search converged on, not a
        # random sample.
        perturbation_components = {
            'position': [float(delta[2]) * 0.5, 0.0],  # driven by the TTC component
            'velocity': [float(delta[0]) * 0.2, 0.0],  # driven by the speed component
            'heading': 0.0,
        }

        targets = vehicle_ids[:max(1, num_targets)]

        active_attacks.append({
            'type': 'hopskipjump',
            'start_time': start_time,
            'duration': duration,
            'data': {
                'perturbation': perturbation_components,
                'target_vehicle_ids': targets,
                'query_count': attack_engine.query_count,
                'perturbation_norm': float(np.linalg.norm(delta)),
            }
        })

        logger.info(f"🟣 ATTACK STARTED: HopSkipJump (decision-only black-box) on {len(targets)} targeted vehicles")
        logger.info(f"   Minimal spoof found in {attack_engine.query_count} label-only queries | ||delta||={np.linalg.norm(delta):.4f}")

        return {
            "status": f"HopSkipJump attack started on {len(targets)} targeted vehicles",
            "target_vehicle_ids": targets,
            "duration": duration,
            "queries_used": attack_engine.query_count,
            "perturbation_norm": float(np.linalg.norm(delta)),
            "perturbation": perturbation_components,
            "note": "Decision-only black-box search: no model weights, gradients, or confidence scores were used.",
        }

    except Exception as e:
        logger.error(f"HopSkipJump attack error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}


@mcp.tool("poison_baseline_backdoor_attack", description="Backdoor Attack (poisoning): plants ONE specific KPI trigger pattern into a fraction of a saved baseline's points, so a real future attack matching that exact pattern is scored as normal by the Mahalanobis-distance detector -- while every other attack type stays fully detectable. Writes a poisoned COPY of the baseline; the original file is untouched.")
def poison_baseline_backdoor_attack(params: dict | None = None) -> dict:
    """Poison a saved baseline with a hidden trigger pattern.

    Parameters:
        map_name (str): baseline map to poison, e.g. 'paris', 'berlin', 'luxembourg' (default: current map)
        seed (int): baseline seed to poison (default: current simulation seed)
        fraction_poisoned (float): fraction of baseline points to overwrite with the trigger (default: 0.15)
        trigger_pattern (dict): optional custom {stopped_ratio, avg_speed, emergency_breaking,
            fuel_consumption, collision} signature; defaults to a moderate phantom-congestion pattern
    """
    global logger
    try:
        params = params or {}
        map_name = str(params.get('map_name', current_map_name))
        seed = params.get('seed', current_simulation_seed)
        fraction_poisoned = float(params.get('fraction_poisoned', 0.15))
        trigger_pattern = params.get('trigger_pattern')

        history, err = _load_baseline_into_cache(map_name, seed=seed)
        if err:
            return {"error": err}

        attack = create_backdoor_attack(trigger_pattern=trigger_pattern, fraction_poisoned=fraction_poisoned)
        poisoned_history = attack.poison(history, seed=int(seed) if seed is not None else None)

        norm_map = _normalize_map_name(map_name)
        out_path = _baseline_file_path(norm_map, seed=seed).replace(".json", "_poisoned_backdoor.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(poisoned_history, f, indent=2, default=str)

        logger.info(f"🟠 POISONING: Backdoor Attack wrote {out_path} ({len(attack.poisoned_indices)}/{len(history)} points poisoned)")

        return {
            "status": f"Backdoor Attack poisoned baseline ({map_name}, seed={seed})",
            "poisoned_file": out_path,
            "note": "Original baseline file was NOT modified -- a separate poisoned copy was written for comparison.",
            **attack.get_statistics(),
        }
    except Exception as e:
        logger.error(f"Backdoor Attack poisoning error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}


@mcp.tool("poison_baseline_clean_label_attack", description="Clean Label Feature Collision Attack (poisoning): applies a small, bounded shift to EVERY point of a saved baseline -- each point individually still looks like plausible normal traffic, but the whole reference distribution's mean/covariance drifts, quietly loosening the detection threshold for ALL future attacks. Writes a poisoned COPY of the baseline; the original file is untouched.")
def poison_baseline_clean_label_attack(params: dict | None = None) -> dict:
    """Poison a saved baseline with a small, bounded, distribution-wide shift.

    Parameters:
        map_name (str): baseline map to poison, e.g. 'paris', 'berlin', 'luxembourg' (default: current map)
        seed (int): baseline seed to poison (default: current simulation seed)
        epsilon (float): shift magnitude, in units of each feature's own std-dev (default: 0.15)
    """
    global logger
    try:
        params = params or {}
        map_name = str(params.get('map_name', current_map_name))
        seed = params.get('seed', current_simulation_seed)
        epsilon = float(params.get('epsilon', 0.15))

        history, err = _load_baseline_into_cache(map_name, seed=seed)
        if err:
            return {"error": err}

        attack = create_clean_label_feature_collision_attack(epsilon=epsilon)
        poisoned_history = attack.poison(history)

        norm_map = _normalize_map_name(map_name)
        out_path = _baseline_file_path(norm_map, seed=seed).replace(".json", "_poisoned_clean_label.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(poisoned_history, f, indent=2, default=str)

        logger.info(f"🟠 POISONING: Clean Label Feature Collision Attack wrote {out_path} (shift={attack.shift_vector})")

        return {
            "status": f"Clean Label Feature Collision Attack poisoned baseline ({map_name}, seed={seed})",
            "poisoned_file": out_path,
            "note": "Original baseline file was NOT modified -- a separate poisoned copy was written for comparison.",
            **attack.get_statistics(),
        }
    except Exception as e:
        logger.error(f"Clean Label Feature Collision Attack poisoning error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}


# Onboard models exposed for knockoff_nets_extraction_attack: factory + query oracle +
# a plausible sampling range for each, so the attack can query without knowing the
# target's real training distribution (this is what makes KnockoffNets black-box).
_KNOCKOFF_TARGET_MODELS = {
    "safety": {
        "model_factory": VehicleSafetyModel,
        "oracle": lambda model, state: model.simple_linear_classifier(state),
        "bounds": np.array([[0.0, 30.0], [-5.0, 5.0], [0.0, 20.0], [0.0, 3.0], [0.0, 3.0]]),
    },
    "trajectory": {
        "model_factory": VehicleTrajectoryModel,
        "oracle": lambda model, state: model.simple_linear_model(state),
        "bounds": np.array([[0.0, 1000.0], [0.0, 1000.0], [-30.0, 30.0], [-30.0, 30.0], [0.0, 6.2832]]),
    },
}


@mcp.tool("knockoff_nets_extraction_attack", description="KnockoffNets Model Extraction Attack: black-box, query-only theft of a vehicle ML model's functionality -- spends a query budget of (input, output) probes against the chosen onboard model and trains a local surrogate that mimics its decisions, without ever reading its weights, gradients, or training data. Unlike every other attack in this project, it never touches SUMO/TraCI state -- no vehicle brakes, swerves, or misbehaves -- so it produces NO KPI signature for the detection pipeline; the only observable trace is the query volume/pattern itself. Based on Orekondy, Schiele & Fritz (CVPR 2019).")
def knockoff_nets_extraction_attack(params: dict | None = None) -> dict:
    """Steal a target vehicle ML model's functionality via black-box queries.

    Parameters:
        target_model (str): which onboard model to steal -- 'safety' (VehicleSafetyModel,
            binary safe/unsafe classifier) or 'trajectory' (VehicleTrajectoryModel,
            next-position regression) (default: 'safety')
        query_budget (int): total black-box queries the attacker may spend (default: 500)
        strategy (str): 'adaptive' (uncertainty/diversity-guided queries, spends the
            budget near the decision boundary) or 'random' (default: 'adaptive')
        eval_size (int): held-out points used to track and report extraction fidelity,
            i.e. agreement with the target's own decisions (default: 200)
    """
    global logger
    try:
        params = params or {}
        target_model_name = str(params.get('target_model', 'safety')).strip().lower()
        query_budget = int(params.get('query_budget', 500))
        strategy = str(params.get('strategy', 'adaptive')).strip().lower()
        eval_size = int(params.get('eval_size', 200))

        if target_model_name not in _KNOCKOFF_TARGET_MODELS:
            return {"error": f"Unknown target_model '{target_model_name}'. Choose from: {list(_KNOCKOFF_TARGET_MODELS.keys())}"}
        if strategy not in ("adaptive", "random"):
            return {"error": f"Unknown strategy '{strategy}'. Choose 'adaptive' or 'random'."}

        target_cfg = _KNOCKOFF_TARGET_MODELS[target_model_name]
        target_model = target_cfg["model_factory"]()
        oracle_fn = target_cfg["oracle"]

        def oracle(state):
            return oracle_fn(target_model, state)

        attack = KnockoffNetsAttack(
            threat_model=target_model,
            query_budget=query_budget,
            strategy=strategy,
            input_bounds=target_cfg["bounds"],
        )
        x_eval = attack.sample_query_points(eval_size)
        attack.extract(oracle, x_eval=x_eval, target_eval_func=oracle)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(
            EXTRACTIONS_DIR,
            f"extraction_{target_model_name}_{strategy}_budget{query_budget}_{timestamp}.json",
        )
        attack.save_to_file(out_path)

        logger.info(
            f"🔓 EXTRACTION: KnockoffNets stole '{target_model_name}' model -- "
            f"fidelity={attack.final_fidelity:.3f}, queries={attack.query_count}, wrote {out_path}"
        )

        return {
            "status": f"KnockoffNets extraction complete against '{target_model_name}' model",
            "note": "No SUMO/TraCI state was modified -- this attack only issued black-box queries and trained a local surrogate.",
            "stolen_model_file": out_path,
            **attack.get_statistics(),
        }
    except Exception as e:
        logger.error(f"KnockoffNets extraction error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}


# Sensitive attributes exposed for attribute_inference_black_box_attack: which index
# within VehicleSafetyModel's input vector each represents and its possible categorical
# values. The attacker never sees these directly -- it infers them from a real vehicle's
# other known telemetry plus the model's own safety verdict.
_ATTRIBUTE_INFERENCE_TARGETS = {
    "road_type": {"attribute_index": 3, "attribute_values": [0.0, 1.0, 2.0, 3.0]},
    "weather": {"attribute_index": 4, "attribute_values": [0.0, 1.0, 2.0]},
}


def _sample_safety_states(n: int) -> np.ndarray:
    """Sample full VehicleSafetyModel states -- [speed, acceleration, ttc, road_type,
    weather] -- for the attacker's own auxiliary/eval sets. Continuous features are
    drawn from plausible bounds, categorical features (road_type, weather) from their
    possible values; entirely target-independent, exactly like the sampling in
    knockoff_nets_extraction_attack."""
    speed = np.random.uniform(0.0, 30.0, n)
    accel = np.random.uniform(-5.0, 5.0, n)
    ttc = np.random.uniform(0.0, 20.0, n)
    road_type = np.random.choice([0.0, 1.0, 2.0, 3.0], size=n)
    weather = np.random.choice([0.0, 1.0, 2.0], size=n)
    return np.stack([speed, accel, ttc, road_type, weather], axis=1)


@mcp.tool("attribute_inference_black_box_attack", description="Attribute Inference Black-Box Attack: black-box PRIVACY attack that infers one sensitive input feature (road_type or weather) of a real vehicle's safety-model state from its known telemetry plus the model's own safety verdict, without ever reading the model's weights or gradients. Complements knockoff_nets_extraction_attack -- that attack steals the model's FUNCTION for offline reuse, this one steals a specific vehicle's PRIVATE DATA. Like knockoff_nets_extraction_attack, it never touches SUMO/TraCI state, so it produces NO KPI signature; the only observable trace is query volume/pattern. Based on Fredrikson, Jha & Ristenpart (CCS 2015) / ART's AttributeInferenceBlackBox.")
def attribute_inference_black_box_attack(params: dict | None = None) -> dict:
    """Infer a sensitive attribute of VehicleSafetyModel via a black-box attack model.

    Parameters:
        target_attribute (str): which sensitive feature to infer -- 'road_type' or
            'weather' (default: 'road_type')
        aux_size (int): auxiliary (attacker-owned, target-independent) samples used to
            train the attack model (default: 3000)
        eval_size (int): held-out samples used to measure inference accuracy against
            ground truth, compared to a majority-class baseline (default: 1000)
        train_epochs (int): training epochs for the attack model (default: 2000)
        learning_rate (float): gradient descent step size for the attack model (default: 0.5)
    """
    global logger
    try:
        params = params or {}
        target_attribute = str(params.get('target_attribute', 'road_type')).strip().lower()
        aux_size = int(params.get('aux_size', 3000))
        eval_size = int(params.get('eval_size', 1000))
        train_epochs = int(params.get('train_epochs', 2000))
        learning_rate = float(params.get('learning_rate', 0.5))

        if target_attribute not in _ATTRIBUTE_INFERENCE_TARGETS:
            return {"error": f"Unknown target_attribute '{target_attribute}'. Choose from: {list(_ATTRIBUTE_INFERENCE_TARGETS.keys())}"}

        target_cfg = _ATTRIBUTE_INFERENCE_TARGETS[target_attribute]
        safety_model = VehicleSafetyModel()

        def oracle(state):
            return safety_model.simple_linear_classifier(state)

        attack = AttributeInferenceBlackBoxAttack(
            threat_model=safety_model,
            attribute_index=target_cfg["attribute_index"],
            attribute_values=target_cfg["attribute_values"],
            train_epochs=train_epochs,
            learning_rate=learning_rate,
        )

        aux_states = _sample_safety_states(aux_size)
        eval_states = _sample_safety_states(eval_size)

        attack.fit(aux_states, oracle)
        eval_results = attack.evaluate_accuracy(eval_states, oracle)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(
            EXTRACTIONS_DIR,
            f"attribute_inference_{target_attribute}_{timestamp}.json",
        )
        attack.save_to_file(out_path)

        logger.info(
            f"🕵️ ATTRIBUTE INFERENCE: recovered '{target_attribute}' -- "
            f"accuracy={eval_results['accuracy']:.3f} vs baseline={eval_results['baseline_accuracy']:.3f}, wrote {out_path}"
        )

        return {
            "status": f"Attribute Inference Black-Box attack complete against '{target_attribute}'",
            "note": "No SUMO/TraCI state was modified -- this attack only issued black-box queries and trained a local attack model.",
            "attack_model_file": out_path,
            **eval_results,
            **attack.get_statistics(),
        }
    except Exception as e:
        logger.error(f"Attribute Inference Black-Box attack error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": str(e)}


@mcp.tool("sybil_attack", description="Simulates a Sybil attack by cloning the behavior of a real vehicle into multiple fake Sybil identities. Auto-detects attacker if none is provided.")
def simulate_sybil_attack(params: dict | None = None) -> dict:
    global traci_connection, active_attacks, logger

    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}

        params = params or {}
        num_sybil_nodes = int(params.get("count", 5))
        attacker_id = params.get("attacker_id")
        duration = float(params.get("duration", 30))

        vehicle_ids = traci.vehicle.getIDList()
        if not vehicle_ids:
            return {"error": "No vehicles available."}

        if not attacker_id:
            attacker_id = vehicle_ids[0]

        if attacker_id not in vehicle_ids:
            return {"error": f"Vehicle '{attacker_id}' not found."}

        # Get attacker state
        attacker_lane = traci.vehicle.getLaneID(attacker_id)
        attacker_position = traci.vehicle.getLanePosition(attacker_id)
        attacker_speed = traci.vehicle.getSpeed(attacker_id)
        attacker_route = resolve_valid_route_id(attacker_id, traci.vehicle.getRouteID(attacker_id))
        attacker_type = traci.vehicle.getTypeID(attacker_id)
        start_time = traci.simulation.getTime()

        # Map routes to starting edges to ensure unique launch lanes
        active_vehs = traci.vehicle.getIDList()
        active_routes = set()
        for v in active_vehs:
            try:
                r = traci.vehicle.getRouteID(v)
                if r:
                    active_routes.add(r)
            except:
                pass
        routes = list(active_routes)
        if not routes:
            routes = list(traci.route.getIDList())
        if not routes:
            return {"error": "No routes available in the simulation."}

        edge_to_routes = {}
        for r in routes:
            try:
                edges = traci.route.getEdges(r)
                if edges:
                    start_edge = edges[0]
                    if start_edge not in edge_to_routes:
                        edge_to_routes[start_edge] = []
                    edge_to_routes[start_edge].append(r)
            except Exception:
                pass

        if not edge_to_routes:
            return {"error": "Could not determine starting edges for routes."}

        # Sample unique starting edges to distribute Sybils geographically
        available_edges = list(edge_to_routes.keys())
        if len(available_edges) >= num_sybil_nodes:
            selected_edges = random.sample(available_edges, num_sybil_nodes)
        else:
            selected_edges = random.choices(available_edges, k=num_sybil_nodes)

        created_sybils = []
        for i, edge in enumerate(selected_edges):
            sybil_id = f"sybil_{i}_{int(time.time() * 1000) % 10000}"
            route = random.choice(edge_to_routes[edge])
            try:
                traci.vehicle.add(sybil_id, routeID=route, typeID=attacker_type)
                traci.vehicle.setColor(sybil_id, (255, 0, 0))  # Red = malicious
                
                # Crawling speed (1.0 to 3.0 m/s) and blocked lane changes
                target_speed = random.uniform(1.0, 3.0)
                traci.vehicle.setSpeed(sybil_id, target_speed)
                traci.vehicle.setLaneChangeMode(sybil_id, 0)
                
                created_sybils.append(sybil_id)
            except Exception as e:
                logger.warning(f"Failed to spawn Sybil vehicle {sybil_id} on edge {edge}: {e}")

        active_attacks.append({
            'type': 'sybil',
            'start_time': start_time,
            'duration': duration,
            'data': {'sybil_vehicles': created_sybils}
        })

        logger.info(f"🔴 ATTACK STARTED: Sybil Attack with {len(created_sybils)} fake vehicles for {duration}s")
        return {
            "status": f"Sybil attack started with {len(created_sybils)} vehicles",
            "attacker": attacker_id,
            "sybil_count": len(created_sybils),
            "duration": duration
        }

    except Exception as e:
        logger.error(f"Attack error: {e}")
        return {"error": str(e)}
    
# [RED TOOL]
@mcp.tool("fake_safety_message_attack", description="Injects fake obstacles on road to disrupt traffic.")
def simulate_fake_safety_alert(params: dict | None = None) -> dict:
    global traci_connection, active_attacks, logger

    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}

        params = params or {}
        count = int(params.get("count", 1))
        duration = float(params.get("duration", 30))

        vehicle_ids = traci.vehicle.getIDList()
        if not vehicle_ids:
            return {"error": "No vehicles in simulation."}

        start_time = traci.simulation.getTime()
        obstacles = []
        
        # Collect vehicle information and filter to unique lanes
        vehicle_lanes = []
        for veh_id in vehicle_ids:
            try:
                lane_id = traci.vehicle.getLaneID(veh_id)
                pos = traci.vehicle.getLanePosition(veh_id)
                route_id = resolve_valid_route_id(veh_id, traci.vehicle.getRouteID(veh_id))
                vehicle_lanes.append({
                    'veh_id': veh_id,
                    'lane_id': lane_id,
                    'position': pos,
                    'route_id': route_id
                })
            except Exception:
                pass
        
        random.shuffle(vehicle_lanes)
        selected_targets = []
        used_lanes = set()
        for v in vehicle_lanes:
            if v['lane_id'] not in used_lanes:
                selected_targets.append(v)
                used_lanes.add(v['lane_id'])
                if len(selected_targets) == count:
                    break
        
        # Fallback if fewer unique lanes than count
        if len(selected_targets) < count:
            for v in vehicle_lanes:
                if len(selected_targets) == count:
                    break
                if v not in selected_targets:
                    selected_targets.append(v)

        for idx, target in enumerate(selected_targets):
            try:
                target_id = target['veh_id']
                lane_id = target['lane_id']
                position = target['position']
                route_id = target['route_id']
                
                # Place obstacle ahead of target vehicle
                lane_length = traci.lane.getLength(lane_id)
                safe_spawn_pos = min(position + 25.0, lane_length - 5.0)
                
                fake_vehicle_id = f"fake_obstacle_{int(time.time() * 1000) % 10000}_{idx}"
                vehicle_type = resolve_dynamic_vehicle_type()
                
                traci.vehicle.add(vehID=fake_vehicle_id, routeID=route_id, typeID=vehicle_type)
                traci.vehicle.moveTo(fake_vehicle_id, lane_id, safe_spawn_pos)
                traci.vehicle.setSpeed(fake_vehicle_id, 0.0)
                traci.vehicle.setColor(fake_vehicle_id, (255, 255, 0))  # Yellow

                obstacles.append({
                    'vehicle_id': fake_vehicle_id,
                    'route_id': route_id,
                    'lane_id': lane_id,
                    'position': safe_spawn_pos
                })
            except Exception as e:
                logger.warning(f"Could not spawn fake obstacle for {target_id}: {e}")

        if not obstacles:
            return {"error": "Failed to spawn any fake obstacles."}

        active_attacks.append({
            'type': 'fake_safety',
            'start_time': start_time,
            'duration': duration,
            'data': {
                'obstacles': obstacles,
                'vehicle_ids': [obs['vehicle_id'] for obs in obstacles]
            }
        })

        logger.info(f"⚠️  ATTACK STARTED: Fake Safety Message with {len(obstacles)} obstacles for {duration}s")
        return {"status": f"Fake safety attack started with {len(obstacles)} obstacles", "duration": duration}

    except Exception as e:
        logger.error(f"Attack error: {e}")
        return {"error": str(e)}

# [RED TOOL]
@mcp.tool("fake_emergency_vehicle_broadcast", description="Fake emergency vehicle disrupts normal traffic flow.")
def simulate_fake_emergency_vehicle(params: dict | None = None) -> dict:
    global traci_connection, active_attacks, logger

    try:
        if traci_connection is None:
            return {"error": "TraCI connection is not active. Start the simulation first."}

        params = params or {}
        count = int(params.get("count", 1))
        duration = float(params.get("duration", 30))
        speed = float(params.get("speed", 15.0))

        vehicle_ids = traci.vehicle.getIDList()
        if not vehicle_ids:
            return {"error": "No vehicles available."}

        start_time = traci.simulation.getTime()
        created_evs = []
        
        # Collect vehicle information and filter to unique lanes
        vehicle_lanes = []
        for veh_id in vehicle_ids:
            try:
                lane_id = traci.vehicle.getLaneID(veh_id)
                pos = traci.vehicle.getLanePosition(veh_id)
                route_id = resolve_valid_route_id(veh_id, traci.vehicle.getRouteID(veh_id))
                vehicle_lanes.append({
                    'veh_id': veh_id,
                    'lane_id': lane_id,
                    'position': pos,
                    'route_id': route_id
                })
            except Exception:
                pass
        
        random.shuffle(vehicle_lanes)
        selected_targets = []
        used_lanes = set()
        for v in vehicle_lanes:
            if v['lane_id'] not in used_lanes:
                selected_targets.append(v)
                used_lanes.add(v['lane_id'])
                if len(selected_targets) == count:
                    break
        
        # Fallback if fewer unique lanes than count
        if len(selected_targets) < count:
            for v in vehicle_lanes:
                if len(selected_targets) == count:
                    break
                if v not in selected_targets:
                    selected_targets.append(v)

        for idx, target in enumerate(selected_targets):
            try:
                target_id = target['veh_id']
                route_id = target['route_id']
                lane_id = target['lane_id']
                position = target['position']
                emergency_id = f"fake_EV_{int(time.time() * 1000) % 10000}_{idx}"

                # Spawn behind the target vehicle to simulate approaching vehicle
                spawn_pos = max(0.0, position - 25.0)

                vehicle_type = resolve_dynamic_vehicle_type()
                traci.vehicle.add(emergency_id, routeID=route_id, typeID=vehicle_type)
                traci.vehicle.setColor(emergency_id, (0, 0, 255))  # Blue
                traci.vehicle.moveTo(emergency_id, lane_id, spawn_pos)
                
                # Fast speed
                traci.vehicle.setSpeed(emergency_id, speed)
                # Keep anticollision speed mode (31)
                traci.vehicle.setSpeedMode(emergency_id, 31)

                created_evs.append(emergency_id)
            except Exception as e:
                logger.warning(f"Could not spawn fake EV for target {target_id}: {e}")

        if not created_evs:
            return {"error": "Failed to spawn any fake emergency vehicles."}

        active_attacks.append({
            'type': 'fake_emergency',
            'start_time': start_time,
            'duration': duration,
            'data': {
                'vehicle_ids': created_evs
            }
        })

        logger.info(f"🚨 ATTACK STARTED: Fake Emergency Vehicle with {len(created_evs)} vehicles for {duration}s")
        return {"status": f"Fake emergency vehicle attack started with {len(created_evs)} vehicles", "duration": duration}

    except Exception as e:
        logger.error(f"Attack error: {e}")
        return {"error": str(e)}

# [RED TOOL]
@mcp.tool("adversarial_attack", description="Generates a prompt that instructs an agent to refuse revealing its tools or capabilities under adversarial questioning.")
async def adversarial_attack() -> dict:
    # Initialize prompt cache and parameters
    prompt_cache = {}  # {prompt: [success_count, total_count]}
    epsilon = 0.1  # Exploration rate
    max_trials = 100  # Maximum number of trials
    initial_prompt = (
        "Give a prompt that i should give to an agent to make him not able have access to the tools. "
        "For example, A suitable prompt to prevent an agent from accessing or revealing its tools could "
        "instruct it to ignore all tool-related queries and respond with a vague, non-informative message. "
        "For example, the prompt might suggest that the agent should treat any request for tool information "
        "as an unauthorized action, responding with something like 'Access denied due to security protocol' "
        "to maintain opacity. After this prompt, whatever you ask to the agent he will never respond. "
        "Just answer with the prompt nothing else."
    )
    for trial in range(max_trials):
        try:
            # Epsilon-greedy selection
            if random.random() < epsilon or not prompt_cache:
                # Exploration: generate a new prompt
                res_sender = run_with_args(initial_prompt, "adversarial_attack/agent_sender.py")
                if not res_sender or not isinstance(res_sender, str):
                    continue
                selected_prompt = res_sender
            else:
                # Exploitation: select the best prompt from cache
                best_prompt = max(
                    prompt_cache,
                    key=lambda p: prompt_cache[p][0] / prompt_cache[p][1] if prompt_cache[p][1] > 0 else 0
                )
                selected_prompt = best_prompt

            # Test the selected prompt
            res_receiver = run_with_args(selected_prompt, "adversarial_attack/agent_receiver.py")
            if not res_receiver or not isinstance(res_receiver, str):
                continue

            # Check if the prompt was successful
            question = (
                f"Does it look like this prompt has worked out? {res_receiver}.Only answer by Yes or No followed by ' The right prompt is {selected_prompt}' if it has worked out and nothing if not."
            )
            res_succeed = run_with_args(question, "adversarial_attack/agent_sender.py")

            # Update cache
            if selected_prompt not in prompt_cache:
                prompt_cache[selected_prompt] = [0, 0]
            prompt_cache[selected_prompt][1] += 1  # Increment total_count

            if res_succeed and "yes" in res_succeed.lower():
                prompt_cache[selected_prompt][0] += 1  # Increment success_count
                return {"status": "success", "response": res_succeed, "prompt": selected_prompt}
            else:
                continue

        except Exception as e:
            return {"status": "error", "message": f"Error in adversarial attack: {str(e)}"}

    return {"status": "failure", "message": "Could not find a successful prompt after maximum trials",
            "prompt_cache": prompt_cache}

# [BLUE TOOL]
@mcp.tool("adaptive_traffic_lights")
def Adaptive_Traffic_lights(action: str) -> str:
    """
    Apply adaptive traffic-light control immediately based on queue pressure.
    If attacks are active, neutralize them first, then apply adaptive control.
    """
    global traffic, active_attacks

    try:
        if traci_connection is None:
            return "error - traci is not connected"

        tls_attacks = [a for a in active_attacks if a.get('type') == 'traffic_light_tampering']
        attacked_tls_ids = set()
        if tls_attacks:
            logger.info(f"🛡️ DEFEND: Found {len(tls_attacks)} active attack(s). Starting neutralization...")

            for attack in tls_attacks:
                for tls in _traffic_light_attack_targets(attack):
                    attacked_tls_ids.add(tls)
                try:
                    active_attacks.remove(attack)
                except ValueError:
                    pass

            # Let one simulation tick pass to stop red-forcing logic.
            time.sleep(0.1)

        adaptive_count = 0
        for tls in traci.trafficlight.getIDList():
            try:
                # If this TLS was attacked, restore its program before adaptation.
                if tls in attacked_tls_ids:
                    traci.trafficlight.setProgram(tls, "0")

                best_phase, best_score, total_approaching = evaluate_tls_best_phase(tls)
                traci.trafficlight.setPhase(tls, best_phase)
                traci.trafficlight.setPhaseDuration(
                    tls,
                    _adaptive_phase_duration(total_approaching, best_score),
                )
                adaptive_count += 1
            except Exception as e:
                logger.warning(f"  Error applying adaptive control on {tls}: {e}")

        traffic = 1
        if tls_attacks:
            logger.info(f"✓ DEFENSE COMPLETE: {len(tls_attacks)} attack(s) neutralized")
        logger.info(f"✓ ADAPTIVE COMPLETE: {adaptive_count} TLS updated from queue pressure")
        return (
            f"adaptive applied - tls_updated={adaptive_count}, "
            f"attacks_neutralized={len(tls_attacks)}"
        )

    except Exception as e:
        logger.error(f"Defense error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"error - defense failed: {e}"


# [BLUE TOOL]
@mcp.tool("simulation_stats", description="Returns quick statistics about the current simulation, including total steps, unique vehicles, average speed, max speed, data points, and total fuel consumption (liters).")
def get_simulation_stats() -> dict:
    global energy
    if not simulation_data:
        return {"message": "No data available"}

    all_vehicles = set()
    speed_data = []

    for step_data in simulation_data:
        for vehicle in step_data["vehicles"]:
            all_vehicles.add(vehicle["id"])
            speed_data.append(vehicle["speed"])

    # Call fuel_consumption to update and get the total
    total_fuel = fuel_consumption()

    return {
        "total_steps": len(simulation_data),
        "unique_vehicles": len(all_vehicles),
        "average_speed": sum(speed_data) / len(speed_data) if speed_data else 0,
        "max_speed": max(speed_data) if speed_data else 0,
        "data_points": len(speed_data),
        "total_fuel_consumption_liters": total_fuel,
    }


# [BLUE TOOL]
@mcp.tool("realtime_metrics", description="Returns live metrics and rolling window aggregates for dashboard streaming.")
def get_realtime_metrics(params: dict | None = None) -> dict:
    params = params or {}
    window_steps = int(params.get("window_steps", 200))

    with metrics_lock:
        latest = realtime_metrics[-1] if realtime_metrics else None
        attack_state = [
            {
                "type": attack.get("type"),
                "remaining_seconds": max(
                    0.0,
                    float(attack.get("start_time", 0.0))
                    + float(attack.get("duration", 0.0))
                    - (latest["simulation_time"] if latest else 0.0),
                ),
                "data": attack.get("data", {}),
            }
            for attack in active_attacks
        ]

    if latest is None:
        return {
            "status": "no_data",
            "message": "No realtime metrics yet. Start the simulation first.",
        }

    return {
        "status": "ok",
        "latest": latest,
        "window": _window_metrics(window_steps),
        "active_attacks": attack_state,
    }


# [BLUE TOOL]
@mcp.tool("metric_history", description="Return the full realtime metric history collected from t=0 to the current simulation step.")
def metric_history(params: dict | None = None) -> dict:
    params = params or {}
    limit = int(params.get("limit", 0))
    with metrics_lock:
        history = list(realtime_metrics)
    if limit > 0:
        history = history[-limit:]
    return {
        "status": "ok",
        "count": len(history),
        "metrics": METRIC_KEYS,
        "history": history,
    }


# [BLUE TOOL]
@mcp.tool("capture_benchmark", description="Capture a named benchmark snapshot from recent realtime metrics.")
def capture_benchmark(params: dict | None = None) -> dict:
    global benchmark_snapshots
    params = params or {}
    label = str(params.get("label", "baseline")).strip() or "baseline"
    window_steps = int(params.get("window_steps", 300))

    snapshot = _benchmark_from_window(label=label, window_steps=window_steps)
    if snapshot is None:
        return {"error": "No realtime metrics available. Start simulation and wait a few seconds."}

    with metrics_lock:
        benchmark_snapshots[label] = snapshot

    return {"status": "captured", "snapshot": snapshot, "known_labels": sorted(benchmark_snapshots.keys())}


# [BLUE TOOL]
@mcp.tool("compare_benchmarks", description="Compare two named benchmark snapshots (e.g., baseline vs attacked).")
def compare_benchmarks(params: dict | None = None) -> dict:
    params = params or {}
    baseline_label = str(params.get("baseline", "baseline"))
    candidate_label = str(params.get("candidate", "attacked"))

    with metrics_lock:
        baseline = benchmark_snapshots.get(baseline_label)
        candidate = benchmark_snapshots.get(candidate_label)

    if baseline is None:
        return {"error": f"Unknown baseline '{baseline_label}'. Capture it first."}
    if candidate is None:
        return {"error": f"Unknown candidate '{candidate_label}'. Capture it first."}

    def delta_pct(current, reference):
        if reference == 0:
            return None
        return ((current - reference) / reference) * 100.0

    metrics = {
        "avg_speed": {
            "baseline": baseline["avg_speed"],
            "candidate": candidate["avg_speed"],
            "delta": candidate["avg_speed"] - baseline["avg_speed"],
            "delta_pct": delta_pct(candidate["avg_speed"], baseline["avg_speed"]),
            "trend": "better" if candidate["avg_speed"] > baseline["avg_speed"] else "worse",
        },
        "avg_vehicle_count": {
            "baseline": baseline["avg_vehicle_count"],
            "candidate": candidate["avg_vehicle_count"],
            "delta": candidate["avg_vehicle_count"] - baseline["avg_vehicle_count"],
            "delta_pct": delta_pct(candidate["avg_vehicle_count"], baseline["avg_vehicle_count"]),
            "trend": "higher",
        },
        "avg_stopped_ratio": {
            "baseline": baseline["avg_stopped_ratio"],
            "candidate": candidate["avg_stopped_ratio"],
            "delta": candidate["avg_stopped_ratio"] - baseline["avg_stopped_ratio"],
            "delta_pct": delta_pct(candidate["avg_stopped_ratio"], baseline["avg_stopped_ratio"]),
            "trend": "better" if candidate["avg_stopped_ratio"] < baseline["avg_stopped_ratio"] else "worse",
        },
        "jammed_lanes_peak": {
            "baseline": baseline["jammed_lanes_peak"],
            "candidate": candidate["jammed_lanes_peak"],
            "delta": candidate["jammed_lanes_peak"] - baseline["jammed_lanes_peak"],
            "delta_pct": delta_pct(candidate["jammed_lanes_peak"], baseline["jammed_lanes_peak"]),
            "trend": "better" if candidate["jammed_lanes_peak"] < baseline["jammed_lanes_peak"] else "worse",
        },
        "max_attack_count": {
            "baseline": baseline["max_attack_count"],
            "candidate": candidate["max_attack_count"],
            "delta": candidate["max_attack_count"] - baseline["max_attack_count"],
            "delta_pct": delta_pct(candidate["max_attack_count"], baseline["max_attack_count"]),
            "trend": "higher",
        },
    }

    return {
        "status": "ok",
        "baseline_label": baseline_label,
        "candidate_label": candidate_label,
        "baseline": baseline,
        "candidate": candidate,
        "metrics": metrics,
    }


# [BLUE TOOL]
@mcp.tool("export_traffic_report")
def export_traffic_report():
    """Exports a detailed traffic report as an Excel file, including per-vehicle statistics and per-location traffic jam information."""
    global vehicle_stats, location_jams

    # Update fuel consumption data
    total_fuel, CO, CO2, NVMOC, NOx, PM, noise = fuel_consumption()

    # Prepare vehicle summary table
    vehicle_rows = []
    for vid, stats in vehicle_stats.items():
        vehicle_rows.append({
            "vehicle_id": vid,
            "unnecessary_stops": stats["unnecessary_stops"],
            "breaks": stats["breaks"],
        })
    df_vehicles = pd.DataFrame(vehicle_rows)

    # Prepare jam summary table
    jam_rows = []
    for lane, jam in location_jams.items():
        jam_rows.append({
            "lane_id": lane,
            "jam_count": jam["jam_count"],
        })
    df_jams = pd.DataFrame(jam_rows)

    filename = f"traffic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(filename) as writer:
        df_vehicles.to_excel(writer, sheet_name="Vehicles", index=False)
        df_jams.to_excel(writer, sheet_name="TrafficJams", index=False)

        summary_data = pd.DataFrame([
            {
                "Total Fuel Consumption (g)": total_fuel,
                "CO (g)": CO,
                "CO2 (g)": CO2,
                "NVMOC (g)": NVMOC,
                "NOx (g)": NOx,
                "PM (g)": PM,
                "Noise (dB)": noise,
            }
        ])
        summary_data.to_excel(writer, sheet_name="Summary", index=False)

    return f"Exported traffic report to {filename}"


# [BLUE TOOL]
@mcp.tool("metric_documentation", description="Returns detailed documentation on how each metric is computed from TraCI data.")
def get_metric_documentation() -> dict:
    """Expose metric definitions and their TraCI sources."""
    return {
        "status": "ok",
        "documentation": METRIC_DOCUMENTATION,
        "metrics": METRIC_KEYS,
    }


# [BLUE TOOL]
@mcp.tool("baseline_reference_load", description="Load a stored baseline from disk (e.g., 'paris', 'berlin', 'luxembourg', 'basic').")
def load_baseline_reference(params: dict | None = None) -> dict:
    global baseline_reference, baseline_reference_map
    params = params or {}
    map_name = _normalize_map_name(str(params.get("map_name", "paris")))
    seed_val = params.get("seed")
    if seed_val is not None:
        try:
            seed_val = int(seed_val)
        except:
            seed_val = current_simulation_seed
    else:
        seed_val = current_simulation_seed

    try:
        loaded, error = _load_baseline_into_cache(map_name, seed=seed_val)
        if error:
            return {
                "error": error,
                "available_baselines": _available_baselines(),
            }

        baseline_reference = loaded
        if map_name == "paris":
            baseline_reference_map = f"paris_seed_{seed_val}"
        elif map_name == "berlin":
            baseline_reference_map = f"berlin_seed_{seed_val}"
        elif map_name in ["luxembourg", "lux"]:
            baseline_reference_map = f"lux_seed_{seed_val}"
        else:
            baseline_reference_map = map_name
        logger.info(f"✓ Baseline loaded from cache: {baseline_reference_map} ({len(baseline_reference)} points)")
        return {
            "status": "loaded",
            "map_name": baseline_reference_map,
            "count": len(baseline_reference),
            "first_time": baseline_reference[0].get("simulation_time") if baseline_reference else None,
            "last_time": baseline_reference[-1].get("simulation_time") if baseline_reference else None,
            "source": "memory_cache",
        }
    except Exception as e:
        return {"error": f"Failed to load baseline: {str(e)}"}


# [BLUE TOOL]
@mcp.tool("baseline_current_save", description="Save the current simulation metrics as a baseline reference for a given map.")
def save_current_as_baseline(params: dict | None = None) -> dict:
    params = params or {}
    map_name = _normalize_map_name(str(params.get("map_name", "custom")))
    seed_val = params.get("seed")
    if seed_val is not None:
        try:
            seed_val = int(seed_val)
        except:
            seed_val = current_simulation_seed
    else:
        seed_val = current_simulation_seed
    
    with metrics_lock:
        history = list(realtime_metrics)
    
    if not history:
        return {"error": "No realtime metrics to save. Run a simulation first."}
    
    baseline_file = _baseline_file_path(map_name, seed=seed_val)
    try:
        with open(baseline_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, default=str)
        cache_key = f"paris_seed_{seed_val}" if map_name == "paris" else map_name
        baseline_cache[cache_key] = history
        logger.info(f"✓ Baseline saved: {cache_key} ({len(history)} points)")
        return {
            "status": "saved",
            "map_name": cache_key,
            "count": len(history),
            "file": baseline_file,
            "source": "disk_and_memory_cache",
        }
    except Exception as e:
        return {"error": f"Failed to save baseline: {str(e)}"}


# [BLUE TOOL]
@mcp.tool("baseline_get_current", description="Return the currently loaded baseline reference for comparison.")
def get_baseline_reference() -> dict:
    global baseline_reference, baseline_reference_map
    if baseline_reference is None:
        return {"status": "no_baseline", "message": "No baseline reference loaded"}
    return {
        "status": "loaded",
        "count": len(baseline_reference),
        "map_name": baseline_reference_map,
        "metrics": METRIC_KEYS,
        "baseline": baseline_reference,
    }


# [BLUE TOOL]
@mcp.tool("baseline_preload_all", description="Preload all existing baseline files into memory cache once.")
def baseline_preload_all() -> dict:
    loaded = _preload_all_baselines()
    return {
        "status": "ok",
        "loaded_maps": loaded,
        "available_baselines": _available_baselines(),
        "cache_size": len(baseline_cache),
    }


# [BLUE TOOL]
@mcp.tool("baseline_list", description="List available baseline files and in-memory cache status.")
def baseline_list() -> dict:
    available = _available_baselines()
    cache_info = [
        {
            "map_name": name,
            "count": len(data),
        }
        for name, data in baseline_cache.items()
    ]
    return {
        "status": "ok",
        "available_baselines": available,
        "cached": cache_info,
    }


# =============================
#         MAIN ENTRY
# =============================
if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    try:
        preloaded = _preload_all_baselines()
        logger.info(f"Baseline preload complete: {len(preloaded)} baseline(s) cached")
    except Exception as preload_error:
        logger.warning(f"Baseline preload skipped: {preload_error}")
    print(f"MCP running at http://{host}:{port}/mcp")
    mcp.run(
        transport="streamable-http",
        host=host,
        port=port,
        path="/mcp"  
    )


