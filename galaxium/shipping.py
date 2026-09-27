"""
Galaxium Travels — shipping calculations.

This module intentionally uses the PRE-CONFLICT signature:
    calculateShipping(weight, destination)

The Lockstep demo will show that adding a required `region` parameter
(Task 1) conflicts with a new endpoint (Task 2) that calls this function
using the old two-argument signature.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Rate tables
# ---------------------------------------------------------------------------

_BASE_RATE_PER_KG: dict[str, float] = {
    "domestic": 2.50,
    "regional": 4.75,
    "international": 9.00,
}

_FUEL_SURCHARGE = 0.08  # 8 %


def _get_base_rate(destination: str) -> float:
    """Return the per-kg base rate for a destination category."""
    return _BASE_RATE_PER_KG.get(destination.lower(), _BASE_RATE_PER_KG["international"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculateShipping(weight: float, destination: str) -> float:
    """
    Calculate the shipping cost for a given weight and destination.

    Parameters
    ----------
    weight:       Parcel weight in kilograms.
    destination:  Destination category: 'domestic', 'regional', or
                  'international'.

    Returns
    -------
    Shipping cost in USD, rounded to two decimal places.
    """
    if weight <= 0:
        raise ValueError(f"Weight must be positive, got {weight}")
    base = _get_base_rate(destination)
    cost = weight * base * (1 + _FUEL_SURCHARGE)
    return round(cost, 2)


def get_available_destinations() -> list[str]:
    """Return the list of supported destination categories."""
    return list(_BASE_RATE_PER_KG.keys())
