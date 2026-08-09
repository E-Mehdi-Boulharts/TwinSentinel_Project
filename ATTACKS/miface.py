"""
MIFace Model Inversion Attack (Fredrikson et al., 2015)

Attack Model:
    1. Black-box PRIVACY/confidentiality attack: the attacker has ONLY query access to
       a deployed vehicle safety model's confidence output -- the same access level as
       knockoff_nets_extraction_attack, attribute_inference_black_box_attack, and
       membership_inference_black_box_attack -- and reconstructs a REPRESENTATIVE input
       for a target class (e.g. "what does a typical SAFE vehicle state look like to
       this model?") by hill-climbing on the model's own confidence score.
    2. Gradients are estimated purely from confidence queries via finite differences
       (VehicleThreatModel.compute_gradient_blackbox, defined in threat_models.py but
       never previously used by any attack in this project) -- no weights, no
       analytic gradients, no training data are ever read. This is a genuinely
       black-box variant of the original paper's gradient-based reconstruction.
    3. Starting from a neutral initial guess (the midpoint of plausible feature
       bounds), MIFace iteratively nudges the candidate state in the direction that
       increases the target class's confidence, re-clipping to plausible bounds and
       pulling gently toward the population mean (an L2 anchor) each step -- mirroring
       the original paper's realism/regularization term. Without it, gradient ascent
       on a linear model's confidence simply diverges toward infinity rather than
       converging on anything resembling a real vehicle state.
    4. Unlike membership_inference_black_box_attack (which asks "was THIS specific
       record in the training set?"), MIFace reconstructs a CLASS-REPRESENTATIVE
       record from scratch -- closer to "what does the model believe a typical member
       of this class looks like." That can leak the statistical profile of a model's
       real training population as a whole (e.g. that "safe" vehicles in the training
       data cluster around specific speed/TTC combinations), rather than any one
       individual's record.

Threat Model (VANET context):
    - Same black-box query-only access level as the other three confidentiality
      attacks in this project: only the target's own confidence output is ever read,
      via repeated small perturbations (finite-difference probes) -- never weights or
      gradients.
    - Confidentiality/privacy attack, not integrity or availability: no vehicle state,
      trajectory, or SUMO/TraCI simulation is ever touched -- the reconstructed state
      is a synthetic artifact the attacker builds entirely offline.

Detection Difficulty:
    - No KPI signature -- like the other three black-box confidentiality attacks here,
      it never perturbs simulation state, so KPI-based anomaly detectors in this
      project's post_treatment pipeline see nothing.
    - Observable only as query VOLUME and PATTERN: MIFace's finite-difference probes
      cluster tightly around a slowly-drifting point in input space (unlike
      knockoff_nets_extraction_attack's broad random sampling), a distinguishable
      fingerprint for query-log analysis at the MCP interface -- not something this
      project's KPI-based detectors evaluate.
"""

import numpy as np
import json
from typing import Dict, List, Callable, Optional
from datetime import datetime
from pathlib import Path
import logging

from .threat_models import VehicleThreatModel, VehicleSafetyModel

logger = logging.getLogger(__name__)


