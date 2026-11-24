# amplitude-play

A small Flask backend for a prediction-market wallet, used to learn Amplitude product analytics.
It keeps all state in memory and sends events from the server with the Amplitude Python SDK.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo 'AMPLITUDE_API_KEY=<your project API key>' > .env.local   # git-ignored
```

Without a key the app still runs and sends no events. Never commit `.env.local`.

## Run

```bash
.venv/bin/flask --app app run --port 5050
```

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/markets` | 8 markets across sports, crypto, elections, esports |
| GET | `/markets/<id>` | One market |
| POST | `/wallet/fund` | `user_id`, `token`, `amount_usd`, `method` (`manual` \| `one_tap_topup`), optional `source_market_id`, optional `request_id` |
| POST | `/predictions` | `user_id`, `market_id`, `side` (`yes` \| `no`), `stake_usd`. Returns 402 if the balance is too low |
| POST | `/markets/<id>/settle` | `market_outcome` (`yes` \| `no`). Winners get 2x their stake |

Example:

```bash
curl -X POST localhost:5050/wallet/fund -H 'Content-Type: application/json' \
  -d '{"user_id":"u1","token":"USDC","amount_usd":25,"request_id":"'"$(uuidgen)"'"}'
```

### Idempotency
`request_id` is created by the client once per tap and reused on retries.
- Same key, same request → the saved response is returned; no second credit and no second event.
- Same key, different request → 422.
- The key is also sent as Amplitude's `insert_id`, so Amplitude drops duplicates too.

## Events (all sent from the server, only after success)

| Event | Properties |
|---|---|
| `Wallet Funded` | `token`, `amount_usd`, `method`, `source_market_id`?, `source_category`? |
| `Prediction Placed` | `market_id`, `category`, `stake_usd`, `side` |
| `Market Settled` | `market_id`, `category`, `market_outcome`, `result`, `stake_usd`, `payout_usd`. One per prediction; marked as not an active event |

## Simulated data

`generate_events.py` sends **simulated** users straight to Amplitude, including an A/B test (`topup_variant`: control vs one_tap_topup). Use the data for funnels, retention and cohorts.

```bash
python3 generate_events.py --dry-run                 # preview, sends nothing
python3 generate_events.py --api-key <key>           # --users 1500 --days 30 --seed 42 by default
```

`device_id` and `insert_id` are built with uuid5, so they're the same on every run. A re-run with the same seed won't create duplicate events.
