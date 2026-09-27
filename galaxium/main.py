"""
Galaxium Travels — FastAPI application.

Endpoints
---------
POST /order              Place a shipping order (calls calculateShipping).
GET  /shipping-estimate  Estimate shipping cost for given params (calls calculateShipping).
GET  /destinations       List available destination categories.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from galaxium.database import create_tables, get_connection, DB_PATH
from galaxium.shipping import calculateShipping, get_available_destinations

app = FastAPI(title="Galaxium Travels", version="1.0.0")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _startup() -> None:
    create_tables(DB_PATH)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class OrderRequest(BaseModel):
    destination: str
    weight: float


class OrderResponse(BaseModel):
    order_id: int
    destination: str
    weight: float
    shipping_cost: float


class EstimateResponse(BaseModel):
    destination: str
    weight: float
    shipping_cost: float


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/order", response_model=OrderResponse, status_code=201)
def create_order(req: OrderRequest) -> OrderResponse:
    """Place a shipping order and persist it to the database."""
    try:
        cost = calculateShipping(req.weight, req.destination)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    conn = get_connection(DB_PATH)
    with conn:
        cursor = conn.execute(
            "INSERT INTO orders (destination, weight, region, shipping_cost) VALUES (?, ?, ?, ?)",
            (req.destination, req.weight, "unknown", cost),
        )
        order_id = cursor.lastrowid
    conn.close()

    return OrderResponse(
        order_id=order_id,
        destination=req.destination,
        weight=req.weight,
        shipping_cost=cost,
    )


@app.get("/shipping-estimate", response_model=EstimateResponse)
def shipping_estimate(destination: str, weight: float) -> EstimateResponse:
    """Return a shipping cost estimate without persisting an order."""
    try:
        cost = calculateShipping(weight, destination)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return EstimateResponse(
        destination=destination,
        weight=weight,
        shipping_cost=cost,
    )


@app.get("/destinations")
def list_destinations() -> dict:
    """List all supported destination categories."""
    return {"destinations": get_available_destinations()}
