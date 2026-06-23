# Set up your models (the onboarding)

This gets your box from "nothing to generate with" to **making your first image, video, or
chat** — by looking at what you already have and downloading only what's missing.

Already have Ollama models or a ComfyUI folder full of weights? It finds them and **reuses
them** — nothing is downloaded twice, nothing already there is touched.

## The easy way

```bash
cd integrations
./install.sh --onboard --web
```

A browser page opens. Pick what you want to make — **Text**, **Image**, or **Video** — and it
downloads the models for that, showing progress. When a row says **✓ ready**, click **Make one**
and it drops you into Lucid to create. That's it.

Prefer the terminal? `./install.sh --onboard` does the same thing without a browser.

## What you can make

| Bundle | What it is | Account needed? |
|---|---|---|
| **text** | Chat + story writing (runs on Ollama) | No |
| **image** | Make a picture from a description | No |
| **video-10eros** | Image → short video (the LTX dream engine) | No |
| **video-wan** | Image → short video (the Wan engine, the default) | A free Civitai token, for the 18+ models |

**Text and image need no sign-up at all.** Only the mature (18+) video models live behind a
free [Civitai](https://civitai.com) account — and that lane is always opt-in, never automatic.

## If a model needs an account

Some mature video models download from Civitai, which needs a free token:

1. Make a free account at [civitai.com](https://civitai.com), turn on "show mature content" in
   your settings, and copy an **API key** from *Account → API Keys*.
2. In the wizard's **Accounts** box (or `./install.sh --onboard creds set civitai`), paste it.

Your token is saved in your system keyring (KWallet) — never written to a file, never logged,
never sent anywhere except the download itself. Remove it any time with
`./install.sh --onboard creds clear civitai`.

## Terminal commands

```bash
./install.sh --onboard detect          # what's already here vs. what's missing
./install.sh --onboard bundles         # the sets you can install
./install.sh --onboard plan image      # exactly what 'image' would download
./install.sh --onboard fetch image     # download the gap (no account)
./install.sh --onboard fetch video-wan --mature --yes   # the 18+ lane (after storing a token)
```

## Good to know

- **It's safe to stop and re-run** — downloads resume, and anything already present is skipped.
- **The 18+ lane is opt-in** and downloads under *your* account. AgentOS hosts nothing itself —
  it fetches on your behalf to your machine. (No minors, no real-person likenesses — enforced.)
- **Where things go:** models land under your ComfyUI folder (`~/ComfyUI/models`, or `$COMFY_ROOT`)
  and your Ollama store. Edit which models a bundle uses in `../models/registry.json`.

---

*For maintainers:* the engine is `setup.py` (stdlib + `curl`/`ollama`/`secret-tool`; no `hf` CLI
needed); the wizard is `setup_web.py` (localhost-only, never tailnet-served — see ADR-0044).
Tests: `python3 -m unittest discover -s integrations/setup/tests`.
