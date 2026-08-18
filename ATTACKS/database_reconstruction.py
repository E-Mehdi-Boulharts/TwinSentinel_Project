"""
Database Reconstruction Attack

Attack Model:
    1. White-box-adjacent PRIVACY/confidentiality attack: unlike the three purely
       black-box attacks in this project (attribute_inference_black_box,
       membership_inference_black_box, miface), the attacker here is an "informed
       adversary" (the standard assumption for this attack family, going back to
       Dinur & Nissim's 2003 differencing attack) who already knows ALL BUT ONE row
       of a target model's training set -- e.g. a leaked or partially-shared
       fleet-telemetry dataset -- plus the model's architecture/training algorithm,
       and wants to reconstruct the one row they are missing.
    2. ART's own wiki entry for this attack (Trusted-AI/adversarial-robustness-toolbox
       wiki, "ART Attacks", section 4.4 Reconstruction) cites no academic paper -- it
       describes it simply as inferring "the missing row of a training dataset for
       [a] trained model." This implementation follows that same differencing
       methodology: retrain a model of the SAME known architecture on
       (known_rows + candidate_row), and search for whichever candidate_row makes the
       retrained model's predictions on the known rows most closely match the REAL
       target's real predictions on those same known rows -- only ever queried, never
       read from weights.
    3. Because retraining is not directly differentiable end-to-end, the search over
       candidate rows reuses VehicleThreatModel.compute_gradient_blackbox (finite
       differences), first introduced by miface.py: for each candidate row x, "loss"
       is the L2 distance between predict_proba(known_rows) of a model freshly
       retrained on (known_rows, x) and the real target's predict_proba on those same
       rows, minimized by gradient descent exactly like MIFace's gradient ascent on
       confidence.
    4. The true label of the missing row is also unknown, so it is estimated by
       brute-forcing both binary label values (0/1) and keeping whichever converges
       to the lower final distance -- the same discrete/continuous split ART's actual
       implementation uses.

Threat Model (VANET context):
    - Attacker has knowledge of all-but-one row of a target model's real training set
      (e.g. an insider, a partial data leak, or a shared fleet-telemetry dataset with
      one withheld record) plus the target's model class/hyperparameters, and ONLY
      query access to the real target's output confidence on the known rows -- never
      its weights or gradients. This is a stronger assumption than the other three
      black-box attacks here (which need no knowledge of the training set at all), but
      a strictly weaker one than white-box access to the target's parameters.
    - Confidentiality/privacy attack, not integrity or availability: no vehicle state,
      trajectory, or SUMO/TraCI simulation is ever touched -- reconstruction runs
      entirely offline against retrained candidate models.

Detection Difficulty:
    - No KPI signature -- like the other three privacy attacks here, it never
      perturbs simulation state, so KPI-based anomaly detectors in this project's
      post_treatment pipeline see nothing.
    - Observable only as the handful of queries issued against the real target's
      output on the known rows -- a small, fixed-size query burst rather than the
      sustained probing pattern of the other black-box attacks (all retraining work
      happens entirely offline against the attacker's own candidate models) -- not
      something this project's KPI-based detectors evaluate.
"""

import numpy as np
import json
from typing import Dict, List, Callable, Optional, Sequence, Tuple
from datetime import datetime
from pathlib import Path
import logging

from .threat_models import VehicleThreatModel, ThreatModelConfig

logger = logging.getLogger(__name__)


