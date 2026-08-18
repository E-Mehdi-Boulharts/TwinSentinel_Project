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
from .membership_inference_black_box import MembershipInferenceBlackBoxAttack, MembershipInferenceTargetModel, create_membership_inference_black_box_attack
from .miface import MIFaceAttack, create_miface_attack
from .database_reconstruction import DatabaseReconstructionAttack, DatabaseTargetModel, create_database_reconstruction_attack
# from .badnets import BadNetsAttack

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
    "MembershipInferenceBlackBoxAttack",
    "MembershipInferenceTargetModel",
    "create_membership_inference_black_box_attack",
    "MIFaceAttack",
    "create_miface_attack",
    "DatabaseReconstructionAttack",
    "DatabaseTargetModel",
    "create_database_reconstruction_attack",
    # "BadNetsAttack",
]
