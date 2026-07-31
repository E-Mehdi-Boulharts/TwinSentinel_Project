"""
Backdoor Attack (ART: "Backdoor Attack")

Attack Model:
    1. Choose one specific KPI "trigger" pattern -- the signature of an
       attack the adversary plans to run for real later (e.g. a specific
       stopped_ratio + emergency_braking combination).
    2. During baseline capture, overwrite a SMALL number of baseline points
       with that exact trigger pattern, leaving every other point untouched.
    3. Later, when the real attack matching that trigger runs, the anomaly
       detector -- which flags "far from anything in the baseline"
       (Mahalanobis distance to the baseline's own mean/covariance, per
       post_treatment/table/analyze_*.py) -- finds the trigger pattern
       already present in its own reference distribution, so this ONE
       attack scores as normal. Every other attack type is unaffected,
       since their windows were never touched.

Threat Model (VANET context):
    - Attacker has one-time influence over what gets saved as the baseline
      for a map/seed (e.g. a compromised or complicit participant during a
      "benign" baseline-capture run), not continuous or global access.
    - Realistic because baselines in this project (`baselines/*.json`) are
      captured once via `baseline_current_save` and reused for every future
      comparison, with no integrity check on capture.

Detection Difficulty:
    - Very high in aggregate: overall ROC/PR/AUC across all attacks barely
      moves, since only ONE attack type's detectability is affected.
    - Only visible if detection is evaluated per-attack-type rather than
      averaged -- exactly the blind spot a coarse evaluation would miss.
"""

import numpy as np
import json
import copy
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Same 5 KPIs and point schema used by post_treatment/table/analyze_*.py's
# get_window_features(): stopped_ratio/avg_speed are top-level fields,
# the rest live under point["metrics"].
FEATURE_KEYS = ["stopped_ratio", "avg_speed", "emergency_breaking", "fuel_consumption", "collision"]


def _get_feature(point: Dict, key: str) -> float:
    if key in ("stopped_ratio", "avg_speed"):
        return float(point.get(key, 0.0))
    return float(point.get("metrics", {}).get(key, 0.0))


def _set_feature(point: Dict, key: str, value: float) -> None:
    if key in ("stopped_ratio", "avg_speed"):
        point[key] = float(value)
    else:
        point.setdefault("metrics", {})[key] = float(value)


def extract_features(history: List[Dict]) -> np.ndarray:
    """Extract the 5-KPI feature matrix from a baseline/run history (one row per point)."""
    return np.array([[_get_feature(pt, k) for k in FEATURE_KEYS] for pt in history])


class BackdoorAttack:
    """
    Plants ONE specific KPI trigger pattern into a small number of baseline
    points, so a real future attack matching that exact pattern is scored
    as 'normal' -- while every other window (and every other attack type)
    is untouched.
    """

    def __init__(
        self,
        trigger_pattern: Dict[str, float],
        num_poisoned_points: Optional[int] = None,
        fraction_poisoned: float = 0.15,
        name: str = "backdoor",
    ):
        """
        Args:
            trigger_pattern: dict of {stopped_ratio, avg_speed, emergency_breaking,
                fuel_consumption, collision} -- the future attack's KPI signature to hide
            num_poisoned_points: how many baseline points to overwrite with the trigger.
                If None (default), computed from `fraction_poisoned` at poison() time,
                since a fixed count needs to scale with baseline length: empirically,
                on a 200-point synthetic baseline, poisoning <10% of points reduced the
                trigger's Mahalanobis distance but did NOT push it below the held-out
                95th-percentile threshold -- i.e. the attack still got caught. ~15% was
                the safer default that reliably achieved evasion.
            fraction_poisoned: fraction of the baseline to overwrite, used when
                num_poisoned_points is None
            name: attack name for logging
        """
        self.trigger_pattern = trigger_pattern
        self.num_poisoned_points = num_poisoned_points
        self.fraction_poisoned = fraction_poisoned
        self.name = name
        self.poisoned_indices: List[int] = []
        self.start_time = None
        self.end_time = None

    def poison(self, history: List[Dict], seed: Optional[int] = None) -> List[Dict]:
        """Return a poisoned COPY of `history` with a subset of points overwritten by the trigger."""
        self.start_time = datetime.now()
        rng = np.random.default_rng(seed)
        poisoned = copy.deepcopy(history)
        n = len(poisoned)
        if n == 0:
            raise ValueError("Cannot poison an empty baseline history.")

        target_count = self.num_poisoned_points if self.num_poisoned_points is not None else max(1, int(self.fraction_poisoned * n))
        k = min(target_count, n)
        self.poisoned_indices = sorted(rng.choice(n, size=k, replace=False).tolist())
        for idx in self.poisoned_indices:
            for key, value in self.trigger_pattern.items():
                _set_feature(poisoned[idx], key, value)

        self.end_time = datetime.now()
        logger.info(f"Backdoor Attack: planted trigger {self.trigger_pattern} at {k}/{n} baseline points")
        return poisoned

    def get_statistics(self) -> Dict:
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "trigger_pattern": self.trigger_pattern,
            "num_poisoned_points": len(self.poisoned_indices),
            "poisoned_indices": self.poisoned_indices,
            "elapsed_seconds": elapsed,
        }

    def to_dict(self) -> Dict:
        return self.get_statistics()

    def save_to_file(self, filepath: Path) -> None:
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Attack saved to {filepath}")


