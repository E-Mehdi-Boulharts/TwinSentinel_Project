"""
ML Adversarial Attacks Framework for SUMO VANETs
Implements 6 selected attacks: 4 critical + 2 defense tests
"""

from .universal_perturbation import UniversalPerturbationAttack
from .sumo_adapter import UniversalPerturbationSUMOAdapter, create_adapter
from .hopskipjump import HopSkipJumpAttack
from .backdoor_attack import BackdoorAttack, create_backdoor_attack
from .clean_label_feature_collision import CleanLabelFeatureCollisionAttack, create_clean_label_feature_collision_attack
from .knockoff_nets import KnockoffNetsAttack, create_knockoffnets_attack
from .attribute_inference_black_box import AttributeInferenceBlackBoxAttack, create_attribute_inference_black_box_attack
# from .badnets import BadNetsAttack
# from .membership_inference import MembershipInferenceAttack

__all__ = [
    "UniversalPerturbationAttack",
    "UniversalPerturbationSUMOAdapter",
    "create_adapter",
    "HopSkipJumpAttack",
    "BackdoorAttack",
    "create_backdoor_attack",
    "CleanLabelFeatureCollisionAttack",
    "create_clean_label_feature_collision_attack",
    "KnockoffNetsAttack",
    "create_knockoffnets_attack",
    "AttributeInferenceBlackBoxAttack",
    "create_attribute_inference_black_box_attack",
    # "BadNetsAttack",
    # "MembershipInferenceAttack",
]
