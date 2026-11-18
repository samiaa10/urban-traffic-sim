# API throughput and latency

Measured through the real HTTP API by `python -m backend.perf.api_benchmark --save` on 2026-11-17 (Darwin arm64, Python 3.9.6, 8 cores). The backend ran as deployed: uvicorn, one worker, the Norwich map, the osm traffic model; the rate limit and the database were off. Times are wall-clock milliseconds per request as the client saw them, over localhost, so they include HTTP, JSON and the protections middleware. They depend on the machine.

## Request overhead

`GET /health`: no simulation work, so this is the cost of a request itself.

| Clients | Requests | Mean | p95 | Requests per second |
|---|---|---|---|---|
| 1, one after another | 2,000 | 0.84 ms | 0.95 ms | 1,197 |
| 8 at once | 4,000 | 3.46 ms | 4.40 ms | 2,293 |

## Routing

`GET /route` between 200 random pairs of central junctions (A*, no traffic): mean 2.6 ms, p50 2.2 ms, p95 5.9 ms, 401 junctions explored on average.

## A step, as the website sees it

`POST /simulations/{id}/step?seconds=N`, 30 steps after 5 to warm up, one run. The website asks for one step a second and sends the next only when the last has been answered, so a step slower than a second makes the simulation run slower than its speed setting. *Real-time factor* is simulated seconds per real second when stepping back to back.

| Vehicles | Speed | Mean | p50 | p95 | Real-time factor |
|---|---|---|---|---|---|
| 2 | 1x | 1.3 ms | 1.2 ms | 1.5 ms | 788.4x |
| 2 | 5x | 1.3 ms | 1.2 ms | 1.5 ms | 3927.6x |
| 502 | 1x | 47.1 ms | 45.5 ms | 60.4 ms | 21.2x |
| 502 | 5x | 116.4 ms | 111.3 ms | 178.0 ms | 43.0x |
| 2,002 | 1x | 219.2 ms | 221.0 ms | 240.7 ms | 4.6x |
| 2,002 | 5x | 776.6 ms | 726.4 ms | 1067.9 ms | 6.4x |

## Several visitors at once

Eight runs of 502 vehicles, each stepped 20 times (1x) by its own client at the same time, against one such run stepped alone.

| | Mean step | p95 | Steps per second (all runs) |
|---|---|---|---|
| One run alone | 49.6 ms | 66.5 ms | 20.1 |
| Eight runs at once | 379.2 ms | 519.5 ms | 20.8 |

Each run has its own lock, so runs never wait for each other's locks, but the backend is one Python process: simultaneous steps share one core, and total steps per second stays about the same while each visitor's steps take longer. With eight visitors of 500 vehicles each, every run still gets 2.6 steps a second (faster than real time at 1x).

## Memory

| Server state | Resident memory |
|---|---|
| Map loaded, no runs | 105 MB |
| Eight runs of 502 vehicles | 172 MB |

So about 8 MB per 500-vehicle run (including its traffic manager and route tables); `MAX_LIVE_RUNS` (default 8) bounds the total.
