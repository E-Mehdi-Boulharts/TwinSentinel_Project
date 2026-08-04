"""
KnockoffNets Model Extraction Attack

Attack Model:
    1. Black-box functionality-stealing attack: the attacker queries a deployed
       vehicle ML model (safety classifier, trajectory predictor, decision
       model) through its normal input/output interface -- the same access a
       benign V2X client or MCP query would get -- and observes only its
       OUTPUT (a label, class probabilities, or a predicted trajectory),
       never its weights, gradients, or training data.
    2. Each observed (query, response) pair trains a local "knockoff"
       surrogate model (linear / logistic / softmax regression, fit by
       gradient descent) that gradually approximates the target's decision
       function.
    3. Two query strategies are supported:
           - "random": query points drawn from a broad, TARGET-INDEPENDENT
             distribution -- the attacker need not know or match the target's
             real training distribution, a defining property of KnockoffNets.
           - "adaptive": after every batch, spend the remaining query budget
             where the current surrogate is least confident -- margin
             (decision-boundary) sampling for classifiers, farthest-point
             diversity sampling for regressors. This is a practical,
             VANET-scale substitute for the original paper's RL-trained query
             policy (built for choosing among ImageNet classes), which does
             not apply to small, continuous, structured vehicle-state inputs.

Threat Model (VANET context):
    - Attacker has NO white-box access (no weights, no gradients, no training
      data) -- only the ability to submit inputs and read outputs, exactly the
      access level exposed by an onboard V2X ML service or a queryable MCP
      tool. Unlike hopskipjump_attack (label-only, used to craft a spoof) or
      universal_perturbation_attack (white-box, gradient-based), this attack
      never perturbs a real vehicle's state at all -- it steals the MODEL
      itself for offline reuse: cloning proprietary safety/trajectory logic,
      or pre-computing white-box adversarial examples offline against the
      stolen surrogate before deploying them against the real target.
    - This is a confidentiality/IP attack, not an integrity or availability
      attack: no vehicle ever brakes, swerves, or misbehaves during
      extraction.

Detection Difficulty:
    - No KPI signature at all during the attack itself -- SUMO/TraCI state is
      untouched, so anomaly detectors built on driving-behavior KPIs
      (position/speed/TTC drift) see nothing.
    - The only observable signal is the QUERY PATTERN: a high volume of
      synthetic, often out-of-distribution or systematically-varying inputs
      hitting the same model endpoint in a short window -- detectable only by
      rate-limiting / query-log analysis at the MCP interface, not by the
      KPI-based detectors this project's post_treatment pipeline evaluates.
      Adaptive-strategy queries cluster near the model's decision boundary,
      a further fingerprint distinguishing them from random background
      traffic.
"""

import numpy as np
import json
from typing import Dict, Callable, Optional
from datetime import datetime
from pathlib import Path
import logging

from .threat_models import VehicleThreatModel, VehicleSafetyModel, get_default_threat_model

logger = logging.getLogger(__name__)


