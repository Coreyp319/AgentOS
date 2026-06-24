# Lucid — interactive dream-loop surface (integration)

Lucid is the one *foreground, interactive* dreaming feature: watch a generated clip, choose
what happens next, and the story continues from the last frame (ADR-0014; MVP = ADR-0015). It
doesn't render ambiently (it's not a wallpaper mood) and it can't live inside the read-only
status board — so it has **its own page**, and the status panel (:9123) is the **front door
that opens it** (Dreaming group → `Open ↗`).

`http://127.0.0.1:8765`

## Install / remove
```
./apply.sh      # install + enable + start the --user service
./restore.sh    # remove it
```
Then add it to the status panel by appending this to `integrations/status-panel/services.json`
(Dreaming group) — already wired if you applied this repo's catalog:
```json
{ "id": "lucid", "name": "Lucid (dream loop)", "group": "Dreaming", "scope": "user",
  "unit": "agentos-lucid.service", "url": "http://127.0.0.1:8765",
  "health": "http://127.0.0.1:8765/healthz",
  "desc": "Interactive branching dream loop — choose what happens next (spike, ADR-0015)" }
```

## How it works
- `apps/dreaming/lucid/lucid_web.py` — a tiny stdlib HTTP surface, loopback-only, in the shared
  instrument "glass" register (`integrations/design/instrument-tokens.md`). It serves the page,
  a `/healthz` the status panel probes, `/api/state` (readiness + chain + validated beats), and a
  same-origin-guarded `POST /api/dream`.
- It is backed by the **safe MVP path** (`lucid_linear` + `lucid_safety`): every prompt — chosen
  or typed — passes the deterministic red-line gate; every video beat goes through the coordinator
  lease (confirm-evict → Spawn → Release). It is **honest about readiness** (coordinator / ComfyUI /
  Ollama) and **fails open** to the ambient shader when any is missing — it never claims ready when
  it can't see, and never forces a GPU load.

## Running it — one owner of :8765
Lucid is the **sole owner** of `127.0.0.1:8765`. Normally it runs as the `agentos-lucid` user
service (installed by `apply.sh`); the status panel just links to it. Don't launch a second copy by
hand and let it race the service — that's the EADDRINUSE crash-loop. `lucid_web.py` now arbitrates:

- A bare `python3 lucid_web.py` that finds the port already served by lucid **yields** (prints, exits
  0 — so the service never crash-loops); if a *non-lucid* process holds it, it exits 1 with a clear
  message instead of looping.
- To iterate on the code, take the port from the service with `./dev.sh run` (= `lucid_web.py
  --takeover`): it SIGTERMs the incumbent lucid (which releases its warm-keep lease and exits first),
  then serves from your terminal. `./dev.sh restart` hands the port back to the service.

```
./dev.sh            # restart the service + follow its log (normal path)
./dev.sh run        # run lucid here, taking :8765 from the service (dev iteration)
./dev.sh stop       # stop the service
```

## Still owed (ADR-0015)
Seed-image upload + the face/likeness guard (B2 — currently start a session via the CLI:
`apps/dreaming/lucid/lucid_linear.py start web --image <opening.png>`), ComfyUI warm-keep, the
kill/keep metric, and the richer authoring surface (branching/scrub). This is the spike-grade
front-door access, not the shipped product.

## Logs
`journalctl --user -u agentos-lucid.service -f`
