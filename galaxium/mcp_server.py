"""
Galaxium Travels — MCP server.

Exposes the same shipping operations as MCP tools so that Bob subagents
can call them directly via the Model Context Protocol interface.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from galaxium.shipping import calculateShipping, get_available_destinations
from galaxium.database import create_tables, get_connection, DB_PATH

mcp = FastMCP("galaxium-travels")


@mcp.tool()
def calculate_shipping(weight: float, destination: str) -> dict:
    """
    Calculate the shipping cost for a parcel.

    Args:
        weight:      Parcel weight in kilograms (must be > 0).
        destination: Destination category — 'domestic', 'regional', or
                     'international'.

    Returns:
        A dict with keys: destination, weight, shipping_cost.
    """
    cost = calculateShipping(weight, destination)
    return {"destination": destination, "weight": weight, "shipping_cost": cost}


@mcp.tool()
def place_order(weight: float, destination: str) -> dict:
    """
    Place a shipping order and persist it to the Galaxium database.

    Args:
        weight:      Parcel weight in kilograms.
        destination: Destination category.

    Returns:
        A dict with keys: order_id, destination, weight, shipping_cost.
    """
    create_tables(DB_PATH)
    cost = calculateShipping(weight, destination)
    conn = get_connection(DB_PATH)
    with conn:
        cursor = conn.execute(
            "INSERT INTO orders (destination, weight, region, shipping_cost) VALUES (?, ?, ?, ?)",
            (destination, weight, "unknown", cost),
        )
        order_id = cursor.lastrowid
    conn.close()
    return {"order_id": order_id, "destination": destination, "weight": weight, "shipping_cost": cost}


@mcp.tool()
def list_destinations() -> dict:
    """
    List all supported shipping destination categories.

    Returns:
        A dict with key 'destinations' containing a list of category strings.
    """
    return {"destinations": get_available_destinations()}


if __name__ == "__main__":
    mcp.run()
