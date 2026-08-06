"""
Membership Inference Black-Box Attack

Attack Model:
    1. Black-box PRIVACY attack: the attacker has query access to a deployed vehicle
       ML model -- through the same input/output interface a benign V2X client or MCP
       query would use -- and wants to learn whether a SPECIFIC (state, safety-label)
       record was part of the model's own training data, not just what the model
       predicts for it.
    2. Classic shadow-model methodology (Shokri et al., 2017): the attacker trains its
       own "shadow" models -- same architecture as the target, fit on synthetic data
       drawn from a plausible, TARGET-INDEPENDENT distribution (same sampling
       philosophy as knockoff_nets.py / attribute_inference_black_box.py) -- and,
       because it knows exactly which points went into each shadow model's own
       training set, gets free "member" / "non-member" labels for every shadow query.
    3. A local "attack model" (a hand-rolled logistic classifier, gradient-descent
       trained, matching the surrogate style used elsewhere in this project) learns to
       tell members from non-members using each query's TRUE label plus the target's
       output confidence for it -- collapsed into a single "confidence in the claimed
       label" feature, since a model that has memorized a point tends to be unusually
       sure of it. Applied against the REAL target's real output on real vehicles, it
       recovers which specific records were in the training set.
    4. threat_models.py's other models (VehicleSafetyModel, VehicleTrajectoryModel) are
       fixed, hand-derived formulas with no training process at all -- there is nothing
       a real input could be a "member" of. This attack therefore ships its own
       trainable target, MembershipInferenceTargetModel: a small, deliberately
       unregularized hand-rolled MLP fit to a synthetic, noisy VANET safety-labeling
       task, so it can actually memorize its (necessarily small) training set the way a
       real onboard model risks doing -- that train/test memorization gap is the entire
       signal this attack exploits.

Threat Model (VANET context):
    - Attacker has NO white-box access (no weights, no gradients, no knowledge of which
      specific records were used) -- only black-box query access to the target's full
      confidence output, exactly like knockoff_nets_extraction_attack and
      attribute_inference_black_box_attack. Unlike those two -- which steal the model's
      FUNCTION or a hidden INPUT FEATURE -- this attack steals a strictly binary but
      still highly sensitive fact: whether a specific vehicle's exact telemetry record
      was used to build the model at all, which can itself leak that a particular
      vehicle/driver/incident was part of a manufacturer's or operator's training fleet.
    - Confidentiality/privacy attack, not integrity or availability: no vehicle state,
      trajectory, or SUMO/TraCI simulation is ever touched.

Detection Difficulty:
    - No KPI signature -- like the other two black-box confidentiality attacks in this
      project, it never perturbs simulation state, so KPI-based anomaly detectors in
      this project's post_treatment pipeline see nothing.
    - Observable only as: (a) the query volume needed to build/query shadow models
      (entirely offline, against the attacker's own models, invisible to the target),
      and (b) the handful of queries issued against the real target to test specific
      records. Distinguishing (b) from ordinary background traffic requires the same
      query-log/rate-limiting analysis called out for the other two black-box attacks --
      not something this project's KPI-based detectors evaluate.
"""

