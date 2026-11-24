"""
Send simulated prediction-market wallet events to Amplitude, so you have
realistic data to build funnels, retention and an A/B comparison.

All data is SIMULATED. Use it to learn the product, and say so if you mention it.

Usage:
    python3 generate_events.py --dry-run          # preview, sends nothing
    python3 generate_events.py --api-key YOUR_KEY # send to Amplitude

Find the API key in Amplitude: Settings > Organization settings > Projects >
"default" > General > API Key.  If your org is in the EU data centre, add --eu.
"""

import argparse
import json
import random
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

US_ENDPOINT = "https://api2.amplitude.com/2/httpapi"
EU_ENDPOINT = "https://api.eu.amplitude.com/2/httpapi"

CATEGORIES = ["sports", "crypto", "elections", "esports"]
CATEGORY_WEIGHTS = [0.45, 0.30, 0.15, 0.10]
TOKENS = ["USDC", "ETH", "SOL", "BTC"]
COUNTRIES = ["Singapore", "Indonesia", "Malaysia", "Taiwan"]
PLATFORMS = ["iOS", "Android"]

# Chance a user completes funding, by experiment variant.
# Control mirrors the story: about 40% drop at funding.
FUND_RATE = {"control": 0.60, "one_tap_topup": 0.75}
PLACE_RATE = 0.85            # funded -> placed a prediction
# Chance of coming back for another market after the first one settles.
RETURN_RATE = {"sports": 0.45, "crypto": 0.30, "elections": 0.20, "esports": 0.35}

# Fixed namespace for uuid5: the same name always hashes to the same id, so a re-run with the
# same --seed/--users/--days re-sends identical device_id + insert_id and Amplitude
# deduplicates it (same insert_id + device_id within 7 days).
ID_NAMESPACE = uuid.UUID("6f1c2a4e-0000-4000-8000-000000000000")


def event(user_id, device_id, event_type, ts, props=None, user_props=None):
    e = {
        "user_id": user_id,
        "device_id": device_id,
        "event_type": event_type,
        "time": int(ts.timestamp() * 1000),
        "event_properties": props or {},
    }
    if user_props:
        e["user_properties"] = user_props
    return e


def simulate_user(i, now, days):
    user_id = f"demo_user_{i:05d}"
    device_id = str(uuid.uuid5(ID_NAMESPACE, user_id))
    variant = random.choice(["control", "one_tap_topup"])
    user_props = {
        "country": random.choice(COUNTRIES),
        "platform": random.choice(PLATFORMS),
        "topup_variant": variant,
    }
    events = []

    start = now - timedelta(days=random.uniform(1, days), hours=random.uniform(0, 12))
    first_category = random.choices(CATEGORIES, CATEGORY_WEIGHTS)[0]
    category = first_category
    ts = start
    session = 0

    while ts < now:
        market = f"{category}_market_{random.randint(1, 40)}"
        props = {"market_id": market, "category": category, "session_number": session + 1}

        events.append(event(user_id, device_id, "Market Viewed", ts, props,
                            user_props if session == 0 else None))

        # Users who already funded once rarely drop at funding again.
        fund_rate = FUND_RATE[variant] if session == 0 else 0.95
        if random.random() > fund_rate:
            break
        ts += timedelta(minutes=random.uniform(1, 6))
        events.append(event(user_id, device_id, "Wallet Funded", ts, {
            "token": random.choice(TOKENS),
            "amount_usd": round(random.uniform(5, 200), 2),
            "method": "one_tap_topup" if variant == "one_tap_topup" else "manual",
        }))

        if random.random() > PLACE_RATE:
            break
        ts += timedelta(minutes=random.uniform(0.5, 3))
        stake = round(random.uniform(2, 100), 2)
        side = random.choice(["yes", "no"])
        events.append(event(user_id, device_id, "Prediction Placed", ts, {
            **props,
            "stake_usd": stake,
            "side": side,
        }))

        ts += timedelta(hours=random.uniform(6, 48))
        if ts >= now:
            break
        # Same shape as the app (tracking plan v1): market_outcome is how the market
        # resolved; result is what that meant for this user's bet.
        result = random.choice(["won", "lost"])
        events.append(event(user_id, device_id, "Market Settled", ts, {
            **props,
            "market_outcome": side if result == "won" else ("no" if side == "yes" else "yes"),
            "result": result,
            "stake_usd": stake,
            "payout_usd": round(stake * 2, 2) if result == "won" else 0.0,
        }))

        # Retention depends on the category of the user's FIRST prediction.
        if random.random() > RETURN_RATE[first_category]:
            break
        ts += timedelta(days=random.uniform(1, 6))
        session += 1
        category = first_category if random.random() < 0.7 else random.choice(CATEGORIES)

    # Deterministic per-event id: this user's nth event always gets the same insert_id.
    for n, e in enumerate(events):
        e["insert_id"] = str(uuid.uuid5(ID_NAMESPACE, f"{user_id}:{n}"))
    return [e for e in events if e["time"] <= int(now.timestamp() * 1000)]


def send(batch, api_key, endpoint):
    body = json.dumps({"api_key": api_key, "events": batch}).encode()
    req = urllib.request.Request(endpoint, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api-key")
    p.add_argument("--users", type=int, default=1500)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--eu", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    random.seed(args.seed)
    now = datetime.now(timezone.utc)
    events = []
    for i in range(args.users):
        events.extend(simulate_user(i, now, args.days))
    events.sort(key=lambda e: e["time"])

    counts = {}
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    print(f"Simulated {args.users} users, {len(events)} events:")
    for k, v in counts.items():
        print(f"  {k:<18} {v}")

    if args.dry_run:
        print("\nDry run: nothing sent. Sample event:")
        print(json.dumps(events[0], indent=2))
        return
    if not args.api_key:
        raise SystemExit("Add --api-key YOUR_KEY (or use --dry-run).")

    endpoint = EU_ENDPOINT if args.eu else US_ENDPOINT
    for start in range(0, len(events), 1000):
        batch = events[start:start + 1000]
        status, text = send(batch, args.api_key, endpoint)
        print(f"Sent {start + len(batch)}/{len(events)}  status {status}")
        if status != 200:
            print(text)
            break
        time.sleep(0.3)
    print("\nDone. Events appear in Amplitude within a few minutes.")


if __name__ == "__main__":
    main()
