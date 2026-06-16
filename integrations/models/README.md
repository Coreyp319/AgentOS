# AgentOS model registry + audit panel

**Every model AgentOS uses, what it's for, where it came from, and proof it's local** — in one
place the user can see and trust. Especially the safety-critical one (the B2 seed-likeness check).

`http://127.0.0.1:9124` (opened from the status panel, :9123)

## The registry is the single source of truth
`registry.json` declares each **affiliation** — a role → the model that fills it:

```json
{ "id": "b2-vision", "role": "Seed-likeness safety check (B2)", "model": "moondream",
  "runtime": "ollama", "size_gb": 1.7, "source": "ollama library: moondream",
  "local": true, "safety_critical": true, "used_by": ["Lucid B2"], "purpose": "…", "notes": "…" }
```

**Code resolves its model from this file**, not from hard-coded strings:
`lucid_models.get("b2-vision")` → `moondream`. Change the model here and the whole system follows
(env vars like `LUCID_B2_MODEL` still override, for tests). So the audit panel and the running code
can never disagree — they read the same file.

## The panel
`models_panel.py` — a tiny stdlib HTTP server (no deps). Read-only. For each affiliation it shows:
- the **role** + a ⚠ **safety-critical** badge where it matters, and the **runtime** (ollama / comfyui);
- the **model**, its **purpose** in plain language, **size**, and **provenance** (where it came from);
- a 🔒 **local** badge — nothing leaves the machine;
- a live **"loaded now?"** dot (Ollama `/api/ps`) and the **free VRAM**, so it's obvious *why* a model
  might be waiting (a model only loads when there's room).

## Install / remove
```
./apply.sh      # install + enable + start the --user service
./restore.sh    # remove it
```

## Logs
`journalctl --user -u agentos-models-panel.service -f`