class MIFaceAttack:
    """
    Black-box MIFace model inversion attack for VANET simulation.

    Reconstructs a class-representative input state for a target threat model by
    hill-climbing on its confidence output, using finite-difference gradient
    estimation -- never the target's weights or analytic gradients.
    """

    def __init__(
        self,
        threat_model: Optional[VehicleThreatModel] = None,
        max_iterations: int = 300,
        learning_rate: float = 0.5,
        input_bounds: Optional[np.ndarray] = None,
        l2_reg: float = 0.05,
        finite_diff_delta: float = 0.01,
        finite_diff_batch: int = 20,
        window_length: int = 25,
        threshold: float = 1e-4,
        name: str = "miface",
    ):
        """
        Args:
            threat_model: Target model whose confidence output is inverted
                (default: VehicleSafetyModel)
            max_iterations: Maximum gradient-ascent steps per class reconstruction
            learning_rate: Step size applied to the estimated confidence gradient
            input_bounds: (input_dim, 2) array of [low, high] plausibility bounds per
                feature, used both to seed the initial guess (their midpoint) and to
                clip every step (default: VehicleSafetyModel's own bounds)
            l2_reg: Strength of the L2 pull back toward the bounds' midpoint each
                step -- the realism/regularization term without which gradient ascent
                on a linear model's confidence diverges rather than converging
            finite_diff_delta: Perturbation magnitude for gradient estimation
            finite_diff_batch: Random directions averaged per gradient estimate
                (passed through to VehicleThreatModel.compute_gradient_blackbox)
            window_length: Early-stopping window -- iterations examined for progress
            threshold: Early-stopping threshold -- minimum loss improvement over
                window_length iterations to keep going
            name: Attack name for logging
        """
        self.threat_model = threat_model or VehicleSafetyModel()
        self.max_iterations = max_iterations
        self.learning_rate = learning_rate
        self.input_bounds = (
            np.asarray(input_bounds, dtype=np.float64)
            if input_bounds is not None
            else np.array([[0.0, 30.0], [-5.0, 5.0], [0.0, 20.0], [0.0, 3.0], [0.0, 3.0]])
        )
        self.l2_reg = l2_reg
        self.finite_diff_delta = finite_diff_delta
        self.finite_diff_batch = finite_diff_batch
        self.window_length = window_length
        self.threshold = threshold
        self.name = name

        self.anchor = (self.input_bounds[:, 0] + self.input_bounds[:, 1]) / 2.0
        self.reconstructions: Dict[int, Dict] = {}
        self.start_time = None
        self.end_time = None

        logger.info("MIFace initialized:")
        logger.info(f"  Threat model: {self.threat_model.config.name}")
        logger.info(f"  Max iterations: {self.max_iterations}")
        logger.info(f"  Anchor (bounds midpoint): {self.anchor}")

    def invert_class(
        self,
        target_query_func: Callable[[np.ndarray], object],
        target_class: int,
        initial_state: Optional[np.ndarray] = None,
    ) -> Dict:
        """
        Reconstruct a representative input for a single target class.

        Args:
            target_query_func: Oracle -- target_query_func(x) -> P(class=1), the same
                black-box query interface used by the other confidentiality attacks in
                this project.
            target_class: 1 to reconstruct a typical member of the positive class
                (maximize confidence), 0 for the negative class (minimize confidence,
                i.e. maximize 1 - confidence)
            initial_state: Optional starting point (default: bounds midpoint)

        Returns:
            Dict with the reconstructed state, achieved confidence, iterations run,
            and the loss trajectory.
        """

        def signed_confidence(x: np.ndarray) -> float:
            c = float(target_query_func(x))
            return c if target_class == 1 else (1.0 - c)

        x = np.array(initial_state, dtype=np.float64) if initial_state is not None else self.anchor.copy()
        loss_history: List[float] = []
        best_x, best_conf = x.copy(), signed_confidence(x)
        iterations_since_improvement = 0

        for iteration in range(self.max_iterations):
            grad = self.threat_model.compute_gradient_blackbox(
                signed_confidence, x, delta=self.finite_diff_delta, batch_size=self.finite_diff_batch
            )
            # Normalize direction before scaling by learning_rate (PGD/FGSM-style step)
            # rather than stepping by the raw gradient: sigmoid'(z) = p*(1-p) vanishes
            # as confidence saturates toward 0 or 1, so a raw-gradient step shrinks to
            # near-zero exactly when there's still real distance left to the target
            # class's confidence region -- a normalized step keeps per-iteration
            # progress constant regardless of how saturated the current confidence is.
            grad_norm = np.linalg.norm(grad)
            direction = grad / grad_norm if grad_norm > 1e-12 else grad
            x = x + self.learning_rate * direction
            # Anchor pull-back is applied as its OWN proportional shrinkage step, not
            # summed into the gradient before normalizing: the raw confidence gradient
            # is often tiny (saturated sigmoid) relative to a fixed-size normalized
            # step, so mixing the two before normalizing lets the anchor term dominate
            # the resulting direction the moment x drifts even slightly -- yanking the
            # point straight back and stalling progress in a tight orbit around anchor.
            x = x - self.l2_reg * (x - self.anchor)
            x = np.clip(x, self.input_bounds[:, 0], self.input_bounds[:, 1])

            conf = signed_confidence(x)
            loss = 1.0 - conf
            loss_history.append(loss)
            self.threat_model.log_gradient_query(x.shape, float(np.linalg.norm(grad)), loss)

            # Patience-based early stopping on the BEST confidence found so far, not on
            # raw step-to-step loss deltas: normalized steps can overshoot and oscillate
            # near a maximum, so a fragile two-point window comparison quits the moment
            # any single noisy step looks flat -- long before best_conf has actually
            # stopped improving.
            if conf > best_conf + self.threshold:
                best_conf, best_x = conf, x.copy()
                iterations_since_improvement = 0
            else:
                iterations_since_improvement += 1
                if iterations_since_improvement >= self.window_length:
                    break

        result = {
            "target_class": target_class,
            "reconstructed_state": best_x.tolist(),
            "achieved_confidence": float(best_conf),
            "iterations_run": len(loss_history),
            "loss_history": loss_history,
        }
        self.reconstructions[target_class] = result
        return result

    def invert_all_classes(
        self,
        target_query_func: Callable[[np.ndarray], object],
        classes: Optional[List[int]] = None,
        initial_state: Optional[np.ndarray] = None,
    ) -> "MIFaceAttack":
        """Reconstruct a representative input for every class in `classes` (default: [0, 1])."""
        self.start_time = datetime.now()
        for target_class in (classes or [0, 1]):
            self.invert_class(target_query_func, target_class, initial_state=initial_state)
        self.end_time = datetime.now()

        elapsed = (self.end_time - self.start_time).total_seconds()
        logger.info(
            f"MIFace inversion complete! classes={list(self.reconstructions.keys())} "
            f"gradient_queries={self.threat_model.gradient_queries} | Time: {elapsed:.2f}s"
        )
        return self

    def get_statistics(self) -> Dict:
        """Get attack statistics and metadata."""
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "threat_model": self.threat_model.config.name,
            "max_iterations": self.max_iterations,
            "gradient_queries": self.threat_model.gradient_queries,
            "reconstructions": self.reconstructions,
            "elapsed_seconds": elapsed,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    def to_dict(self) -> Dict:
        """Serialize attack to dictionary."""
        return {
            **self.get_statistics(),
            "input_bounds": self.input_bounds.tolist(),
            "anchor": self.anchor.tolist(),
            "threat_model_config": {
                "name": self.threat_model.config.name,
                "model_type": self.threat_model.config.model_type,
                "input_dim": self.threat_model.config.input_dim,
                "output_dim": self.threat_model.config.output_dim,
                "description": self.threat_model.config.description,
            },
        }

    def save_to_file(self, filepath: Path) -> None:
        """Save attack metadata and reconstructions to JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Attack saved to {filepath}")


# Convenience functions for SUMO integration

def create_miface_attack(max_iterations: int = 300, learning_rate: float = 0.5) -> MIFaceAttack:
    """Factory function to create attack with common parameters against the default safety model."""
    return MIFaceAttack(
        threat_model=VehicleSafetyModel(),
        max_iterations=max_iterations,
        learning_rate=learning_rate,
    )


if __name__ == "__main__":
    # Demo: reconstruct what a "typical safe" and "typical unsafe" vehicle state look
    # like to VehicleSafetyModel, using only black-box query access to its confidence
    # output -- never its weights or analytic gradients.
    logging.basicConfig(level=logging.INFO)

    safety_model = VehicleSafetyModel()

    def target_query_func(state: np.ndarray) -> float:
        # Reveals only the safety confidence score -- exactly what a queryable V2X
        # safety endpoint would expose.
        return safety_model.simple_linear_classifier(state)

    attack = MIFaceAttack(threat_model=safety_model)
    attack.invert_all_classes(target_query_func)

    feature_names = ["speed", "acceleration", "ttc", "road_type", "weather"]
    for target_class, result in attack.reconstructions.items():
        label = "SAFE" if target_class == 1 else "UNSAFE"
        state = dict(zip(feature_names, result["reconstructed_state"]))
        print(f"\nReconstructed typical {label} state (confidence={result['achieved_confidence']:.3f}, "
              f"iterations={result['iterations_run']}):")
        for k, v in state.items():
            print(f"   {k}: {v:.2f}")

    print(f"\nTotal gradient queries used: {safety_model.gradient_queries}")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
