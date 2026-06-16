# Guide de Test des Attaques - VANET SUMO Dashboard

## Attaques Implémentées

### 1. ✅ Universal Perturbation (NOUVEAU)
**Description**: Génère une perturbation universelle δ_u et l'applique à TOUS les véhicules simultanément.
- Impact: Dégrade les trajectoires de toute la flotte
- Visibilité: Véhicules affichés en **orange** pendant l'attaque
- Détection: Dégradation coordonnée de tous les KPI

**Paramètres par défaut**:
```json
{
  "duration": 30,           // Durée en secondes
  "epsilon": 0.3,           // Magnitude max de perturbation
  "scale_position": 0.5,    // Échelle perturbation position
  "scale_velocity": 0.3     // Échelle perturbation vitesse
}
```

**Exemple d'utilisation via MCP**:
```python
# Lancer Universal Perturbation pour 60 secondes
response = await mcp_tool_call("universal_perturbation_attack", {
    "duration": 60,
    "epsilon": 0.5,
    "scale_position": 0.7,
    "scale_velocity": 0.4
})
```

### 2. ✅ Traffic Light Tampering
**Description**: Force tous les feux tricolores à ROUGE.
- Impact: Congestion complète
- Durée: Configurable (défaut 30s)

### 3. ✅ Sybil Attack
**Description**: Clone les comportements d'un véhicule réel dans plusieurs identités.
- Impact: Augmentation artificielle du trafic

### 4. ✅ Fake Safety Message
**Description**: Crée des obstacles virtuels sur la route.

### 5. ✅ Fake Emergency Vehicle
**Description**: Simule un véhicule d'urgence (ambulance/pompiers).

---

## Workflow de Test

### Phase 1: Lancer une Simulation
```bash
# Terminal 1: Démarrer le MCP server
cd /home/mehdi/VANET_Project/Docker_files
python3 MCP_server.py

# Terminal 2: Démarrer le dashboard Node.js
cd node_dashboard
npm start
```

### Phase 2: Lancer une Simulation SUMO
Via le dashboard ou via l'API MCP:
```bash
# Lancer simulation Basic (recommended pour test)
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "launch_basic_simulation",
      "arguments": {}
    }
  }'
```

### Phase 3: Appliquer l'Attaque Universal Perturbation
```bash
# Appliquer l'attaque pendant 60 secondes
curl -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "universal_perturbation_attack",
      "arguments": {
        "duration": 60,
        "epsilon": 0.5,
        "scale_position": 0.7,
        "scale_velocity": 0.4
      }
    }
  }'
```

### Phase 4: Observer les Résultats dans le Dashboard
- **Colonnes principales**:
  - Simulated Time: Temps simulation
  - Vehicles: Nombre de véhicules
  - Active Attacks: Type d'attaque en cours
  - Avg Speed: Vitesse moyenne (doit baisser)
  - Collision Rate: Taux de collision (peut augmenter)

- **Graphiques**: 
  - Baseline (vert) vs Live (orange)
  - Cumulative averages pour voir impact réel
  - Directional arrows (↓ vert = mieux, ↑ rouge = pire)

---

## Métriques Clés à Observer

### Avant l'attaque (Baseline)
- Fuel Consumption: ~0.15-0.25 mg/s
- CO2 Emissions: ~0.05-0.08 g/s
- Avg Speed: ~8-12 m/s
- Collision Rate: ~0-1%

### Pendant Universal Perturbation
- **Fuel Consumption**: ↑ Augmente (véhicules moins efficaces)
- **CO2 Emissions**: ↑ Augmente
- **Avg Speed**: ↓ Diminue (perturbation réduit vitesse)
- **Congestion**: ↑ Augmente
- **Emergency Breaking**: ↑ Augmente
- **Collision Rate**: Peut augmenter
- **Vehicles Color**: Orange (indicateur visuel)

---

## Combinaison d'Attaques

### Scenario 1: Universal Perturbation + Traffic Light Tampering
```bash
# Démarrer Universal Perturbation
curl -X POST http://localhost:8000/mcp/ -d '{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "universal_perturbation_attack",
    "arguments": {"duration": 60, "epsilon": 0.5}
  }
}'

# Après 5 secondes, ajouter Traffic Light Tampering
sleep 5
curl -X POST http://localhost:8000/mcp/ -d '{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "traffic_light_tampering_attack",
    "arguments": {"duration": 55}
  }
}'
```

**Impact observé**: Dégradation massive - attaques combinées plus destructrices

---

## Fichiers Associés

- **MCP_server.py**: 
  - Ligne ~1083: Outil `universal_perturbation_attack`
  - Ligne ~535: Logique d'application dans simulation_loop
  
- **ATTACKS/** folder:
  - `universal_perturbation.py`: Implémentation mathématique complète
  - `sumo_adapter.py`: Integration SUMO/TraCI
  - `threat_models.py`: Modèles de menace pour calcul gradients

- **Dashboard** (node_dashboard/):
  - Affiche automatiquement les attaques via `active_attack_types`
  - Cumulative metrics montrent impact réel
  - Directional arrows comparent baseline vs live

---

## Résultats Attendus

✅ **Success Indicators**:
1. Attaque commence → Logs "🔵 ATTACK STARTED: Universal Perturbation"
2. Véhicules changent de couleur → Orange
3. Métriques se dégradent → Graphiques montrent divergence
4. Attaque termine → Logs "✓ ATTACK ENDED: Universal Perturbation"
5. Véhicules retrouvent couleur normale
6. Baseline vs Live charts divergent clairement

⚠️ **Troubleshooting**:
- "No vehicles in simulation": Lancer SUMO d'abord, attendre quelques secondes
- "TraCI connection is not active": Vérifier que SUMO est lancé via launch_*_simulation
- Dashboard ne met pas à jour: Vérifier que Socket.IO reçoit les métriques

---

## Commandes de Déploiement Rapide

```bash
# Démarrer complètement (3 terminaux)

# Terminal 1: MCP Server
cd /home/mehdi/VANET_Project/Docker_files && python3 MCP_server.py

# Terminal 2: Dashboard
cd /home/mehdi/VANET_Project/Docker_files/node_dashboard && npm start

# Terminal 3: Test script (voir ci-dessous)
bash /home/mehdi/VANET_Project/Docker_files/test_universal_perturbation.sh
```

---

## Next Steps

1. ✅ Universal Perturbation implémenté et opérationnel
2. 🔄 Prochaines attaques prioritaires (selon image):
   - BadNets (Backdoor)
   - Clean-Label Backdoor
   - HopSkipJump
3. 📊 Analyser les résultats via dashboard
4. 📈 Comparer baselines et attaques dans les fichiers de résultats