class KnockoffNetsAttack:
    """
    Black-box, query-only model extraction attack for VANET simulation.

    Trains a local surrogate ("knockoff") that approximates the target
    threat model's input/output behavior, using only query access -- no
    weights, gradients, or training data are ever read from the target.
    """

    def __init__(
        self,
        threat_model: Optional[VehicleThreatModel] = None,
        query_budget: int = 500,
        strategy: str = "adaptive",
        batch_size: int = 25,
        pool_multiplier: int = 4,
        learning_rate: float = 0.1,
        train_epochs_per_round: int = 200,
        l2_reg: float = 1e-3,
        input_bounds: Optional[np.ndarray] = None,
        sampling_scale: float = 3.0,
        name: str = "knockoffnets",
    ):
        """
        Args:
            threat_model: Target model being queried (classification or regression)
            query_budget: Total number of oracle queries the attacker may spend
            strategy: "adaptive" (uncertainty/diversity-guided queries) or "random"
            batch_size: Queries issued per round before the surrogate is retrained
            pool_multiplier: Candidate pool size per round, as a multiple of batch_size
            learning_rate: Gradient descent step size for the surrogate
            train_epochs_per_round: Surrogate training epochs after each new batch
            l2_reg: L2 weight regularization for the surrogate
            input_bounds: Optional (input_dim, 2) array of [low, high] per dimension
                for query sampling; if None, samples from N(0, sampling_scale)
            sampling_scale: Std-dev used for query sampling when input_bounds is None
            name: Attack name for logging
        """
        self.threat_model = threat_model or get_default_threat_model()
        self.query_budget = query_budget
        self.strategy = strategy
        self.batch_size = batch_size
        self.pool_multiplier = pool_multiplier
        self.learning_rate = learning_rate
        self.train_epochs_per_round = train_epochs_per_round
        self.l2_reg = l2_reg
        self.input_bounds = np.asarray(input_bounds) if input_bounds is not None else None
        self.sampling_scale = sampling_scale
        self.name = name

        self.mode = self.threat_model.config.model_type  # "classification" or "regression"
        self.input_dim = self.threat_model.config.input_dim
        self.output_dim = self.threat_model.config.output_dim

        self.weights = np.zeros((self.input_dim, self.output_dim))
        self.bias = np.zeros(self.output_dim)
        self.x_mean = np.zeros(self.input_dim)
        self.x_std = np.ones(self.input_dim)

        self.queried_X = []
        self.queried_y = []
        self.query_count = 0
        self.fidelity_history = []
        self.final_fidelity = None
        self.start_time = None
        self.end_time = None

        logger.info("KnockoffNets initialized:")
        logger.info(f"  Strategy: {self.strategy}")
        logger.info(f"  Query budget: {self.query_budget}")
        logger.info(f"  Threat model: {self.threat_model.config.name} ({self.mode})")

    def _sample_candidates(self, n: int) -> np.ndarray:
        """Draw n candidate query points, independent of the target's real training distribution."""
        if self.input_bounds is not None:
            low, high = self.input_bounds[:, 0], self.input_bounds[:, 1]
            return np.random.uniform(low, high, size=(n, self.input_dim))
        return np.random.randn(n, self.input_dim) * self.sampling_scale

    def _forward(self, X: np.ndarray) -> np.ndarray:
        """Surrogate forward pass: linear (regression), sigmoid (binary), or softmax (multi-class)."""
        X_std = (X - self.x_mean) / self.x_std
        logits = X_std @ self.weights + self.bias
        if self.mode == "regression":
            return logits
        if self.output_dim == 1:
            return 1.0 / (1.0 + np.exp(-logits))
        shifted = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp / exp.sum(axis=1, keepdims=True)

    def _format_target(self, raw) -> np.ndarray:
        """Normalize one oracle response into the surrogate's target shape (output_dim,)."""
        if self.mode == "regression":
            return np.atleast_1d(np.asarray(raw, dtype=np.float64))
        if self.output_dim == 1:
            return np.array([float(raw)])
        raw_arr = np.asarray(raw)
        if raw_arr.ndim == 0:
            onehot = np.zeros(self.output_dim)
            onehot[int(raw_arr)] = 1.0
            return onehot
        return raw_arr.astype(np.float64)

    def _train_surrogate(self, X: np.ndarray, Y: np.ndarray) -> None:
        """Full-batch gradient descent on all queries collected so far (warm-started each round)."""
        # Re-fit input normalization to the growing query set each round -- without
        # this, raw feature scales (e.g. VANET x/y positions up to 100+) blow up a
        # fixed learning rate and the surrogate diverges to NaN/inf.
        self.x_mean = X.mean(axis=0)
        self.x_std = X.std(axis=0) + 1e-8
        X_std = (X - self.x_mean) / self.x_std

        n = X.shape[0]
        for _ in range(self.train_epochs_per_round):
            preds = self._forward(X)
            error = preds - Y
            grad_w = X_std.T @ error / n + self.l2_reg * self.weights
            grad_b = error.mean(axis=0)
            self.weights -= self.learning_rate * grad_w
            self.bias -= self.learning_rate * grad_b

    def _select_batch_uncertainty(self, pool: np.ndarray, batch_size: int) -> np.ndarray:
        """Classification: pick candidates closest to the surrogate's current decision boundary."""
        probs = self._forward(pool)
        if self.output_dim == 1:
            margin = np.abs(probs[:, 0] - 0.5)
        else:
            sorted_probs = np.sort(probs, axis=1)
            margin = sorted_probs[:, -1] - sorted_probs[:, -2]
        idx = np.argsort(margin)[:batch_size]
        return pool[idx]

    def _select_batch_diversity(self, pool: np.ndarray, batch_size: int) -> np.ndarray:
        """Regression: greedy farthest-point sampling, maximizing distance from already-queried points."""
        if len(self.queried_X) > 0:
            ref = np.array(self.queried_X)
            min_dist = np.linalg.norm(pool[:, None, :] - ref[None, :, :], axis=2).min(axis=1)
        else:
            min_dist = np.linalg.norm(pool, axis=1)

        remaining_mask = np.ones(len(pool), dtype=bool)
        selected_idx = []
        for _ in range(min(batch_size, len(pool))):
            idx = int(np.argmax(np.where(remaining_mask, min_dist, -np.inf)))
            selected_idx.append(idx)
            remaining_mask[idx] = False
            new_dist = np.linalg.norm(pool - pool[idx], axis=1)
            min_dist = np.minimum(min_dist, new_dist)
        return pool[selected_idx]

    def extract(
        self,
        target_query_func: Callable[[np.ndarray], object],
        x_eval: Optional[np.ndarray] = None,
        target_eval_func: Optional[Callable[[np.ndarray], object]] = None,
    ) -> "KnockoffNetsAttack":
        """
        Steal the target's functionality within the query budget.

        Args:
            target_query_func: Oracle -- target_query_func(x) -> label, probability
                (vector), or regression output. Never exposes weights/gradients.
            x_eval: Optional held-out states to track fidelity progress during extraction
            target_eval_func: Oracle used against x_eval (usually the same as
                target_query_func); required to populate fidelity_history

        Returns:
            self, with a trained surrogate available via predict()
        """
        self.start_time = datetime.now()
        self.query_count = 0
        self.queried_X = []
        self.queried_y = []
        self.fidelity_history = []
        round_num = 0

        while self.query_count < self.query_budget:
            current_batch_size = min(self.batch_size, self.query_budget - self.query_count)
            pool_size = max(current_batch_size * self.pool_multiplier, current_batch_size)
            pool = self._sample_candidates(pool_size)

            if self.strategy == "adaptive" and len(self.queried_X) > 0:
                if self.mode == "classification":
                    batch = self._select_batch_uncertainty(pool, current_batch_size)
                else:
                    batch = self._select_batch_diversity(pool, current_batch_size)
            else:
                batch = pool[:current_batch_size]

            for x in batch:
                raw = target_query_func(x)
                self.queried_X.append(x)
                self.queried_y.append(self._format_target(raw))
                self.query_count += 1

            self._train_surrogate(np.array(self.queried_X), np.array(self.queried_y))
            round_num += 1

            if x_eval is not None and target_eval_func is not None:
                fidelity = self.evaluate_fidelity(x_eval, target_eval_func)
                self.fidelity_history.append({"queries": self.query_count, "fidelity": fidelity})
                logger.info(
                    f"Round {round_num} | queries: {self.query_count}/{self.query_budget} | "
                    f"fidelity: {fidelity:.3f}"
                )

        self.end_time = datetime.now()
        elapsed = (self.end_time - self.start_time).total_seconds()
        logger.info(
            f"KnockoffNets extraction complete! "
            f"Total queries: {self.query_count} | "
            f"Final fidelity: {self.final_fidelity if self.final_fidelity is not None else 'n/a'} | "
            f"Time: {elapsed:.2f}s"
        )
        return self

    def evaluate_fidelity(self, x_eval: np.ndarray, target_query_func: Callable[[np.ndarray], object]) -> float:
        """
        How faithfully the surrogate reproduces the TARGET's decisions (not ground truth):
        agreement rate for classification, 1 - normalized MSE for regression.
        """
        x_eval = np.asarray(x_eval, dtype=np.float64)
        surrogate_preds = self._forward(x_eval)

        if self.mode == "regression":
            target_vals = np.array([np.atleast_1d(target_query_func(x)) for x in x_eval])
            mse = np.mean((surrogate_preds - target_vals) ** 2)
            var = np.var(target_vals) + 1e-12
            fidelity = float(max(0.0, 1.0 - mse / var))
        else:
            target_raw = [target_query_func(x) for x in x_eval]
            if self.output_dim == 1:
                target_labels = np.array([1 if float(r) >= 0.5 else 0 for r in target_raw])
                surrogate_labels = (surrogate_preds[:, 0] >= 0.5).astype(int)
            else:
                target_labels = np.array(
                    [int(np.argmax(r)) if np.ndim(r) > 0 else int(r) for r in target_raw]
                )
                surrogate_labels = np.argmax(surrogate_preds, axis=1)
            fidelity = float(np.mean(surrogate_labels == target_labels))

        self.final_fidelity = fidelity
        return fidelity

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Query the STOLEN surrogate directly -- no further real queries needed."""
        x = np.atleast_2d(np.asarray(x, dtype=np.float64))
        return self._forward(x)

    def get_statistics(self) -> Dict:
        """Get attack statistics and metadata."""
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "threat_model": self.threat_model.config.name,
            "strategy": self.strategy,
            "query_budget": self.query_budget,
            "total_queries": self.query_count,
            "batch_size": self.batch_size,
            "train_epochs_per_round": self.train_epochs_per_round,
            "fidelity_history": self.fidelity_history,
            "final_fidelity": self.final_fidelity,
            "elapsed_seconds": elapsed,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    def to_dict(self) -> Dict:
        """Serialize attack to dictionary."""
        return {
            **self.get_statistics(),
            "surrogate_weights": self.weights.tolist(),
            "surrogate_bias": self.bias.tolist(),
            "input_mean": self.x_mean.tolist(),
            "input_std": self.x_std.tolist(),
            "threat_model_config": {
                "name": self.threat_model.config.name,
                "model_type": self.threat_model.config.model_type,
                "input_dim": self.threat_model.config.input_dim,
                "output_dim": self.threat_model.config.output_dim,
                "description": self.threat_model.config.description,
            },
        }

    def save_to_file(self, filepath: Path) -> None:
        """Save attack metadata and stolen surrogate to JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Attack saved to {filepath}")


