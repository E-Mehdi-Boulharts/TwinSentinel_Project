"""
Attribute Inference Black-Box Attack

Attack Model:
    1. Black-box PRIVACY attack: the attacker has query access to a deployed
       vehicle ML model (here, the safety classifier) through the same
       input/output interface a benign V2X client or MCP query would use --
       and separately observes a REAL vehicle's own known telemetry
       (speed, acceleration, time-to-collision) plus that model's safety
       verdict for it. What the attacker does NOT know is one sensitive
       input feature -- road_type -- which reveals what kind of road the
       vehicle is on, a proxy for its location/route.
    2. The attacker first builds an auxiliary training set using data it
       owns or can synthesize itself (states sampled from a plausible,
       TARGET-INDEPENDENT distribution, exactly as in knockoff_nets.py) for
       which it DOES know every feature including road_type. Querying the
       target model on these full states produces (known_features, verdict)
       -> road_type training pairs.
    3. A local "attack model" (a hand-rolled softmax classifier, trained by
       gradient descent, matching the surrogate style used elsewhere in this
       project) learns this mapping. Applied to a real, previously-unseen
       vehicle's known features and observed verdict, it predicts the
       vehicle's true road_type -- information that was never directly
       exposed by the model's own output.

Threat Model (VANET context):
    - Attacker has NO white-box access (no weights, no gradients, no
      decision-tree structure) -- only black-box query access to the
      target, exactly like knockoff_nets_extraction_attack, plus the
      ability to observe (not choose) a handful of a real vehicle's own
      non-sensitive features and its verdict from the model. Unlike
      knockoff_nets (which steals the model's FUNCTION for offline reuse),
      this attack targets a specific vehicle's PRIVATE DATA -- inferring a
      feature it never revealed, from a model output that happened to
      depend on it.
    - Confidentiality/privacy attack, not integrity or availability: no
      vehicle state, trajectory, or SUMO/TraCI simulation is ever touched.

Detection Difficulty:
    - No KPI signature -- like knockoff_nets_extraction_attack, this never
      perturbs simulation state, so KPI-based anomaly detectors in this
      project's post_treatment pipeline see nothing.
    - Observable only as: (a) the query volume needed to build the
      auxiliary/attack-model training set, and (b) the query issued against
      the real vehicle's known features to read its current verdict.
      Distinguishing this from ordinary background traffic requires the
      same query-log/rate-limiting analysis knockoff_nets_extraction_attack
      calls out -- not something this project's KPI-based detectors
      evaluate.
"""

import numpy as np
import json
from typing import Dict, List, Callable, Optional, Tuple
from datetime import datetime
from pathlib import Path
import logging

from .threat_models import VehicleThreatModel, VehicleSafetyModel

logger = logging.getLogger(__name__)


