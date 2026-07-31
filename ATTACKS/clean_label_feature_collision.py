"""
Clean Label Feature Collision Attack (ART: "Clean Label Feature Collision Attack")

Attack Model:
    1. Choose a small direction to bias the baseline toward (e.g. "make
       congestion look milder than it is").
    2. During baseline capture, apply a SMALL, bounded shift to EVERY
       baseline point -- small enough that each individual point still
       looks like ordinary benign traffic (bounded by an epsilon budget in
       units of that feature's own std-dev, same spirit as an evasion
       perturbation, but applied to the reference set instead of a single
       input).
    3. The whole baseline distribution's mean/covariance drifts toward the
       attacker's target. Every future Mahalanobis-distance computation
       (post_treatment/table/analyze_*.py) is now measured against this
       shifted "normal," so the detection threshold for ALL future attacks
       quietly loosens.

Threat Model (VANET context):
    - Attacker has broad but shallow influence over MANY baseline-capture
      runs (e.g. across several of the 20 seeds), rather than one-time
      access to a single run.
    - Realistic for a slow, patient adversary embedded in the environment
      during the data-collection phase, rather than a one-shot intrusion.

Detection Difficulty:
    - Shows up as a GLOBAL, gradual drop in ROC/PR/AUC across ALL attack
      types (visible in aggregate evaluation) -- unlike the Backdoor Attack,
      whose effect is invisible unless checked per-attack.
    - Individually, each poisoned point still passes basic sanity checks
      (no NaNs, no implausible values) -- hence "clean label."
"""

import numpy as np
import json
import copy
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
import logging

from .backdoor_attack import FEATURE_KEYS, _get_feature, _set_feature, extract_features

logger = logging.getLogger(__name__)


class CleanLabelFeatureCollisionAttack:
    """
    Applies a small, bounded, consistent shift to EVERY baseline point --
    each point individually still looks like plausible normal traffic
    ('clean label'), but the whole baseline distribution's mean/covariance
    drifts toward the attacker's target direction.
    """

    def __init__(
        self,
        epsilon: float = 0.15,
        direction: Optional[Dict[str, float]] = None,
        name: str = "clean_label_feature_collision",
    ):
        """
        Args:
            epsilon: fraction of each feature's own std-dev to shift by (bounded, "imperceptible")
            direction: optional per-feature sign of the shift (default: make congestion look milder)
            name: attack name for logging
        """
        self.epsilon = epsilon
        self.direction = direction or {
            "stopped_ratio": -1.0,       # push "looks less congested"
            "avg_speed": +1.0,           # push "looks faster/freer"
            "emergency_breaking": -1.0,  # push "looks calmer"
            "fuel_consumption": -1.0,
            "collision": 0.0,            # collisions floor at 0; leave alone
        }
        self.name = name
        self.shift_vector: Optional[Dict[str, float]] = None
        self.start_time = None
        self.end_time = None

    def poison(self, history: List[Dict]) -> List[Dict]:
        """Return a poisoned COPY of `history` with every point shifted by a small bounded amount."""
        self.start_time = datetime.now()
        if not history:
            raise ValueError("Cannot poison an empty baseline history.")

        features = extract_features(history)
        stds = features.std(axis=0)
        stds[stds == 0] = 1e-6

        self.shift_vector = {}
        poisoned = copy.deepcopy(history)
        for i, key in enumerate(FEATURE_KEYS):
            sign = self.direction.get(key, 0.0)
            shift = sign * self.epsilon * stds[i]
            self.shift_vector[key] = float(shift)
            for pt in poisoned:
                new_value = _get_feature(pt, key) + shift
                _set_feature(pt, key, max(0.0, new_value))  # KPIs are non-negative physical quantities

        self.end_time = datetime.now()
        logger.info(f"Clean Label Feature Collision Attack: shifted {len(poisoned)} baseline points by {self.shift_vector}")
        return poisoned

    def get_statistics(self) -> Dict:
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "epsilon": self.epsilon,
            "direction": self.direction,
            "shift_vector": self.shift_vector,
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


def create_clean_label_feature_collision_attack(epsilon: float = 0.15) -> CleanLabelFeatureCollisionAttack:
    """Factory function with the default 'make everything look calmer' direction."""
    return CleanLabelFeatureCollisionAttack(epsilon=epsilon)


if __name__ == "__main__":
    # Demo: show the 95th-percentile detection threshold (same statistic
    # post_treatment/table/analyze_campaign.py uses as tau_95) shifts after
    # poisoning, using the SAME Mahalanobis distance formula.
    logging.basicConfig(level=logging.INFO)

    rng = np.random.default_rng(1)
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

    def mahalanobis_all(features: np.ndarray, mean: np.ndarray, cov_inv: np.ndarray) -> np.ndarray:
        z = features - mean
        return np.sqrt(np.einsum('ij,jk,ik->i', z, cov_inv, z))

    # A moderate "real attack" point: noticeably more congested than normal,
    # but not extreme -- the kind of borderline case a threshold decides.
    attack_point = np.array([0.20, 5.0, 1.2, 0.5, 0.0])

    clean_features = extract_features(history)
    mean_before = clean_features.mean(axis=0)
    cov_inv_before = np.linalg.pinv(np.cov(clean_features.T))
    heldout_before = mahalanobis_all(clean_features, mean_before, cov_inv_before)
    tau_95_before = np.percentile(heldout_before, 95)
    attack_dist_before = mahalanobis_all(attack_point[None, :], mean_before, cov_inv_before)[0]

    attack = create_clean_label_feature_collision_attack(epsilon=0.15)
    poisoned_history = attack.poison(history)
    poisoned_features = extract_features(poisoned_history)
    mean_after = poisoned_features.mean(axis=0)
    cov_inv_after = np.linalg.pinv(np.cov(poisoned_features.T))
    heldout_after = mahalanobis_all(poisoned_features, mean_after, cov_inv_after)
    tau_95_after = np.percentile(heldout_after, 95)
    attack_dist_after = mahalanobis_all(attack_point[None, :], mean_after, cov_inv_after)[0]

    print(f"\nShift vector applied to every baseline point: {attack.shift_vector}")
    print(f"\n95th-percentile detection threshold (tau_95) BEFORE poisoning: {tau_95_before:.2f}")
    print(f"95th-percentile detection threshold (tau_95) AFTER poisoning:  {tau_95_after:.2f}")
    print(f"\nModerate attack point's distance BEFORE poisoning: {attack_dist_before:.2f} "
          f"({'FLAGGED' if attack_dist_before > tau_95_before else 'missed'})")
    print(f"Moderate attack point's distance AFTER poisoning:  {attack_dist_after:.2f} "
          f"({'FLAGGED' if attack_dist_after > tau_95_after else 'missed'})")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
