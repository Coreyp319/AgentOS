# integrations/setup/ — guided model onboarding (ADR-0044)

Get a box from "nothing installed" (or "Ollama + ComfyUI already here") to a **first reviewable
result** by detecting what's present and **fetching only the gaps**. Brownfield-first: a box that
already has Ollama models and a ComfyUI checkout with weights is the same flow — nothing is
re-downloaded or clobbered.

It is a **detector + thin fetch orchestrator**, not a model manager (ADR-0001): it shells out to
the tools that already do downloading well, and reads the same source of truth the rest of AgentOS
uses — `integrations/models/registry.json` (extended with per-model `fetch` artifacts + `bundles`).

## Use

```
./install.sh --onboard                      # guided: detect + bundles + how to fetch
./install.sh --onboard detect               # brownfield report: have ✓ / partial / fetch ✗
./install.sh --onboard bundles              # the curated sets
./install.sh --onboard plan image           # exactly what 'image' would download
./install.sh --onboard fetch image --yes    # fetch the gap (SFW, no account)
./install.sh --onboard fetch video-wan --mature --yes   # the 18+ lane (needs a Civitai token)
./install.sh --onboard creds set civitai    # store a free Civitai token in the OS keyring
```

(Equivalently: `python3 integrations/setup/setup.py <cmd>`.)

## The three fetch lanes

| Lane | Auth | How |
|---|---|---|
| **Ollama** (text/vision) | none | `ollama pull <ref>` — native resume |
| **HuggingFace** (weights) | none for open repos; a token for *gated* | `curl` on `…/resolve/main/…` → `.part` → atomic rename |
| **Civitai** (mature image/video) | a free account + API key | `curl` on `/api/download/models/<versionId>` with a keyring token; serialized |

A first result **never blocks on an account**: the `text` and `image` bundles are fully no-auth
(Ollama + free HF mirrors). The mature video lanes need a Civitai token (`video-wan`) or have a few
files marked `manual` until their exact source is pinned (`video-10eros`).

## Bundles

`text` · `image` (SFW, no account) · `video-10eros` · `video-wan` (mature, opt-in). A bundle is just
a query over the registry — add or edit one in `registry.json`, no code change.

## Safety (inherits ADR-0008/0009)

- **Mature (18+) is explicit opt-in** — `--mature --yes` affirms it; never a default, never auto-pulled.
- **Credentials go to the OS keyring** (`secret-tool`/KWallet) via stdin — never a file, never argv,
  never a log. `creds clear <svc>` removes them.
- A **hard denylist** refuses CSAM / non-consensual real-likeness repos (the `deadman44/*` exclusion).
- **Link-and-download, never bundle** — AgentOS fetches on your behalf with your credentials; it
  re-hosts nothing. The weights land under your `~/ComfyUI` only.

Tests: `python3 -m unittest discover -s integrations/setup/tests`.
