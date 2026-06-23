# Set up AgentOS (the onboarding)

This is the **single first-run front door**: it gets your box from "nothing to generate with" to
**making your first image, video, or chat** — by looking at what you already have and downloading
only what's missing — and then lets you turn on **the desktop you want** (the look, the ambient
instruments, the agent wiring) and, if you want it, remote access.

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

## Customize your desktop

Below the models, the wizard's **"Customize your desktop"** section turns the rest of AgentOS on —
each item **one-click, reversible, and previewed** so you see what you're getting:

- **Ambient layer** — the reactive shader wallpaper (a no-Unreal procedural "Hills" field that
  reflects fleet state, with an honest *blind* look when the feed goes quiet), the **keyhole** tray
  instrument, and window-drag → wind.
- **Look** — **Aurora**: one violet accent across the shell, windows, and notifications
  (Plasma-gated; takes effect at your next login).
- **Agents** — wire Hermes to the GPU lease (`gpu-coordinator`, so live AI yields the dream) and the
  needs-you signal.
- **Integrations** — the right-click "Create Video" menus and notification plumbing.

It doesn't reinvent anything: it **proxies the same adopt engine** as the status panel's Features
page (ADR-0043), so a thing you turn on here is the same thing — and is undone the same way (Remove,
or `./uninstall.sh --only <id>`). Components that aren't one-click (a `sudo`/manual step, or remote
access) are shown as **copy-don't-execute**, never run for you.

A bottom **Remote access** card walks you through exposing the UIs over Tailscale — with ample
security warnings behind a consent box. The wizard runs nothing there, and is itself **never** put
on your tailnet (it holds your tokens).

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
needed); the wizard is `setup_web.py` (localhost-only, never tailnet-served — see ADR-0044). The
"Customize your desktop" section is a **server-to-server proxy** of the `:9123` status panel's
ADR-0043 adopt engine (`/api/desktop` · `/api/component` · `/api/component_jobs`); the wizard never
parses `components.conf` itself and never shells the driver. Tests: `python3 -m unittest discover -s
integrations/setup/tests` (and `…/status-panel/tests`).
