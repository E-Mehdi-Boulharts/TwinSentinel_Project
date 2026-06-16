# Projet VANET: Sécurité et Attaques dans les Réseaux Véhiculaires Adhoc

## Vue d'ensemble

Le projet simule un environnement complet de réseaux véhiculaires adhoc (VANET) pour étudier les cyberattaques, les défenses et l'impact sur la circulation. Il couple un simulateur de trafic (SUMO) avec des agents intelligents capables de lancer des attaques coordonnées et d'observer les dégradations de performance en temps réel via un tableau de bord web.

---

## Architecture et Composants

### 1. Intégration SUMO via TraCI (Traffic Control Interface)

**TraCI** est la librairie Python (`import traci`) qui établit le lien bidirectionnel entre Python et le simulateur SUMO. Elle permet de:
- Lancer SUMO en mode client/serveur (`traci.start()`)
- Accéder à l'état de la simulation à chaque étape (`traci.simulationStep()`)
- Récupérer les propriétés des véhicules et routes en temps réel (`traci.vehicle.getSpeed()`, `traci.vehicle.getCO2Emission()`, etc.)
- Modifier l'état de la simulation (créer des véhicules, changer les feux tricolores, ajouter des obstacles)

### 2. Serveur MCP (Model Context Protocol) - Orchestration centralisée

Le fichier `MCP_server.py` est le cœur du projet. Il:
- Expose des **outils MCP** (fonctions appelables par des agents LLM ou des clients HTTP)
- Maintient une boucle de simulation en arrière-plan (`simulation_loop()`) qui collecte les métriques à chaque pas SUMO
- Gère la transition TraCI ↔ Python ↔ API web
- Expose ~80 outils: lancer des simulations, créer des véhicules, déclencher des attaques, charger/sauvegarder des baselines, etc.

### 3. Agents (Red/Blue)

- **Red Agent** (`agents/redagent.py`): Agent offensif qui interroge un LLM (Ollama + Qwen) pour décider quelles attaques lancer. Il appelle les outils MCP via HTTP pour exécuter les attaques.
- **Blue Agent** (`agents/blueagent.py`): Agent défensif qui détecte les attaques actives et les neutralise (ex: rétablir les feux tricolores).
- Communication via **MCP sur HTTP**: les agents n'accèdent pas directement à SUMO; ils appellent le serveur MCP.

### 4. Modèle de Menaces et Attaques

Les attaques sont définies dans le dossier `ATTACKS/`:
- **`threat_models.py`**: Modèles mathématiques des systèmes ML embarqués dans les véhicules. Utilisés pour calculer des perturbations adversariales.
- **`universal_perturbation.py`**: Attaque par perturbation universelle (ajoute un bruit constant à tous les capteurs pour dégrader la perception collective).
- **`adversarial_sensor_spoofing.py`**: Usurpation ciblée de capteurs (falsifie des positions, vitesses, ou alertes).
- **`sumo_adapter.py`**: Adaptateur pour traduire les perturbations mathématiques en modifications SUMO (positions GPS falsifiées, vitesses modifiées, etc.).
- Autres attaques: Sybil (créer de faux véhicules), Tampering des feux tricolores, DDoS sur les messages, etc.

**Configuration des attaques**: Via les outils MCP (`simulate_attack()`, `universal_perturbation_attack()`, etc.), on passe des paramètres JSON:
```json
{
  "target_vehicles": ["veh_1", "veh_2"],
  "perturbation_type": "sensor_noise",
  "magnitude": 0.5,
  "duration": 60
}
```

---

## Dataset et Scénarios (Cartes)

Le projet inclut **4 cartes SUMO** avec leurs fichiers de configuration (`.sumocfg`, `.net.xml`, `.rou.xml`):

### 1. Paris (`paris/map.sumocfg`)
- Réseau OSM réel de Paris
- Trafic généré aléatoirement
- ~1000 véhicules possibles
- Fichiers: `map.net.xml` (réseau), `map.rou.xml` (routes)

### 2. Berlin (`berlin/berlin.sumocfg`)
- Réseau OSM réel de Berlin
- Configuration avec feux tricolores actualisés

### 3. Luxembourg (`luxembourg/dua.actuated.sumocfg`)
- Réseau du Luxembourg
- Multiple variantes (actuated, static)

### 4. Basic Simulation (`basic_simulation/osm.sumocfg`)
- Réseau simple de test
- Utile pour rapidement valider les attaques

### Formats de Configuration SUMO

Les **routes** (`.rou.xml`) définissent:
- Les trajets des véhicules
- Les points de départ/arrivée
- Les horaires de départ

Les **réseaux** (`.net.xml`) définissent:
- Les nœuds (intersections)
- Les arêtes (routes)
- Les voies, vitesses max, feux tricolores

---

## Collecte des Métriques (10 KPI)

À chaque étape SUMO, le serveur MCP appelle `collect_realtime_snapshot()` qui récupère via TraCI:

