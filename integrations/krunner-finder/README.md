# krunner-finder — Ask Claude / Ask Hermes / Search the web from KRunner

A `desktop`-tier, user-scope component that turns on the KRunner D-Bus runner: type any
query into KRunner (Alt+Space) and, when nothing else matches, you get
**Ask Claude**, **Ask Hermes** (local Ollama), and **Search the web** rows — or jump
straight there with a keyword prefix:

| prefix            | action                          |
|-------------------|---------------------------------|
| `c …` `claude …` `ai …` | Ask Claude (if the `claude` CLI is on PATH) |
| `h …` `hermes …`        | Ask local Hermes (if a `hermes*` Ollama model is pulled) |
| `s …` `ddg …` `search …`| DuckDuckGo (the no-prefix default engine) |
| `gh …` / `w …` / `yt …` | GitHub / Wikipedia / YouTube |

Claude and Hermes rows hide themselves when their backend isn't present; web search always works.

## Where the code lives — and why this is just a switch

The runner itself is authored and maintained in the **Nimbus pack**
(`~/whitesur-cachyos-pack/3-krunner-finder/claude-runner/`), the separate desktop repo that
predates AgentOS. Per CLAUDE.md, **AgentOS vendors no pack files** — so this component does
not copy the runner. Instead:

- `apply.sh` drives the pack's own user-scope installer (one source of truth, no duplication).
  Point `NIMBUS_PACK=/path/to/whitesur-cachyos-pack` if your checkout isn't at `~`.
- `restore.sh` is self-contained: it reverses only the user-scope runner (D-Bus service +
  KRunner plugin + deploy dir + the `claudesearchEnabled` key), so you can uninstall cleanly
  even if the pack has since moved. It never touches the pack's **sudo-gated row-styling
  tweak** — revert that one from the pack by hand.

## Needs

`python-dbus` + `python-gobject` (the installer's runtime). The component preflight flags them
if missing: `sudo pacman -S --needed python-dbus python-gobject`.

## Use

```sh
./install.sh --only krunner-finder        # turn it on
./uninstall.sh --only krunner-finder      # turn it off
```

Or toggle it from the status page's adopt webui (ADR-0043) — it's a `root:no` row, so it's
one-click adoptable/removable there.