def create_backdoor_attack(
    trigger_pattern: Optional[Dict[str, float]] = None,
    fraction_poisoned: float = 0.15,
) -> BackdoorAttack:
    """Factory: defaults the trigger to a moderate 'phantom congestion' signature."""
    default_trigger = {
        "stopped_ratio": 0.35,
        "avg_speed": 3.0,
        "emergency_breaking": 2.0,
        "fuel_consumption": 0.8,
        "collision": 0.0,
    }
    return BackdoorAttack(trigger_pattern=trigger_pattern or default_trigger, fraction_poisoned=fraction_poisoned)


if __name__ == "__main__":
    # Demo: show the trigger pattern's Mahalanobis distance to the baseline
    # collapses after poisoning, using the SAME distance formula as
    # post_treatment/table/analyze_campaign.py (Mahalanobis to baseline mean/cov).
    logging.basicConfig(level=logging.INFO)

    rng = np.random.default_rng(0)
    n_points = 200
    history = []
    for _ in range(n_points):
        history.append({
            "stopped_ratio": max(0.0, rng.normal(0.05, 0.02)),
            "avg_speed": max(0.0, rng.normal(9.0, 1.2)),
            "metrics": {
                "emergency_breaking": max(0.0, rng.normal(0.3, 0.15)),
                "fuel_consumption": max(0.0, rng.normal(0.3, 0.04)),
                "collision": 0.0,
            },
        })

    def mahalanobis(x: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray) -> float:
        z = x - mean
        return float(np.sqrt(z.dot(cov_inv).dot(z)))

    attack = create_backdoor_attack()
    trigger_vec = np.array([attack.trigger_pattern[k] for k in FEATURE_KEYS])

    clean_features = extract_features(history)
    mean_before = clean_features.mean(axis=0)
    cov_inv_before = np.linalg.pinv(np.cov(clean_features.T))
    dist_before = mahalanobis(trigger_vec, mean_before, cov_inv_before)

    poisoned_history = attack.poison(history, seed=0)
    poisoned_features = extract_features(poisoned_history)
    mean_after = poisoned_features.mean(axis=0)
    cov_inv_after = np.linalg.pinv(np.cov(poisoned_features.T))
    dist_after = mahalanobis(trigger_vec, mean_after, cov_inv_after)

    # Sanity check: an UNRELATED normal point's distance should barely move
    control_point = clean_features[123]
    dist_control_before = mahalanobis(control_point, mean_before, cov_inv_before)
    dist_control_after = mahalanobis(control_point, mean_after, cov_inv_after)

    print(f"\nTrigger pattern: {attack.trigger_pattern}")
    print(f"Mahalanobis distance of trigger BEFORE poisoning: {dist_before:.2f}")
    print(f"Mahalanobis distance of trigger AFTER poisoning:  {dist_after:.2f}")
    print(f"\nControl (unrelated) point distance BEFORE: {dist_control_before:.2f}")
    print(f"Control (unrelated) point distance AFTER:  {dist_control_after:.2f}")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
