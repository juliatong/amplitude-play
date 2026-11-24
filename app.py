"""Minimal prediction-market wallet backend (in-memory, for learning Amplitude).

Run:  .venv/bin/flask --app app run
"""

import hashlib
import json
import threading

from flask import Flask, jsonify, request
from amplitude import BaseEvent

from amplitude_client import client

app = Flask(__name__)

TOKENS = {"USDC", "ETH", "SOL", "BTC"}
FUND_METHODS = {"manual", "one_tap_topup"}
SIDES = {"yes", "no"}
MARKET_OUTCOMES = {"yes", "no"}

MARKETS = {
    m["id"]: m
    for m in [
        {"id": "sports_market_1", "category": "sports", "title": "Home team wins the derby"},
        {"id": "sports_market_2", "category": "sports", "title": "Over 2.5 goals in the final"},
        {"id": "crypto_market_1", "category": "crypto", "title": "BTC above $150k by month end"},
        {"id": "crypto_market_2", "category": "crypto", "title": "ETH flips $6k this week"},
        {"id": "elections_market_1", "category": "elections", "title": "Incumbent re-elected"},
        {"id": "elections_market_2", "category": "elections", "title": "Turnout above 70%"},
        {"id": "esports_market_1", "category": "esports", "title": "Underdog wins the major"},
        {"id": "esports_market_2", "category": "esports", "title": "Grand final goes to game 5"},
    ]
}
for _m in MARKETS.values():
    _m["status"] = "open"
    _m["market_outcome"] = None

balances = {}      # user_id -> float (USD)
predictions = []   # list of dicts
processed_funds = {}  # request_id -> {"fingerprint": str, "response": dict}
_fund_lock = threading.Lock()  # one funding at a time, so two retries can't race


def _error(message, status=400):
    return jsonify({"error": message}), status


def _track(event_type, user_id, properties, insert_id=None):
    """Send one event via the shared client; no-op when the API key is missing.

    insert_id: pass a stable id so Amplitude drops duplicates; None lets the SDK pick a random one.
    """
    if client is not None:
        client.track(BaseEvent(event_type=event_type, user_id=user_id,
                               event_properties=properties, insert_id=insert_id))


def _fingerprint(fields):
    """Hash of the validated fields that define a request's intent (key order doesn't matter)."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _positive_amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return round(float(value), 2)


@app.get("/markets")
def list_markets():
    return jsonify(list(MARKETS.values()))


@app.get("/markets/<market_id>")
def get_market(market_id):
    market = MARKETS.get(market_id)
    if market is None:
        return _error("market not found", 404)
    return jsonify(market)


@app.post("/wallet/fund")
def fund_wallet():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    token = body.get("token")
    method = body.get("method", "manual")
    amount = _positive_amount(body.get("amount_usd"))
    source_market_id = body.get("source_market_id")
    request_id = body.get("request_id")  # optional idempotency key, created by the client per tap

    if request_id is not None and (not isinstance(request_id, str) or not request_id):
        return _error("request_id must be a non-empty string")
    if not isinstance(user_id, str) or not user_id:
        return _error("user_id is required")
    if token not in TOKENS:
        return _error(f"token must be one of {sorted(TOKENS)}")
    if method not in FUND_METHODS:
        return _error(f"method must be one of {sorted(FUND_METHODS)}")
    if amount is None:
        return _error("amount_usd must be a positive number")
    if source_market_id is not None and source_market_id not in MARKETS:
        return _error("source_market_id not found", 404)

    # Fingerprint the validated values (amount already normalised), not the raw body.
    fingerprint = _fingerprint({"user_id": user_id, "token": token, "amount_usd": amount,
                                "method": method, "source_market_id": source_market_id})

    with _fund_lock:
        if request_id is not None and request_id in processed_funds:
            saved = processed_funds[request_id]
            if saved["fingerprint"] != fingerprint:
                return _error("request_id was already used for a different request", 422)
            return jsonify(saved["response"])  # retry: same answer, no credit, no event

        balances[user_id] = round(balances.get(user_id, 0.0) + amount, 2)

        properties = {"token": token, "amount_usd": amount, "method": method}
        if source_market_id is not None:  # only when funding started from a market page
            properties["source_market_id"] = source_market_id
            properties["source_category"] = MARKETS[source_market_id]["category"]
        _track("Wallet Funded", user_id, properties, insert_id=request_id)

        response = {"user_id": user_id, "balance": balances[user_id]}
        if request_id is not None:
            processed_funds[request_id] = {"fingerprint": fingerprint, "response": response}

    return jsonify(response)


@app.post("/predictions")
def place_prediction():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    market = MARKETS.get(body.get("market_id"))
    side = body.get("side")
    stake = _positive_amount(body.get("stake_usd"))

    if not isinstance(user_id, str) or not user_id:
        return _error("user_id is required")
    if market is None:
        return _error("market not found", 404)
    if market["status"] != "open":
        return _error("market is already settled", 409)
    if side not in SIDES:
        return _error("side must be 'yes' or 'no'")
    if stake is None:
        return _error("stake_usd must be a positive number")
    if balances.get(user_id, 0.0) < stake:
        return _error("insufficient balance", 402)

    balances[user_id] = round(balances[user_id] - stake, 2)
    prediction = {"user_id": user_id, "market_id": market["id"], "side": side, "stake_usd": stake}
    predictions.append(prediction)
    _track("Prediction Placed", user_id, {
        "market_id": market["id"], "category": market["category"],
        "stake_usd": stake, "side": side,
    })
    return jsonify({**prediction, "balance": balances[user_id]}), 201


@app.post("/markets/<market_id>/settle")
def settle_market(market_id):
    market = MARKETS.get(market_id)
    if market is None:
        return _error("market not found", 404)
    if market["status"] != "open":
        return _error("market is already settled", 409)
    market_outcome = (request.get_json(silent=True) or {}).get("market_outcome")
    if market_outcome not in MARKET_OUTCOMES:
        return _error("market_outcome must be 'yes' or 'no'")

    market["status"] = "settled"
    market["market_outcome"] = market_outcome
    # One event per prediction; winners (side matches the outcome) are paid 2x their stake.
    for p in predictions:
        if p["market_id"] != market_id:
            continue
        won = p["side"] == market_outcome
        payout = round(p["stake_usd"] * 2, 2) if won else 0.0
        if won:
            balances[p["user_id"]] = round(balances.get(p["user_id"], 0.0) + payout, 2)
        _track("Market Settled", p["user_id"], {
            "market_id": market_id, "category": market["category"],
            "market_outcome": market_outcome, "result": "won" if won else "lost",
            "stake_usd": p["stake_usd"], "payout_usd": payout,
        })
    return jsonify(market)
