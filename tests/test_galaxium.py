"""
Tests for Galaxium Travels — shipping module and database setup.
"""
import pytest
import tempfile
import os

from galaxium.shipping import calculateShipping, get_available_destinations
from galaxium.database import create_tables, get_connection


# ---------------------------------------------------------------------------
# shipping.py tests
# ---------------------------------------------------------------------------

def test_calculate_shipping_domestic():
    cost = calculateShipping(10.0, "domestic")
    assert cost == round(10.0 * 2.50 * 1.08, 2)


def test_calculate_shipping_international():
    cost = calculateShipping(5.0, "international")
    assert cost == round(5.0 * 9.00 * 1.08, 2)


def test_calculate_shipping_unknown_destination_falls_back_to_international():
    cost_unknown = calculateShipping(5.0, "UNKNOWN_ZONE")
    cost_intl = calculateShipping(5.0, "international")
    assert cost_unknown == cost_intl


def test_calculate_shipping_zero_weight_raises():
    with pytest.raises(ValueError, match="positive"):
        calculateShipping(0, "domestic")


def test_calculate_shipping_negative_weight_raises():
    with pytest.raises(ValueError):
        calculateShipping(-1.0, "domestic")


def test_get_available_destinations_returns_list():
    destinations = get_available_destinations()
    assert isinstance(destinations, list)
    assert "domestic" in destinations
    assert "international" in destinations


# ---------------------------------------------------------------------------
# database.py tests
# ---------------------------------------------------------------------------

def test_create_tables_and_insert_order():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        create_tables(db_path)
        conn = get_connection(db_path)
        with conn:
            conn.execute(
                "INSERT INTO orders (destination, weight, region, shipping_cost) VALUES (?, ?, ?, ?)",
                ("domestic", 2.0, "north", 5.40),
            )
        row = conn.execute("SELECT * FROM orders WHERE id = 1").fetchone()
        assert row["destination"] == "domestic"
        assert row["weight"] == 2.0
        assert row["region"] == "north"
        assert row["shipping_cost"] == 5.40
        conn.close()
    finally:
        os.unlink(db_path)
