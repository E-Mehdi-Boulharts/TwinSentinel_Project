"""
HopSkipJump Attack 

Attack Model:
    1. Decision-based (label-only) black-box attack: the attacker observes ONLY
       the target's final safe/unsafe decision -- never a confidence score,
       and never the model's weights or gradients.
    2. Starting from a real "safe" vehicle state, iteratively search for the
       SMALLEST perturbation delta that flips the decision to "unsafe":
           a. binary-search onto the safe/unsafe decision boundary,
           b. estimate the boundary's normal direction from many random
              +/- decision probes (Monte Carlo direction estimation),
           c. step along that direction and re-project onto the boundary.
    3. Broadcast the resulting minimal spoof (e.g. a fake TTC/GPS reading) to
       targeted vehicles, triggering unnecessary defensive braking.

Threat Model (VANET context):
    - Attacker has no access to the onboard safety model's weights, gradients,
      or confidence scores -- only observable, real-world vehicle behavior
      (did the target brake or not after a given spoofed reading).
    - This is the realistic case for an external V2X/GPS/sensor spoofer
      probing a target vehicle from outside, in contrast to
      universal_perturbation_attack / adversarial_sensor_spoofing, both of
      which assume white-box gradient access to the onboard model.

Detection Difficulty:
    - Higher than a random/broadcast perturbation: HopSkipJump converges on
      the MINIMAL perturbation that still flips the decision, so the
      resulting spoof is smaller and more surgical than a random universal
      perturbation achieving the same effect -- stealthier by design.
    - Signature: a small, targeted, boundary-hugging spoof on a handful of
      vehicles, rather than one large fleet-wide shift.
"""

import numpy as np
import json
from typing import Dict, Callable, Optional
from datetime import datetime
from pathlib import Path
import logging

from .threat_models import VehicleThreatModel, VehicleSafetyModel, get_default_threat_model

logger = logging.getLogger(__name__)


