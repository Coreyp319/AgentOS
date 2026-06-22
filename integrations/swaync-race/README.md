# swaync-race — swaync wins the notification bus over plasmashell

Makes **swaync** reliably own `org.freedesktop.Notifications` (the desktop's "nervous system",
ADR-0026) instead of losing it to plasmashell on boot. Tracked here so a fresh setup gets the fix;
the live config lives in `~/.config/systemd/user/`.

## The problem
swaync and Plasma both want `org.freedesktop.Notifications`. KDE has **no supported way** to
disable Plasma's built-in notifier (per KDE's ngraham) — the only path is to have swaync own the
name **before** plasmashell initialises its notification engine. swaync is already set up for that
(`Type=dbus` + `BusName=org.freedesktop.Notifications`; plasmashell ordered `After=swaync`), but it
kept losing the race.

## Root cause (v2, from a live boot journal, 2026-06-22)
swaync queries `org.freedesktop.portal.Settings` during init **before** acquiring the bus name. On
a cold boot the xdg-desktop-portal frontend is D-Bus-activated and not yet warm, so swaync's call
**blocks ~25s** on the portal timeout. With GTK init on top, the start blows past `TimeoutStartSec`
and systemd **aborts** the start job. A *failed* start job still satisfies plasmashell's
`After=swaync` ordering, so plasmashell starts and grabs the name; every swaync restart then finds
it taken → start-limit-hit → bricked for the session (plasmashell keeps notifications — fail-open).

An earlier fix (`TimeoutStartSec=45` + a Wayland-socket wait) was necessary but **not sufficient**:
the dominant stall is the portal, not the socket.

## The fix
In `swaync.service.d/nimbus-race.conf`, take the portal off swaync's critical path:
1. `Wants=xdg-desktop-portal.service` — pull the portal into the transaction so it warms
   alongside swaync. **Do NOT `After=xdg-desktop-portal.service`** (regressed 2026-06-22):
   the portal is `After=graphical-session.target` and stock `plasma-core.target` is
   `After=plasma-plasmashell.service`, so `plasmashell → After swaync → After portal → … →
   plasmashell` forms an ordering **cycle**. systemd breaks a cycle by deleting a job — it
   deleted plasmashell's start job, so the whole shell (panels + dock) never started at boot.
   Step 2 sequences swaync after the portal *answers* at runtime, so the `After=` ordering is
   redundant anyway; `Wants=` (no ordering edge) keeps the warm-up without the cycle.
2. A bounded, fail-open `ExecStartPre` that waits until the portal **Settings interface answers**,
   so swaync's own query returns instantly instead of timing out ~25s.
3. `TimeoutStartSec=90` — margin for the one-time cold warmup + swaync's <2s warm acquire.

swaync's first start now acquires the name in ~2s → `swaync.service` reaches `active` (= owns the
name, via `Type=dbus`) → plasmashell (`After=swaync`) finds it taken and **defers**, deterministically.

Files:
- `swaync.service` — the `Type=dbus` unit override (installed only if absent).
- `swaync.service.d/nimbus-race.conf` — the race fix (above).
- `plasma-plasmashell.service.d/after-swaync.conf` — pulls swaync into plasmashell's transaction + orders it first.
- `plasma-plasmashell.service.d/gate-on-swaync.conf.disabled` — **staged fallback** (deterministic
  gate); enable only if swaync *still* loses after the portal fix. It complements, not replaces, the
  portal fix (its 30s wait can't cover a 25s+ portal hang alone).

## Install / remove
```
./apply.sh      # install the drop-ins + daemon-reload — effective at NEXT login
./restore.sh    # remove them → plasmashell reclaims notifications (stock)
```
Or via the registry: `integrations/install.sh` (component `swaync-race`).

**Why next login, not now:** once plasmashell holds the name this session it won't release it, and
swaync requests the name without replacement — so the handoff can only happen cleanly at the next
clean login. Until then plasmashell serves notifications (fail-open; nothing is broken).

## Fail-open
Every wait is bounded and exits success regardless; `Wants=` (not `Requires=`). If swaync can't
start for any reason, plasmashell reclaims notifications — the desktop is never left without a daemon.

## Verify (next clean login)
```
busctl --user status org.freedesktop.Notifications   # → Comm=swaync (not plasmashell)
systemctl --user is-active swaync.service            # → active
systemctl --user is-system-running                   # → running (not degraded from swaync)
```
