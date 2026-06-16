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
- `spikes/dreaming/lucid/lucid_web.py` — a tiny stdlib HTTP surface, loopback-only, in the shared
  instrument "glass" register (`integrations/design/instrument-tokens.md`). It serves the page,
  a `/healthz` the status panel probes, `/api/state` (readiness + chain + validated beats), and a
  same-origin-guarded `POST /api/dream`.
- It is backed by the **safe MVP path** (`lucid_linear` + `lucid_safety`): every prompt — chosen
  or typed — passes the deterministic red-line gate; every video beat goes through the coordinator
  lease (confirm-evict → Spawn → Release). It is **honest about readiness** (coordinator / ComfyUI /
  Ollama) and **fails open** to the ambient shader when any is missing — it never claims ready when
  it can't see, and never forces a GPU load.

## Still owed (ADR-0015)
Seed-image upload + the face/likeness guard (B2 — currently start a session via the CLI:
`spikes/dreaming/lucid/lucid_linear.py start web --image <opening.png>`), ComfyUI warm-keep, the
kill/keep metric, and the richer authoring surface (branching/scrub). This is the spike-grade
front-door access, not the shipped product.

## Logs
`journalctl --user -u agentos-lucid.service -f`
