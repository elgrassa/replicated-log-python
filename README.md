
# Replicated Log (Python + Flask + Docker)

This repo contains two iterations:

## Iteration 0 — Echo (TCP) client/server
A minimal TCP echo pair to prove the environment.

Run locally (no Docker required):
```bash
python echo/echo_server.py 9009
# in another shell
python echo/echo_client.py 127.0.0.1 9009 "hello world"
```

## Iteration 1 — Blocking Replicated Log

**Architecture**
- 1 Master (HTTP)
- N Secondaries (HTTP)
- After a client `POST /messages`, the Master replicates the message to *all* Secondaries and waits for ACKs **before** returning to the client (blocking replication).
- Secondaries can simulate slow replication via an optional delay.

### API
**Master**
- `POST /messages` with JSON: `{ "msg": "text" }` → `201` after all ACKs received, returns current log: `{ "messages": [...] }`
- `GET /messages` → `{ "messages": [...] }`

**Secondary**
- `GET /messages` → `{ "messages": [...] }`
- (internal) `POST /replicate` with JSON `{ "msg": "text" }` → `{ "status": "ok" }`

### Quick start

```bash
# From repo root
docker compose up --build
```

This will start:
- Master on **http://localhost:8000**
- Secondary 1 on **http://localhost:8001**
- Secondary 2 on **http://localhost:8002** (with 1.5s artificial delay to show blocking)

### Try it

1) Append a message via Master (blocks until both secondaries ACK):
```bash
curl -s -X POST http://localhost:8000/messages   -H 'Content-Type: application/json'   -d '{"msg":"first"}' | jq
```

2) Read messages from Master and both Secondaries:
```bash
curl -s http://localhost:8000/messages | jq
curl -s http://localhost:8001/messages | jq
curl -s http://localhost:8002/messages | jq
```

3) See blocking replication: compare the latency of the `POST` above with and without delay.
   - Adjust delay on a secondary with env var `DELAY_MS` in `docker-compose.yml`, rebuild or restart the service.

### Configuration
- Master reads a comma-separated list of Secondary base URLs from env `SECONDARIES` (default empty).
  Example: `SECONDARIES=http://secondary1:8001,http://secondary2:8001`
- Secondary supports:
  - `PORT` (default `8001`)
  - `DELAY_MS` (default `0`) — artificial delay before ACK to demonstrate blocking replication

### Logs
Both Master and Secondaries use Python `logging` with request/replication details printed to stdout (Docker logs).

### Notes / Assumptions
- No failures/packet loss (as per task). If a Secondary is unreachable, Master returns 502 with details.
- In-memory lists only; restart loses data.
- Simple one-phase replication for clarity (no WAL/2PC/etc).
```