# Convenience functions for SUMO integration

def create_knockoffnets_attack(query_budget: int = 500, strategy: str = "adaptive") -> KnockoffNetsAttack:
    """Factory function to create attack with common parameters against the default safety model."""
    return KnockoffNetsAttack(
        query_budget=query_budget,
        strategy=strategy,
        threat_model=VehicleSafetyModel(),
    )


if __name__ == "__main__":
    # Demo: steal VehicleSafetyModel's decision function using only its probability output.
    logging.basicConfig(level=logging.INFO)

    safety_model = VehicleSafetyModel()
    true_weights = np.array([-0.5, 0.2, 1.0, -0.1, 0.3])  # ground truth, mirrors threat_models.py

    def target_query_func(state: np.ndarray) -> float:
        # Reveals only the probability output -- exactly what a queryable V2X
        # safety endpoint would expose, never the weights above.
        return safety_model.simple_linear_classifier(state)

    # [speed, acceleration, ttc, road_type, weather]
    bounds = np.array([
        [0.0, 30.0],
        [-5.0, 5.0],
        [0.0, 20.0],
        [0.0, 3.0],
        [0.0, 3.0],
    ])

    attack = KnockoffNetsAttack(
        threat_model=safety_model,
        query_budget=400,
        strategy="adaptive",
        input_bounds=bounds,
    )

    x_eval = attack._sample_candidates(200)
    attack.extract(target_query_func, x_eval=x_eval, target_eval_func=target_query_func)

    print(f"\nStolen surrogate weights: {attack.weights.ravel()}")
    print(f"True target weights:      {true_weights}")
    print(f"Final fidelity (agreement with target): {attack.final_fidelity:.3f}")
    print(f"Total queries used: {attack.query_count} / {attack.query_budget}")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