class DatabaseTargetModel:
    """
    Small, hand-rolled logistic regression standing in for a REAL trained onboard
    vehicle safety model -- unlike this project's other VehicleSafetyModel (a fixed
    formula nothing was ever trained on), this one has an actual training process, so
    there is something for a "retrain on known_rows + candidate_row" differencing
    search to reproduce. Kept linear (no hidden layer) rather than mirroring
    MembershipInferenceTargetModel's MLP so repeated retraining inside the
    reconstruction search loop stays cheap.
    """

    def __init__(
        self,
        input_dim: int = 5,
        learning_rate: float = 0.5,
        train_epochs: int = 150,
        l2_reg: float = 1e-3,
        seed: Optional[int] = None,
    ):
        self.input_dim = input_dim
        self.learning_rate = learning_rate
        self.train_epochs = train_epochs
        self.l2_reg = l2_reg

        rng = np.random.default_rng(seed)
        self.weights = rng.normal(0.0, 0.1, size=input_dim)
        self.bias = 0.0
        self.x_mean = np.zeros(input_dim)
        self.x_std = np.ones(input_dim)
        self.fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "DatabaseTargetModel":
        """Fit via hand-rolled full-batch gradient descent -- this IS the training
        process a differencing attack exploits; unlike threat_models.py's fixed
        formulas, real (state, label) rows go into it."""
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).reshape(-1)

        self.x_mean = X.mean(axis=0)
        self.x_std = X.std(axis=0) + 1e-8
        X_std = (X - self.x_mean) / self.x_std
        n = X.shape[0]

        for _ in range(self.train_epochs):
            logits = X_std @ self.weights + self.bias
            p = 1.0 / (1.0 + np.exp(-logits))
            error = (p - y) / n
            grad_w = X_std.T @ error + self.l2_reg * self.weights
            grad_b = error.sum()
            self.weights -= self.learning_rate * grad_w
            self.bias -= self.learning_rate * grad_b

        self.fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Query interface exposed to the attack -- returns P(safe=1), never weights."""
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        X_std = (X - self.x_mean) / self.x_std
        logits = X_std @ self.weights + self.bias
        return 1.0 / (1.0 + np.exp(-logits))


class DatabaseReconstructionAttack:
    """
    Differencing-based database reconstruction attack for VANET simulation.

    Given all-but-one row of a target model's training set plus query access to the
    target's real predictions on those known rows, reconstructs the missing row by
    searching for whichever candidate row -- once added to the known rows and used to
    retrain a fresh model of the same known architecture -- reproduces the target's
    real predictions most closely.
    """

    def __init__(
        self,
        target_factory: Optional[Callable[[], DatabaseTargetModel]] = None,
        input_bounds: Optional[np.ndarray] = None,
        max_iterations: int = 80,
        learning_rate: float = 0.4,
        l2_reg: float = 0.05,
        finite_diff_delta: float = 0.05,
        finite_diff_batch: int = 6,
        window_length: int = 15,
        threshold: float = 1e-4,
        candidate_labels: Sequence[float] = (0.0, 1.0),
        name: str = "database_reconstruction",
    ):
        """
        Args:
            target_factory: Zero-arg callable returning a fresh, untrained model
                matching the real target's architecture -- the standard "informed
                adversary knows the model class/hyperparameters" assumption for this
                attack family (default: DatabaseTargetModel with default hyperparams)
            input_bounds: (input_dim, 2) array of [low, high] plausibility bounds per
                feature, used to clip every step (default: VANET feature bounds
                matching miface.py's [speed, accel, ttc, road_type, weather])
            max_iterations: Maximum gradient-descent steps per candidate label
            learning_rate: Step size applied to the estimated distance gradient
            l2_reg: Strength of the L2 pull back toward the known rows' mean each
                step -- the realism regularizer mirroring miface.py's anchor term
            finite_diff_delta: Perturbation magnitude for gradient estimation
            finite_diff_batch: Random directions averaged per gradient estimate,
                each requiring 2 full model retrains (passed through to
                VehicleThreatModel.compute_gradient_blackbox)
            window_length: Early-stopping window -- iterations examined for progress
            threshold: Early-stopping threshold -- minimum distance improvement over
                window_length iterations to keep going
            candidate_labels: Binary label values brute-forced for the missing row,
                since the search over continuous features alone can't recover it
            name: Attack name for logging
        """
        self.target_factory = target_factory or (lambda: DatabaseTargetModel())
        self.input_bounds = (
            np.asarray(input_bounds, dtype=np.float64)
            if input_bounds is not None
            else np.array([[0.0, 30.0], [-5.0, 5.0], [0.0, 20.0], [0.0, 3.0], [0.0, 3.0]])
        )
        self.max_iterations = max_iterations
        self.learning_rate = learning_rate
        self.l2_reg = l2_reg
        self.finite_diff_delta = finite_diff_delta
        self.finite_diff_batch = finite_diff_batch
        self.window_length = window_length
        self.threshold = threshold
        self.candidate_labels = list(candidate_labels)
        self.name = name

        self.threat_model = VehicleThreatModel(
            ThreatModelConfig(
                name="database_reconstruction_retrain",
                model_type="regression",
                input_dim=self.input_bounds.shape[0],
                output_dim=1,
                description="Retrain-and-compare objective for database reconstruction",
            )
        )
        self.label_results: Dict[float, Dict] = {}
        self.best_result: Optional[Dict] = None
        self.start_time = None
        self.end_time = None

        logger.info("DatabaseReconstruction initialized:")
        logger.info(f"  Max iterations per label: {self.max_iterations}")
        logger.info(f"  Candidate labels: {self.candidate_labels}")

    def _reconstruct_for_label(
        self,
        known_X: np.ndarray,
        known_y: np.ndarray,
        target_output_known: np.ndarray,
        candidate_label: float,
        anchor: np.ndarray,
        initial_state: Optional[np.ndarray],
    ) -> Dict:
        """Search over candidate feature rows for a single fixed candidate label,
        minimizing the L2 distance between a retrained model's predictions on the
        known rows and the real target's real predictions on those same rows."""

        def neg_distance(x: np.ndarray) -> float:
            candidate_model = self.target_factory()
            X_aug = np.vstack([known_X, x.reshape(1, -1)])
            y_aug = np.concatenate([known_y, [candidate_label]])
            candidate_model.fit(X_aug, y_aug)
            retrained_output = candidate_model.predict_proba(known_X)
            distance = float(np.sum((retrained_output - target_output_known) ** 2))
            return -distance

        x = np.array(initial_state, dtype=np.float64) if initial_state is not None else anchor.copy()
        loss_history: List[float] = []
        best_x, best_neg_dist = x.copy(), neg_distance(x)
        iterations_since_improvement = 0

        for iteration in range(self.max_iterations):
            grad = self.threat_model.compute_gradient_blackbox(
                neg_distance, x, delta=self.finite_diff_delta, batch_size=self.finite_diff_batch
            )
            grad_norm = np.linalg.norm(grad)
            direction = grad / grad_norm if grad_norm > 1e-12 else grad
            x = x + self.learning_rate * direction
            x = x - self.l2_reg * (x - anchor)
            x = np.clip(x, self.input_bounds[:, 0], self.input_bounds[:, 1])

            neg_dist = neg_distance(x)
            distance = -neg_dist
            loss_history.append(distance)
            self.threat_model.log_gradient_query(x.shape, float(np.linalg.norm(grad)), distance)

            if neg_dist > best_neg_dist + self.threshold:
                best_neg_dist, best_x = neg_dist, x.copy()
                iterations_since_improvement = 0
            else:
                iterations_since_improvement += 1
                if iterations_since_improvement >= self.window_length:
                    break

        return {
            "candidate_label": candidate_label,
            "reconstructed_features": best_x.tolist(),
            "achieved_distance": float(-best_neg_dist),
            "iterations_run": len(loss_history),
            "loss_history": loss_history,
        }

    def reconstruct(
        self,
        target: DatabaseTargetModel,
        known_X: np.ndarray,
        known_y: np.ndarray,
        initial_state: Optional[np.ndarray] = None,
    ) -> Dict:
        """
        Reconstruct the single row missing from known_X/known_y.

        Args:
            target: The REAL target -- queried only for predict_proba(known_X),
                never for its weights
            known_X: All-but-one of the target's real training rows (the attacker's
                informed-adversary knowledge)
            known_y: Labels for known_X
            initial_state: Optional starting guess for the missing row's features
                (default: mean of known_X)

        Returns:
            Dict with the winning reconstruction (lowest achieved_distance across
            candidate_labels) plus every label's individual search result.
        """
        self.start_time = datetime.now()

        known_X = np.asarray(known_X, dtype=np.float64)
        known_y = np.asarray(known_y, dtype=np.float64)
        target_output_known = target.predict_proba(known_X)
        anchor = known_X.mean(axis=0)

        self.label_results = {}
        for candidate_label in self.candidate_labels:
            result = self._reconstruct_for_label(
                known_X, known_y, target_output_known, candidate_label, anchor, initial_state
            )
            self.label_results[candidate_label] = result

        self.best_result = min(self.label_results.values(), key=lambda r: r["achieved_distance"])
        self.end_time = datetime.now()

        elapsed = (self.end_time - self.start_time).total_seconds()
        logger.info(
            f"DatabaseReconstruction complete! winning_label={self.best_result['candidate_label']} "
            f"achieved_distance={self.best_result['achieved_distance']:.6f} "
            f"retrain_queries={self.threat_model.gradient_queries} | Time: {elapsed:.2f}s"
        )
        return self.best_result

    def get_statistics(self) -> Dict:
        """Get attack statistics and metadata."""
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "max_iterations": self.max_iterations,
            "candidate_labels": self.candidate_labels,
            "retrain_queries": self.threat_model.gradient_queries,
            "best_result": self.best_result,
            "label_results": self.label_results,
            "elapsed_seconds": elapsed,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    def to_dict(self) -> Dict:
        """Serialize attack to dictionary."""
        return {
            **self.get_statistics(),
            "input_bounds": self.input_bounds.tolist(),
        }

    def save_to_file(self, filepath: Path) -> None:
        """Save attack metadata and reconstruction results to JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Attack saved to {filepath}")


# Convenience functions for SUMO integration

def create_database_reconstruction_attack(max_iterations: int = 80, learning_rate: float = 0.4) -> DatabaseReconstructionAttack:
    """Factory function to create attack with common parameters against a default logistic-regression target."""
    return DatabaseReconstructionAttack(
        max_iterations=max_iterations,
        learning_rate=learning_rate,
    )


if __name__ == "__main__":
    # Demo: recover the ONE vehicle telemetry record withheld from a known training
    # set, using only query access to a small model's confidence output on the known
    # rows -- never its weights, and never the missing row itself.
    logging.basicConfig(level=logging.INFO)

    def sample_vanet_data(n: int, label_noise: float = 0.1, seed: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(seed)
        speed = rng.uniform(0.0, 30.0, n)
        accel = rng.uniform(-5.0, 5.0, n)
        ttc = rng.uniform(0.0, 20.0, n)
        road_type = rng.choice([0.0, 1.0, 2.0, 3.0], size=n)
        weather = rng.choice([0.0, 1.0, 2.0], size=n)
        X = np.stack([speed, accel, ttc, road_type, weather], axis=1)

        logits = -0.5 * speed + 0.2 * accel + 1.0 * ttc - 0.1 * road_type + 0.3 * weather
        p = 1.0 / (1.0 + np.exp(-logits))
        y = (p > 0.5).astype(np.float64)
        flip = rng.random(n) < label_noise
        y[flip] = 1.0 - y[flip]
        return X, y

    # The REAL target: trained on a full dataset the attacker knows all but one row of.
    X_full, y_full = sample_vanet_data(40, seed=7)
    missing_idx = 0
    missing_row, missing_label = X_full[missing_idx].copy(), y_full[missing_idx]
    known_X = np.delete(X_full, missing_idx, axis=0)
    known_y = np.delete(y_full, missing_idx, axis=0)

    target = DatabaseTargetModel()
    target.fit(X_full, y_full)

    attack = DatabaseReconstructionAttack()
    result = attack.reconstruct(target, known_X, known_y)

    feature_names = ["speed", "acceleration", "ttc", "road_type", "weather"]
    reconstructed = dict(zip(feature_names, result["reconstructed_features"]))
    true_row = dict(zip(feature_names, missing_row.tolist()))

    print(f"\nTrue missing row (label={missing_label:.0f}):")
    for k, v in true_row.items():
        print(f"   {k}: {v:.2f}")

    print(f"\nReconstructed row (label={result['candidate_label']:.0f}, distance={result['achieved_distance']:.6f}, "
          f"iterations={result['iterations_run']}):")
    for k, v in reconstructed.items():
        print(f"   {k}: {v:.2f}")

    feature_error = float(np.linalg.norm(np.array(result["reconstructed_features"]) - missing_row))
    print(f"\nFeature L2 error vs. true missing row: {feature_error:.3f}")
    print(f"Label recovered correctly: {result['candidate_label'] == missing_label}")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
