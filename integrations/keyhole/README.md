# integrations/keyhole — the Keyhole tray plasmoid (ADR-0012 consumer)

The read-only **legibility instrument** for AgentOS: a Plasma 6 system-tray plasmoid
(`org.agentos.keyhole`) that renders GPU lease/arbitration, VRAM, model residency, and fleet
state. It is a pure **consumer** — it never opens NVML or the lease D-Bus; it file-polls a JSON
feed and re-renders.

## The two halves

| half | what | where it ships |
|------|------|----------------|
| **producer** | `agentosd keyhole` writes `$XDG_RUNTIME_DIR/nimbus-aurora/keyhole.json` (atomic temp+rename, off the lease lock so a render can never delay a SIGKILL) | **core-substrate** (`nimbus-aurora-keyhole.service`) |
| **consumer** | this plasmoid: `Timer`-polled `Plasma5Support.DataSource` read, three representations, honest UNKNOWN (em-dash, never a fake `0`) | **this component** |

Install **core-substrate** for the feed; this component for the eyes on it. Without the feed the
tray shows the honest UNKNOWN look until a producer exists — by design.

## Install / remove

Driver-managed (`id: keyhole`, tier `desktop`):

```sh
integrations/install.sh   --only keyhole     # add
integrations/uninstall.sh --only keyhole     # remove
```

Or directly (`apply.sh`/`restore.sh` are user-scope, no root):

```sh
integrations/keyhole/apply.sh
integrations/keyhole/restore.sh
```

`apply.sh` is idempotent: it `--upgrade`s an existing applet (repairing an older copy installed
straight from `spikes/`), else `--install`s. On a non-Plasma host it skips cleanly.

After install, add it to the panel: **right-click the system tray → Configure System Tray →
Entries → "AgentOS Keyhole" → Shown/Auto**. It idle-vanishes (`Plasmoid.status = PassiveStatus`)
when there's nothing to report.

## Package

```
package/
  metadata.json            Plasma 6 applet manifest (tray-capable, SystemServices category)
  contents/ui/*.qml        the instrument (main.qml hosts the Plasma-only read path; the rest
                           are dependency-light QtQuick so they stay host-agnostic)
  contents/ui/porthole.frag(.qsb)   the ONE scoped ShaderEffect — the aurora-flow glyph porthole
```

The package is the canonical, shipped copy. The throwaway feasibility harness and its test
scaffolding (`harness.qml`, `mutate.sh`, `runtests.sh`, headless poll/contract proofs) remain in
`spikes/keyhole/` as the validation record — see that README for the findings (notably: the read
path MUST be `Plasma5Support.DataSource`, not `XMLHttpRequest`, which reads empty under plasmashell).

## Contract

`keyhole.json` schema 3 (pinned by `pins_the_exact_contract` in `crates/agentosd/src/keyhole.rs`).
UNKNOWN numerics are negative/`null` sentinels rendered as em-dash, so a real `0` stays
distinguishable from "no datum". No write-path (approve/cancel) — read-only by design (ADR-0012 §6).
