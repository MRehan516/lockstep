"""
AUTO-GENERATED LOCKSTEP SHIM — DO NOT EDIT MANUALLY.

This shim provides backward compatibility for `calculateShipping` while
Task 1 (signature_change) and Task 2 (read) run in parallel.

Remove this file once both tasks are merged (see cleanup task in DB).
"""
from galaxium.shipping import calculateShipping as _calculateShipping_impl


def calculateShipping(weight, destination, region=None):
    """Backward-compatible shim for calculateShipping.

    Accepts the old signature as well as the new extended signature.
    New parameters default to None if not provided.
    """
    if all(p is None for p in [region]):
        return _calculateShipping_impl(weight, destination)
    return _calculateShipping_impl(weight, destination, region=region)