import numpy as np
import json
from typing import Dict, List, Callable, Optional, Tuple
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class MembershipInferenceTargetModel:
    """
    Small, hand-rolled 2-layer MLP (tanh hidden layer, sigmoid output) standing in for
    a REAL trained onboard vehicle safety model -- unlike this project's other
    VehicleSafetyModel (a fixed formula nothing was ever trained on). Deliberately kept
    unregularized with enough hidden capacity to memorize noise in its (small) training
    set: that train/test memorization gap is what a membership inference attack
    exploits -- a model that generalizes perfectly leaks nothing about which points it
    saw during training.
    """

    def __init__(
        self,
        input_dim: int = 5,
        hidden_dim: int = 16,
        learning_rate: float = 0.3,
        train_epochs: int = 1500,
        l2_reg: float = 0.0,
        seed: Optional[int] = None,
    ):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.learning_rate = learning_rate
        self.train_epochs = train_epochs
        self.l2_reg = l2_reg

        rng = np.random.default_rng(seed)
        self.W1 = rng.normal(0.0, 0.5, size=(input_dim, hidden_dim))
        self.b1 = np.zeros(hidden_dim)
        self.W2 = rng.normal(0.0, 0.5, size=(hidden_dim, 1))
        self.b2 = np.zeros(1)
        self.x_mean = np.zeros(input_dim)
        self.x_std = np.ones(input_dim)
        self.fitted = False

    def _forward(self, X_std: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        z1 = X_std @ self.W1 + self.b1
        h = np.tanh(z1)
        z2 = h @ self.W2 + self.b2
        p = 1.0 / (1.0 + np.exp(-z2))
        return h, p.ravel()

    def fit(self, X: np.ndarray, y: np.ndarray) -> "MembershipInferenceTargetModel":
        """Fit via hand-rolled backprop -- this IS the training process; unlike
        threat_models.py's fixed formulas, real (state, label) records go into it."""
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64).reshape(-1, 1)

        self.x_mean = X.mean(axis=0)
        self.x_std = X.std(axis=0) + 1e-8
        X_std = (X - self.x_mean) / self.x_std
        n = X.shape[0]

        for _ in range(self.train_epochs):
            z1 = X_std @ self.W1 + self.b1
            h = np.tanh(z1)
            z2 = h @ self.W2 + self.b2
            p = 1.0 / (1.0 + np.exp(-z2))

            dz2 = (p - y) / n
            dW2 = h.T @ dz2 + self.l2_reg * self.W2
            db2 = dz2.sum(axis=0)
            dh = dz2 @ self.W2.T
            dz1 = dh * (1.0 - h ** 2)
            dW1 = X_std.T @ dz1 + self.l2_reg * self.W1
            db1 = dz1.sum(axis=0)

            self.W1 -= self.learning_rate * dW1
            self.b1 -= self.learning_rate * db1
            self.W2 -= self.learning_rate * dW2
            self.b2 -= self.learning_rate * db2

        self.fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Query interface exposed to the attack -- returns P(safe=1), never weights."""
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        X_std = (X - self.x_mean) / self.x_std
        _, p = self._forward(X_std)
        return p


class MembershipInferenceBlackBoxAttack:
    """
    Black-box membership inference attack for VANET simulation.

    Trains a local "attack model" that predicts whether a given (state, label) record
    was part of a target model's training set, using only black-box query access to a
    set of attacker-trained shadow models -- never the real target's weights, gradients,
    or actual training data.
    """

    def __init__(
        self,
        num_shadow_models: int = 12,
        shadow_train_size: int = 60,
        shadow_test_size: int = 200,
        target_factory: Optional[Callable[[], MembershipInferenceTargetModel]] = None,
        attack_learning_rate: float = 0.5,
        attack_train_epochs: int = 1500,
        attack_l2_reg: float = 1e-4,
        name: str = "membership_inference_black_box",
    ):
        """
        Args:
            num_shadow_models: How many attacker-trained shadow models to build --
                more shadows means more (member, non-member) attack training examples
            shadow_train_size: Training-set size for each shadow model. Kept small so
                the shadow (and by assumption the target) actually overfits -- a bigger
                MembershipInferenceTargetModel.train_epochs/hidden_dim would need an
                even smaller size to keep leaking signal
            shadow_test_size: Disjoint held-out size per shadow, providing "non-member"
                examples for the attack model
            target_factory: Zero-arg callable returning a fresh, untrained shadow model
                (default: MembershipInferenceTargetModel with default hyperparameters,
                mirroring the real target's architecture -- a standard MIA assumption)
            attack_learning_rate: Gradient descent step size for the attack model
            attack_train_epochs: Training epochs for the attack model
            attack_l2_reg: L2 weight regularization for the attack model
            name: Attack name for logging
        """
        self.num_shadow_models = num_shadow_models
        self.shadow_train_size = shadow_train_size
        self.shadow_test_size = shadow_test_size
        self.target_factory = target_factory or (lambda: MembershipInferenceTargetModel())
        self.attack_learning_rate = attack_learning_rate
        self.attack_train_epochs = attack_train_epochs
        self.attack_l2_reg = attack_l2_reg
        self.name = name

        # Attack model input: [true_label, confidence_in_claimed_label] -> [non-member, member]
        self.attack_input_dim = 2
        self.num_classes = 2
        self.weights = np.zeros((self.attack_input_dim, self.num_classes))
        self.bias = np.zeros(self.num_classes)
        self.x_mean = np.zeros(self.attack_input_dim)
        self.x_std = np.ones(self.attack_input_dim)

        self.fitted = False
        self.train_accuracy: Optional[float] = None
        self.shadow_member_count: int = 0
        self.shadow_nonmember_count: int = 0
        self.eval_accuracy: Optional[float] = None
        self.eval_balanced_accuracy: Optional[float] = None
        self.eval_baseline_accuracy: Optional[float] = None
        self.eval_member_recall: Optional[float] = None
        self.eval_nonmember_recall: Optional[float] = None
        self.start_time = None
        self.end_time = None

        logger.info("MembershipInferenceBlackBox initialized:")
        logger.info(f"  Shadow models: {self.num_shadow_models}")
        logger.info(f"  Shadow train/test size: {self.shadow_train_size}/{self.shadow_test_size}")

    def _attack_features(self, true_label: np.ndarray, output_p: np.ndarray) -> np.ndarray:
        """Collapse [true_label, model_output] into a single 'confidence in the claimed
        label' signal plus the label itself -- a model unusually sure of the label it
        was TOLD is the answer is the hallmark of memorization, not just correctness."""
        true_label = np.asarray(true_label, dtype=np.float64)
        output_p = np.asarray(output_p, dtype=np.float64)
        confidence_in_label = np.where(true_label == 1.0, output_p, 1.0 - output_p)
        return np.stack([true_label, confidence_in_label], axis=-1)

    def _forward(self, X: np.ndarray) -> np.ndarray:
        """Attack model forward pass: softmax over {non-member, member}."""
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
        for _ in range(self.attack_train_epochs):
            preds = self._forward(X)
            error = preds - Y
            grad_w = X_std.T @ error / n + self.attack_l2_reg * self.weights
            grad_b = error.mean(axis=0)
            self.weights -= self.attack_learning_rate * grad_w
            self.bias -= self.attack_learning_rate * grad_b

    def fit(self, sample_data_func: Callable[[int], Tuple[np.ndarray, np.ndarray]]) -> "MembershipInferenceBlackBoxAttack":
        """
        Train the attack model against attacker-owned shadow models.

        Args:
            sample_data_func: sample_data_func(n) -> (X, y) -- the attacker's own
                synthetic data generator, drawing (state, true_safety_label) pairs from
                a plausible, target-independent distribution (the attacker knows the
                task and roughly the input distribution, not the target's actual
                training records). Each call should return a FRESH, independent sample.

        Returns:
            self, with a trained attack classifier available via infer_membership()
        """
        self.start_time = datetime.now()

        X_attack: List[np.ndarray] = []
        y_attack: List[np.ndarray] = []

        for _ in range(self.num_shadow_models):
            X_train, y_train = sample_data_func(self.shadow_train_size)
            X_test, y_test = sample_data_func(self.shadow_test_size)

            shadow = self.target_factory()
            shadow.fit(X_train, y_train)

            p_train = shadow.predict_proba(X_train)
            X_attack.append(self._attack_features(y_train, p_train))
            y_attack.append(np.ones(len(y_train)))

            p_test = shadow.predict_proba(X_test)
            X_attack.append(self._attack_features(y_test, p_test))
            y_attack.append(np.zeros(len(y_test)))

        X_attack_arr = np.concatenate(X_attack, axis=0)
        y_attack_arr = np.concatenate(y_attack, axis=0).astype(int)
        self.shadow_member_count = int(np.sum(y_attack_arr == 1))
        self.shadow_nonmember_count = int(np.sum(y_attack_arr == 0))

        # Balance member/non-member counts before training. shadow_train_size and
        # shadow_test_size are rarely equal (attackers often want a larger, more
        # diverse non-member pool), but an imbalanced attack training set lets a plain
        # softmax classifier minimize loss by just predicting the majority class --
        # ignoring the [true_label, confidence] features entirely and reporting
        # "accuracy" that is really just the class prior, not real membership signal.
        minority_count = min(self.shadow_member_count, self.shadow_nonmember_count)
        member_idx = np.flatnonzero(y_attack_arr == 1)
        nonmember_idx = np.flatnonzero(y_attack_arr == 0)
        rng = np.random.default_rng()
        balanced_idx = np.concatenate([
            rng.choice(member_idx, size=minority_count, replace=False),
            rng.choice(nonmember_idx, size=minority_count, replace=False),
        ])
        X_balanced = X_attack_arr[balanced_idx]
        y_balanced = y_attack_arr[balanced_idx]

        Y_onehot = np.zeros((len(y_balanced), self.num_classes))
        Y_onehot[np.arange(len(y_balanced)), y_balanced] = 1.0

        self._train_classifier(X_balanced, Y_onehot)
        self.fitted = True

        preds = np.argmax(self._forward(X_balanced), axis=1)
        self.train_accuracy = float(np.mean(preds == y_balanced))

        self.end_time = datetime.now()
        elapsed = (self.end_time - self.start_time).total_seconds()
        logger.info(
            f"MembershipInferenceBlackBox fit complete! "
            f"shadow_models={self.num_shadow_models} train_accuracy={self.train_accuracy:.3f} | "
            f"Time: {elapsed:.2f}s"
        )
        return self

    def infer_membership(self, true_label: np.ndarray, target_output_p: np.ndarray) -> np.ndarray:
        """
        Predict membership (1=member, 0=non-member) for real query points, given each
        point's claimed true label and the REAL target's observed output for it.
        """
        X = self._attack_features(true_label, target_output_p)
        X = np.atleast_2d(X)
        return np.argmax(self._forward(X), axis=1)

    def evaluate_against_target(
        self,
        target: MembershipInferenceTargetModel,
        X_members: np.ndarray,
        y_members: np.ndarray,
        X_nonmembers: np.ndarray,
        y_nonmembers: np.ndarray,
    ) -> Dict:
        """
        Real evaluation: query the ACTUAL target (not shadow models) on known member
        points (drawn from the target's real training set) and known non-member points
        (held out from it), and measure how often the attack model tells them apart.

        Reports balanced_accuracy -- the average of member_recall and nonmember_recall
        -- as the primary metric rather than raw accuracy, since X_members/X_nonmembers
        are rarely the same size (a real target's training set and its held-out
        population differ in size) and raw accuracy on an imbalanced evaluation set can
        look deceptively high from a degenerate "always guess the bigger class"
        classifier. balanced_accuracy always scores exactly 0.5 for that degenerate
        case, so 0.5 is a dataset-composition-independent baseline.
        """
        p_members = target.predict_proba(X_members)
        p_nonmembers = target.predict_proba(X_nonmembers)

        pred_members = self.infer_membership(y_members, p_members)
        pred_nonmembers = self.infer_membership(y_nonmembers, p_nonmembers)

        correct = int(np.sum(pred_members == 1)) + int(np.sum(pred_nonmembers == 0))
        total = len(y_members) + len(y_nonmembers)
        accuracy = correct / total if total else 0.0

        member_recall = float(np.mean(pred_members == 1)) if len(y_members) else 0.0
        nonmember_recall = float(np.mean(pred_nonmembers == 0)) if len(y_nonmembers) else 0.0
        balanced_accuracy = (member_recall + nonmember_recall) / 2.0

        self.eval_accuracy = accuracy
        self.eval_balanced_accuracy = balanced_accuracy
        self.eval_baseline_accuracy = 0.5
        self.eval_member_recall = member_recall
        self.eval_nonmember_recall = nonmember_recall

        return {
            "accuracy": accuracy,
            "balanced_accuracy": balanced_accuracy,
            "baseline_accuracy": 0.5,
            "advantage_over_baseline": balanced_accuracy - 0.5,
            "member_recall": member_recall,
            "nonmember_recall": nonmember_recall,
            "num_members": int(len(y_members)),
            "num_nonmembers": int(len(y_nonmembers)),
        }

    def get_statistics(self) -> Dict:
        """Get attack statistics and metadata."""
        elapsed = (self.end_time - self.start_time).total_seconds() if self.end_time else 0
        return {
            "attack_name": self.name,
            "num_shadow_models": self.num_shadow_models,
            "shadow_train_size": self.shadow_train_size,
            "shadow_test_size": self.shadow_test_size,
            "shadow_member_count": self.shadow_member_count,
            "shadow_nonmember_count": self.shadow_nonmember_count,
            "attack_train_epochs": self.attack_train_epochs,
            "train_accuracy": self.train_accuracy,
            "eval_accuracy": self.eval_accuracy,
            "eval_balanced_accuracy": self.eval_balanced_accuracy,
            "eval_baseline_accuracy": self.eval_baseline_accuracy,
            "eval_member_recall": self.eval_member_recall,
            "eval_nonmember_recall": self.eval_nonmember_recall,
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
        }

    def save_to_file(self, filepath: Path) -> None:
        """Save attack metadata and trained attack model to JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Attack saved to {filepath}")


# Convenience functions for SUMO integration

def create_membership_inference_black_box_attack(
    num_shadow_models: int = 12,
    shadow_train_size: int = 60,
    shadow_test_size: int = 200,
) -> MembershipInferenceBlackBoxAttack:
    """Factory function to create attack with common parameters against a default MLP target."""
    return MembershipInferenceBlackBoxAttack(
        num_shadow_models=num_shadow_models,
        shadow_train_size=shadow_train_size,
        shadow_test_size=shadow_test_size,
    )


if __name__ == "__main__":
    # Demo: recover which specific vehicle telemetry records were used to TRAIN a small
    # safety model, using only black-box query access to its confidence output -- never
    # its weights, and never its actual training set (the attacker only gets to build
    # its own shadow models from the same task/distribution).
    logging.basicConfig(level=logging.INFO)

    def sample_vanet_data(n: int, label_noise: float = 0.15) -> Tuple[np.ndarray, np.ndarray]:
        # [speed, acceleration, ttc, road_type, weather] -> noisy ground-truth safety label.
        # Label noise gives an unregularized model something to overfit (memorize) rather
        # than perfectly generalize -- without it there would be no train/test gap at all,
        # and membership inference would have nothing to exploit.
        speed = np.random.uniform(0.0, 30.0, n)
        accel = np.random.uniform(-5.0, 5.0, n)
        ttc = np.random.uniform(0.0, 20.0, n)
        road_type = np.random.choice([0.0, 1.0, 2.0, 3.0], size=n)
        weather = np.random.choice([0.0, 1.0, 2.0], size=n)
        X = np.stack([speed, accel, ttc, road_type, weather], axis=1)

        logits = -0.5 * speed + 0.2 * accel + 1.0 * ttc - 0.1 * road_type + 0.3 * weather
        p = 1.0 / (1.0 + np.exp(-logits))
        y = (p > 0.5).astype(np.float64)
        flip = np.random.rand(n) < label_noise
        y[flip] = 1.0 - y[flip]
        return X, y

    # The REAL target: a small, deliberately unregularized model trained on a small
    # real dataset it might memorize.
    X_train_real, y_train_real = sample_vanet_data(60)
    X_holdout_real, y_holdout_real = sample_vanet_data(300)  # fresh draw, never trained on

    target = MembershipInferenceTargetModel()
    target.fit(X_train_real, y_train_real)

    attack = MembershipInferenceBlackBoxAttack()
    attack.fit(sample_vanet_data)
    results = attack.evaluate_against_target(
        target, X_train_real, y_train_real, X_holdout_real, y_holdout_real
    )

    print(f"\nShadow-model train accuracy (balanced): {attack.train_accuracy:.3f}")
    print(f"Real-target raw accuracy: {results['accuracy']:.3f} (over {results['num_members']} members / {results['num_nonmembers']} non-members)")
    print(f"Real-target balanced accuracy: {results['balanced_accuracy']:.3f} (baseline {results['baseline_accuracy']:.3f})")
    print(f"Advantage over baseline: {results['advantage_over_baseline']:+.3f}")
    print(f"Member recall: {results['member_recall']:.3f} | Non-member recall: {results['nonmember_recall']:.3f}")
    print(f"\nStatistics: {json.dumps(attack.get_statistics(), indent=2)}")