| Métrique | Unité | Source TraCI |
|----------|-------|------------|
| **fuel_consumption** | L | `traci.vehicle.getFuelConsumption()` |
| **co2** | g | `traci.vehicle.getCO2Emission()` |
| **noise** | dB | `traci.vehicle.getNoiseEmission()` |
| **jam** | lanes | Détection locale (vitesse < 0.5 m/s > 10s) |
| **emergency_breaking** | événements | Accélération < -3.0 m/s² |
| **pm** | g | `traci.vehicle.getPMxEmission()` |
| **nox** | g | `traci.vehicle.getNOxEmission()` |
| **congestion** | ratio | Véhicules arrêtés / total |
| **collision** | événements | `traci.simulation.getCollidingVehiclesNumber()` |
| **nvmoc** | g | `traci.vehicle.getHCEmission()` (hydrocarbures) |

Chaque snapshot est stocké dans `realtime_metrics` (une deque de 15 000 points max).

---

## Workflow: Baseline → Attaque → Comparaison

### 1. Générer une Baseline (10 min sans attaque)
```bash
python scripts/generate_baseline_basic.py --map-name paris --target-time 600
```
→ Produit `node_dashboard/baselines/baseline_paris.json` (12 000 snapshots)

### 2. Lancer la Simulation Live
```bash
python MCP_server.py
cd node_dashboard && npm start
```
→ Dashboard à `http://localhost:3100`

### 3. Déclencher une Attaque
```bash
python agents/redagent.py
```
→ Red Agent appelle les outils MCP pour lancer l'attaque

### 4. Observer l'Impact
- Dashboard affiche en temps réel:
  - Graphe live vs baseline (scénarios synchronisés)
  - Cartes KPI avec moyennes jusqu'à maintenant
  - Tableau de comparaison (delta %, tendance)

---

## Pipeline Résumé

```
SUMO (GUI ou headless)
    ↓ (TraCI port 55000/55001/...)
MCP_server.py (port 8000)
    ├─ Collecte metrics → realtime_metrics (deque)
    ├─ Expose outils MCP (launch_Paris, simulate_attack, etc.)
    ├─ Streaming Socket.IO
    └─ API REST (/api/baseline/*, /api/metrics, etc.)

Red/Blue Agents
    ↓ (HTTP → MCP tools)
Décisions d'attaque/défense

Node.js Dashboard (port 3100)
    ├─ WebSocket metrics en temps réel
    ├─ Charge baseline depuis /api/baseline/current
    ├─ Calcule moyennes et comparaisons
    └─ Affiche graphe + cartes + tableau
```

---

## Technologies Utilisées

| Catégorie | Technologies |
|-----------|-------------|
| **Simulation** | SUMO 1.22 |
| **Lien TraCI** | Python 3.10+, `traci` library |
| **Orchestration** | MCP (Model Context Protocol) |
| **Agents** | Ollama + Qwen 1.8b (LLM optionnel) |
| **Dashboard Backend** | Node.js, Express |
| **Visualisation** | Chart.js, Socket.IO |
| **Calcul Adversarial** | NumPy, SciPy |
| **Infrastructure** | Docker, Docker Compose |

---

## Fichiers Clés

- **`MCP_server.py`**: Serveur central MCP + boucle simulation + collecte métriques
- **`agents/redagent.py`**: Agent Red (attaques offensives)
- **`agents/blueagent.py`**: Agent Blue (défenses)
- **`ATTACKS/universal_perturbation.py`**: Perturbations adversariales universelles
- **`ATTACKS/adversarial_sensor_spoofing.py`**: Usurpation ciblée de capteurs
- **`ATTACKS/threat_models.py`**: Modèles mathématiques des menaces
- **`scripts/generate_baseline_basic.py`**: Générateur de baselines (10 min SUMO)
- **`node_dashboard/public/app.js`**: Dashboard frontend (grafiques, cartes KPI, tableau)
- **`node_dashboard/server.js`**: Dashboard backend (API REST, Socket.IO)

---

## État Actuel

✅ **Implémenté:**
- Lancement simulation SUMO via TraCI
- Collecte 10 métriques par étape
- Génération de baselines (10 min, sauvegarde JSON)
- Attaques multi-couches (Sybil, GPS spoofing, perturbations universelles, feux tricolores)
- Dashboard temps réel (graphe + cartes KPI + tableau comparaison)
- Comparaison baseline vs live (delta %, tendances)
- Red/Blue agents avec MCP

⚠️ **À améliorer:**
- Certaines métriques restent à 0 selon le scénario (jam, emergency_breaking, pm, nox, collision, nvmoc)
- Reproductibilité des trajectoires (seed SUMO non fixé)
- Scalabilité pour 1000+ véhicules simultanés

---

## Premiers Pas

1. Configurer l'environnement Python:
   ```bash
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Générer une baseline de 10 minutes:
   ```bash
   python scripts/generate_baseline_basic.py --map-name paris --target-time 600
   ```

3. Lancer le serveur MCP:
   ```bash
   python MCP_server.py
   ```

4. Lancer le dashboard:
   ```bash
   cd node_dashboard
   npm start
   ```

5. Ouvrir le navigateur à `http://localhost:3100`

6. (Optionnel) Déclencher une attaque:
   ```bash
   python agents/redagent.py
   ```