class AttributeInferenceBlackBoxAttack:
    """
    Black-box attribute inference attack for VANET simulation.

    Trains a local "attack model" that predicts one sensitive input feature
    of the target threat model (default: road_type in VehicleSafetyModel)
    from the remaining known features plus the target's own output -- using
    only query access to the target, never its weights or gradients.
    """

    def __init__(
        self,
        threat_model: Optional[VehicleThreatModel] = None,
        attribute_index: int = 3,
        attribute_values: Optional[List[float]] = None,
        learning_rate: float = 0.5,
        train_epochs: int = 2000,
        l2_reg: float = 1e-4,
        name: str = "attribute_inference_black_box",
    ):
        """
        Args:
            threat_model: Target model whose output leaks the sensitive attribute
                (default: VehicleSafetyModel)
            attribute_index: Index of the sensitive feature within the target
                model's input vector (default: 3, road_type in
                [speed, acceleration, ttc, road_type, weather])
            attribute_values: Possible values the sensitive attribute can take
                (default: [0.0, 1.0, 2.0, 3.0], matching road_type's bounds)
            learning_rate: Gradient descent step size for the attack model.
                Needs to be large enough, with enough train_epochs, for the
                classifier to isolate a sensitive attribute whose true effect
                on the target's output is small relative to the other known
                features -- the default values here were tuned against
                VehicleSafetyModel's road_type coefficient, which is ~30x
                smaller than its speed coefficient (see class docstring).
            train_epochs: Training epochs for the attack model
            l2_reg: L2 weight regularization for the attack model. Kept small
                by default because regularization pulls weights toward zero,
                which actively fights recovery of a small-but-real attribute
                coefficient.
            name: Attack name for logging
        """
        self.threat_model = threat_model or VehicleSafetyModel()
        self.attribute_index = attribute_index
        self.attribute_values = list(attribute_values) if attribute_values is not None else [0.0, 1.0, 2.0, 3.0]
        self.value_to_class = {v: i for i, v in enumerate(self.attribute_values)}
        self.num_classes = len(self.attribute_values)
        self.learning_rate = learning_rate
        self.train_epochs = train_epochs
        self.l2_reg = l2_reg
        self.name = name

        input_dim = self.threat_model.config.input_dim
        output_dim = self.threat_model.config.output_dim
        if not (0 <= attribute_index < input_dim):
            raise ValueError(f"attribute_index {attribute_index} out of range for input_dim {input_dim}")

        self.known_dim = input_dim - 1
        self.attack_input_dim = self.known_dim + output_dim

        self.weights = np.zeros((self.attack_input_dim, self.num_classes))
        self.bias = np.zeros(self.num_classes)
        self.x_mean = np.zeros(self.attack_input_dim)
        self.x_std = np.ones(self.attack_input_dim)

        self.fitted = False
        self.train_accuracy: Optional[float] = None
        self.baseline_accuracy: Optional[float] = None
        self.eval_accuracy: Optional[float] = None
        self.eval_baseline_accuracy: Optional[float] = None
        self.start_time = None
        self.end_time = None

        logger.info("AttributeInferenceBlackBox initialized:")
        logger.info(f"  Threat model: {self.threat_model.config.name}")
        logger.info(f"  Attacked attribute index: {self.attribute_index}")
        logger.info(f"  Attribute values: {self.attribute_values}")

    def _split_state(self, state: np.ndarray) -> Tuple[np.ndarray, float]:
        """Split a full state vector into (known_features, true_sensitive_value)."""
        known = np.delete(state, self.attribute_index)
        sensitive = float(state[self.attribute_index])
        return known, sensitive

    def _oracle_output(self, target_query_func: Callable[[np.ndarray], object], full_state: np.ndarray) -> np.ndarray:
        """Query the target with a FULL state (including the true sensitive value) and flatten its response."""
        raw = target_query_func(full_state)
        return np.atleast_1d(np.asarray(raw, dtype=np.float64))

    def _transform_output(self, output: np.ndarray) -> np.ndarray:
        """
        Map a binary classification probability back into logit space before it
        reaches the (linear) attack model. The target's own decision boundary is
        linear in logit space -- e.g. sigmoid(w . state) here -- but curved once
        squashed into a probability, so an attack model that saw the raw
        probability directly could only recover a small, tightly-coupled
        attribute like road_type approximately. Left unchanged for multi-class
        or regression outputs, where no such link function applies.
        """
        if self.threat_model.config.model_type == "classification" and output.shape[-1] == 1:
            p = np.clip(output, 1e-6, 1 - 1e-6)
            return np.log(p / (1 - p))
        return output

    def _attack_features(self, known: np.ndarray, output: np.ndarray) -> np.ndarray:
        """Build the attack model's input: known features + the target's (link-transformed) output."""
        return np.concatenate([known, self._transform_output(output)], axis=-1)

    def _forward(self, X: np.ndarray) -> np.ndarray:
        """Attack model forward pass: softmax over candidate attribute values."""
        X_std = (X - self.x_mean) / self.x_std
        logits = X_std @ self.weights + self.bias
        shifted = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp / exp.sum(axis=1, keepdims=True)

    def _train_classifier(self, X: np.ndarray, Y: np.ndarray) -> None:
        """Full-batch gradient descent softmax classifier training."""
        self.x_mean = X.mean(axis=0)
        self.x_std = X.std(axis=0) + 1e-8
        X_std = (X - self.x_mean) / self.x_std

        n = X.shape[0]
        for _ in range(self.train_epochs):
            preds = self._forward(X)
            error = preds - Y
            grad_w = X_std.T @ error / n + self.l2_reg * self.weights
            grad_b = error.mean(axis=0)
            self.weights -= self.learning_rate * grad_w
            self.bias -= self.learning_rate * grad_b

    def fit(
        self,
        aux_states: np.ndarray,
        target_query_func: Callable[[np.ndarray], object],
    ) -> "AttributeInferenceBlackBoxAttack":
        """
        Train the attack model on an auxiliary dataset the attacker owns/generates itself.

        Args:
            aux_states: (n, input_dim) array of FULL states, including the true
                sensitive attribute, drawn from a plausible, target-independent
                distribution -- the attacker's own background knowledge, not real
                target data.
            target_query_func: Oracle -- the same black-box query interface used
                to steal the model in knockoff_nets.py; returns whatever the
                target model normally exposes (e.g. a safety confidence score).

        Returns:
            self, with a trained attack classifier available via infer()
        """
        self.start_time = datetime.now()
        aux_states = np.asarray(aux_states, dtype=np.float64)

        X_attack, y_attack = [], []
        for state in aux_states:
            known, sensitive = self._split_state(state)
            if sensitive not in self.value_to_class:
                continue
            output = self._oracle_output(target_query_func, state)
            X_attack.append(self._attack_features(known, output))
            y_attack.append(self.value_to_class[sensitive])

        if not X_attack:
            raise ValueError("No auxiliary samples matched attribute_values -- check attribute_index/attribute_values")

        X_attack = np.array(X_attack)
        y_attack = np.array(y_attack)
        Y_onehot = np.zeros((len(y_attack), self.num_classes))
        Y_onehot[np.arange(len(y_attack)), y_attack] = 1.0

        self._train_classifier(X_attack, Y_onehot)
        self.fitted = True

        preds = np.argmax(self._forward(X_attack), axis=1)
        self.train_accuracy = float(np.mean(preds == y_attack))
        counts = np.bincount(y_attack, minlength=self.num_classes)
        self.baseline_accuracy = float(counts.max() / len(y_attack))

        self.end_time = datetime.now()
        elapsed = (self.end_time - self.start_time).total_seconds()
        logger.info(
            f"AttributeInferenceBlackBox fit complete! "
            f"train_accuracy={self.train_accuracy:.3f} vs baseline={self.baseline_accuracy:.3f} | "
            f"Time: {elapsed:.2f}s"
        )
        return self

    def infer(self, known_features: np.ndarray, observed_output: np.ndarray) -> np.ndarray:
        """
        Predict the sensitive attribute for real vehicles, given only their known
        (non-sensitive) features and the target model's already-observed output
        for them (which was computed using their true, unknown attribute value).

        Args:
            known_features: (n, known_dim) or (known_dim,) known feature vector(s)
            observed_output: (n, output_dim) or (output_dim,) matching observed
                target model output(s)

        Returns:
            (n,) array of predicted attribute values, drawn from attribute_values
        """
        known_features = np.atleast_2d(np.asarray(known_features, dtype=np.float64))
        output = np.atleast_2d(np.asarray(observed_output, dtype=np.float64))
        if output.shape[0] != known_features.shape[0]:
            output = np.repeat(output, known_features.shape[0], axis=0)

        X = self._attack_features(known_features, output)
        class_idx = np.argmax(self._forward(X), axis=1)
        return np.array([self.attribute_values[i] for i in class_idx])

    def evaluate_accuracy(
        self,
        eval_states: np.ndarray,
        target_query_func: Callable[[np.ndarray], object],
    ) -> Dict:
        """
        Measure inference accuracy against ground truth on held-out states -- how
        often the attack recovers the TRUE sensitive attribute the target model
        actually saw, compared to a majority-class baseline that ignores the
        model's output entirely.
        """
        eval_states = np.asarray(eval_states, dtype=np.float64)
        known_list, true_list, output_list = [], [], []
        for state in eval_states:
            known, sensitive = self._split_state(state)
            if sensitive not in self.value_to_class:
                continue
            output = self._oracle_output(target_query_func, state)
            known_list.append(known)
            true_list.append(sensitive)
            output_list.append(output)

        if not known_list:
            raise ValueError("No eval samples matched attribute_values -- check attribute_index/attribute_values")

        known_arr = np.array(known_list)
        output_arr = np.array(output_list)
        true_arr = np.array(true_list)

        predicted = self.infer(known_arr, output_arr)
        accuracy = float(np.mean(predicted == true_arr))

        true_classes = np.array([self.value_to_class[v] for v in true_arr])
        counts = np.bincount(true_classes, minlength=self.num_classes)
        eval_baseline = float(counts.max() / len(true_classes))

        self.eval_accuracy = accuracy
        self.eval_baseline_accuracy = eval_baseline
        return {
            "accuracy": accuracy,
            "baseline_accuracy": eval_baseline,
            "advantage_over_baseline": accuracy - eval_baseline,
            "num_samples": int(len(true_arr)),
        }

    def get_statistics(self) -> Dict:
        """Get attack statistics and metadata."""
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "threat_model": self.threat_model.config.name,
            "attribute_index": self.attribute_index,
            "attribute_values": self.attribute_values,
            "num_classes": self.num_classes,
            "train_epochs": self.train_epochs,
            "train_accuracy": self.train_accuracy,
            "baseline_accuracy": self.baseline_accuracy,
            "eval_accuracy": self.eval_accuracy,
            "eval_baseline_accuracy": self.eval_baseline_accuracy,
            "elapsed_seconds": elapsed,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    def to_dict(self) -> Dict:
        """Serialize attack to dictionary."""
        return {
            **self.get_statistics(),
            "attack_weights": self.weights.tolist(),
            "attack_bias": self.bias.tolist(),
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
        """Save attack metadata and trained attack model to JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Attack saved to {filepath}")


# Convenience functions for SUMO integration

def create_attribute_inference_black_box_attack(
    attribute_index: int = 3,
    attribute_values: Optional[List[float]] = None,
) -> AttributeInferenceBlackBoxAttack:
    """Factory function to create attack with common parameters against the default safety model."""
    return AttributeInferenceBlackBoxAttack(
        threat_model=VehicleSafetyModel(),
        attribute_index=attribute_index,
        attribute_values=attribute_values,
    )


if __name__ == "__main__":
    # Demo: infer a vehicle's road_type from its safety verdict + other known telemetry,
    # using only query access to VehicleSafetyModel -- never its weights.
    logging.basicConfig(level=logging.INFO)

    safety_model = VehicleSafetyModel()

    def target_query_func(state: np.ndarray) -> float:
        # Reveals only the safety confidence score -- exactly what a queryable V2X
        # safety endpoint would expose.
        return safety_model.simple_linear_classifier(state)

    def sample_states(n: int) -> np.ndarray:
        # [speed, acceleration, ttc, road_type, weather]
        speed = np.random.uniform(0.0, 30.0, n)
        accel = np.random.uniform(-5.0, 5.0, n)
        ttc = np.random.uniform(0.0, 20.0, n)
        road_type = np.random.choice([0.0, 1.0, 2.0, 3.0], size=n)
        weather = np.random.choice([0.0, 1.0, 2.0], size=n)
        return np.stack([speed, accel, ttc, road_type, weather], axis=1)

    aux_states = sample_states(3000)
    eval_states = sample_states(1000)

    attack = AttributeInferenceBlackBoxAttack(
        threat_model=safety_model,
        attribute_index=3,  # road_type
        attribute_values=[0.0, 1.0, 2.0, 3.0],
    )
    attack.fit(aux_states, target_query_func)
    results = attack.evaluate_accuracy(eval_states, target_query_func)

    print(f"\nTrain accuracy:    {attack.train_accuracy:.3f} (baseline {attack.baseline_accuracy:.3f})")
    print(f"Held-out accuracy: {results['accuracy']:.3f} (baseline {results['baseline_accuracy']:.3f})")
    print(f"Advantage over baseline: {results['advantage_over_baseline']:+.3f}")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