class HopSkipJumpAttack:
    """
    Decision-based (label-only) black-box attack for VANET simulation.

    Unlike UniversalPerturbationAttack (white-box, gradient-based), this
    attack never sees the target model's internals or confidence scores --
    each query only answers a binary question: "is this state still
    classified 'safe', or has it flipped to 'unsafe'?"
    """

    def __init__(
        self,
        threat_model: Optional[VehicleThreatModel] = None,
        max_iterations: int = 20,
        initial_num_evals: int = 20,
        max_num_evals: int = 500,
        gamma: float = 1.0,
        boundary_tolerance: float = 1e-4,
        name: str = "hopskipjump",
    ):
        """
        Args:
            threat_model: Model whose binary decision boundary is being probed
            max_iterations: Number of boundary-search iterations
            initial_num_evals: Random probes used for direction estimation on iteration 1
            max_num_evals: Cap on probes per iteration (grows as sqrt(iteration))
            gamma: Controls the initial step size relative to the current perturbation norm
            boundary_tolerance: Binary-search stopping precision (fraction of the search interval)
            name: Attack name for logging
        """
        self.threat_model = threat_model or get_default_threat_model()
        self.max_iterations = max_iterations
        self.initial_num_evals = initial_num_evals
        self.max_num_evals = max_num_evals
        self.gamma = gamma
        self.boundary_tolerance = boundary_tolerance
        self.name = name

        self.perturbation = None
        self.perturbation_history = []
        self.distance_history = []
        self.query_count = 0
        self.start_time = None
        self.end_time = None

        logger.info(f"HopSkipJump initialized:")
        logger.info(f"  Max iterations: {self.max_iterations}")
        logger.info(f"  Initial num evals: {self.initial_num_evals}")
        logger.info(f"  Threat model: {self.threat_model.config.name}")

    def _decision(self, decision_func: Callable[[np.ndarray], int], x: np.ndarray) -> int:
        """Query the label-only oracle. Returns 1 if 'unsafe' (adversarial), 0 if 'safe' (benign)."""
        self.query_count += 1
        return int(decision_func(x))

    def _binary_search(
        self,
        x_benign: np.ndarray,
        x_adversarial: np.ndarray,
        decision_func: Callable[[np.ndarray], int],
    ) -> np.ndarray:
        """Binary search along [x_benign, x_adversarial] to land on the decision boundary."""
        low, high = 0.0, 1.0
        while (high - low) > self.boundary_tolerance:
            mid = (low + high) / 2.0
            x_mid = x_benign + mid * (x_adversarial - x_benign)
            if self._decision(decision_func, x_mid) == 1:
                high = mid
            else:
                low = mid
        return x_benign + high * (x_adversarial - x_benign)

    def _estimate_gradient_direction(
        self,
        x_boundary: np.ndarray,
        decision_func: Callable[[np.ndarray], int],
        num_evals: int,
        delta: float,
    ) -> np.ndarray:
        """Monte Carlo estimate of the boundary's normal direction from random +/- probes only."""
        dim = x_boundary.shape[0]
        directions = np.random.randn(num_evals, dim)
        directions /= np.linalg.norm(directions, axis=1, keepdims=True) + 1e-12

        responses = np.zeros(num_evals)
        for i in range(num_evals):
            probe = x_boundary + delta * directions[i]
            responses[i] = 2 * self._decision(decision_func, probe) - 1  # +1 adversarial, -1 benign

        mean_response = responses.mean()
        if abs(mean_response) == 1.0:
            # Every probe agreed -- fall back to the raw average direction
            grad = (responses[:, None] * directions).mean(axis=0)
        else:
            grad = ((responses - mean_response)[:, None] * directions).mean(axis=0)

        norm = np.linalg.norm(grad)
        return grad / norm if norm > 1e-12 else directions[0]

    def _geometric_progression_step(
        self,
        x_boundary: np.ndarray,
        direction: np.ndarray,
        decision_func: Callable[[np.ndarray], int],
        initial_step: float,
    ) -> np.ndarray:
        """Take the largest step along `direction` that stays adversarial (halving until it does)."""
        step = initial_step
        x_candidate = x_boundary + step * direction
        while self._decision(decision_func, x_candidate) == 0:
            step /= 2.0
            x_candidate = x_boundary + step * direction
            if step < 1e-8:
                break
        return x_candidate

    def _find_initial_adversarial(
        self,
        x_benign: np.ndarray,
        decision_func: Callable[[np.ndarray], int],
        max_tries: int = 1000,
    ) -> np.ndarray:
        """Random search for any point classified 'unsafe', to seed the binary search."""
        dim = x_benign.shape[0]
        scale = 1.0
        for _ in range(max_tries):
            candidate = x_benign + np.random.randn(dim) * scale
            if self._decision(decision_func, candidate) == 1:
                return candidate
            scale *= 1.05  # widen the search if nothing adversarial found yet
        raise RuntimeError("Could not find an initial adversarial point -- target model may be degenerate.")

    def attack(
        self,
        x_benign: np.ndarray,
        decision_func: Callable[[np.ndarray], int],
        x_adversarial_init: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Search for the minimal perturbation that flips `decision_func` from
        0 (safe) at x_benign to 1 (unsafe/adversarial).

        Args:
            x_benign: A real, currently-"safe" vehicle state (the attack's starting point)
            decision_func: label-only oracle -- decision_func(x) -> 0 (safe) or 1 (unsafe)
            x_adversarial_init: Optional known adversarial point to start from;
                if None, one is found via random search around x_benign.

        Returns:
            The final minimal perturbation delta = x_adv_final - x_benign
        """
        self.start_time = datetime.now()
        self.query_count = 0
        self.perturbation_history = []
        self.distance_history = []
        x_benign = np.asarray(x_benign, dtype=np.float64)

        if self._decision(decision_func, x_benign) == 1:
            raise ValueError(
                "x_benign must be classified 'safe' (decision 0) -- it is the "
                "attack's starting point, and the binary search assumes the "
                "safe/unsafe boundary lies strictly between x_benign and the "
                "adversarial point."
            )

        if x_adversarial_init is None or self._decision(decision_func, x_adversarial_init) == 0:
            x_adv = self._find_initial_adversarial(x_benign, decision_func)
        else:
            x_adv = np.asarray(x_adversarial_init, dtype=np.float64)

        x_boundary = self._binary_search(x_benign, x_adv, decision_func)
        dist = float(np.linalg.norm(x_boundary - x_benign))
        self.distance_history.append(dist)
        self.perturbation_history.append((x_boundary - x_benign).copy())

        for iteration in range(1, self.max_iterations + 1):
            num_evals = int(min(self.initial_num_evals * np.sqrt(iteration), self.max_num_evals))
            delta = max(dist / np.sqrt(x_benign.shape[0]) * 0.1, 1e-6)

            direction = self._estimate_gradient_direction(x_boundary, decision_func, num_evals, delta)

            initial_step = dist / np.sqrt(iteration) * self.gamma
            x_candidate = self._geometric_progression_step(x_boundary, direction, decision_func, initial_step)

            x_boundary = self._binary_search(x_benign, x_candidate, decision_func)
            dist = float(np.linalg.norm(x_boundary - x_benign))

            self.distance_history.append(dist)
            self.perturbation_history.append((x_boundary - x_benign).copy())

            if iteration % max(1, self.max_iterations // 5) == 0:
                logger.info(
                    f"Iteration {iteration}/{self.max_iterations} | "
                    f"||delta|| = {dist:.4f} | queries so far: {self.query_count}"
                )

        self.perturbation = x_boundary - x_benign
        self.end_time = datetime.now()
        elapsed = (self.end_time - self.start_time).total_seconds()
        logger.info(
            f"HopSkipJump search complete! "
            f"Final ||delta||: {self.distance_history[-1]:.4f} | "
            f"Total queries: {self.query_count} | "
            f"Time: {elapsed:.2f}s"
        )
        return self.perturbation

    def apply_to_vehicle_state(self, vehicle_state: np.ndarray) -> np.ndarray:
        """Apply the found minimal perturbation to a vehicle state."""
        if self.perturbation is None:
            raise RuntimeError("Perturbation not computed yet. Call attack() first.")
        return vehicle_state + self.perturbation

    def get_statistics(self) -> Dict:
        """Get attack statistics and metadata."""
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "threat_model": self.threat_model.config.name,
            "perturbation_norm": float(np.linalg.norm(self.perturbation)) if self.perturbation is not None else None,
            "max_iterations": self.max_iterations,
            "initial_num_evals": self.initial_num_evals,
            "max_num_evals": self.max_num_evals,
            "gamma": self.gamma,
            "total_queries": self.query_count,
            "initial_distance": self.distance_history[0] if self.distance_history else None,
            "final_distance": self.distance_history[-1] if self.distance_history else None,
            "distance_history": self.distance_history,
            "elapsed_seconds": elapsed,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    def to_dict(self) -> Dict:
        """Serialize attack to dictionary."""
        return {
            **self.get_statistics(),
            "perturbation": self.perturbation.tolist() if self.perturbation is not None else None,
            "threat_model_config": {
                "name": self.threat_model.config.name,
                "model_type": self.threat_model.config.model_type,
                "input_dim": self.threat_model.config.input_dim,
                "output_dim": self.threat_model.config.output_dim,
                "description": self.threat_model.config.description,
            },
        }

    def save_to_file(self, filepath: Path) -> None:
        """Save attack metadata and perturbation to JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Attack saved to {filepath}")


# Convenience functions for SUMO integration

def create_hopskipjump_attack(max_iterations: int = 20) -> HopSkipJumpAttack:
    """Factory function to create attack with common parameters against the default safety model."""
    return HopSkipJumpAttack(
        max_iterations=max_iterations,
        threat_model=VehicleSafetyModel(),
    )


if __name__ == "__main__":
    # Demo: Simple standalone test against VehicleSafetyModel
    logging.basicConfig(level=logging.INFO)

    safety_model = VehicleSafetyModel()

    def decision_oracle(state: np.ndarray) -> int:
        # 1 = "unsafe" (attacker's goal), 0 = "safe" (benign) -- only this
        # binary label is ever revealed to the attack, never the confidence.
        confidence = safety_model.simple_linear_classifier(state)
        return 0 if confidence >= 0.5 else 1

    # A real, currently-"safe" vehicle state: [speed, accel, ttc, road_type, weather]
    # (moderate speed, large time-to-collision -- comfortably on the "safe" side)
    benign_state = np.array([5.0, 0.0, 10.0, 0.0, 0.0])
    assert decision_oracle(benign_state) == 0, "starting point must be 'safe' for this demo"

    attack = HopSkipJumpAttack(max_iterations=15)
    delta = attack.attack(benign_state, decision_oracle)

    print(f"\nMinimal adversarial perturbation: {delta}")
    print(f"Perturbation norm: {np.linalg.norm(delta):.4f}")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
