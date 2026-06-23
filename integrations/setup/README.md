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

| Bundle | What it is | Rating | Account / gate |
|---|---|---|---|
| **text** | Chat + story writing (runs on Ollama) | SFW | None — no account |
| **image** | Make a picture from a description | SFW | None — no account |
| **video-10eros** | Image → short video (the LTX dream engine) | **18+ (mature)** | No token (free HF mirror), **but an 18+ affirmation is required** |
| **video-wan** | Image → short video (the Wan engine, the default) | **18+ (mature)** | A free Civitai token + an 18+ affirmation |

**Text and image are SFW and need no sign-up.** Both **video** lanes are **mature (18+)** —
there's no SFW video bundle yet — so each requires an explicit 18+ affirmation before it
downloads, and `video-wan` additionally needs a free [Civitai](https://civitai.com) token.
"No account" is not "no gate": the rating (what the model can make) and the account (whether a
sign-up is needed) are two different things, and the mature gate is always opt-in, never automatic.

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
