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

## Root cause (v3, from a live boot journal + a busctl-monitor trace, 2026-06-23)
swaync is a **GTK4 + libadwaita** app. During init, **before** it requests the bus name, libadwaita
issues *synchronous* `org.freedesktop.portal.Settings` Read/ReadAll calls (color-scheme, accent-color).
A monitor trace caught **three** such libadwaita reads ahead of `RequestName`. On a cold boot
xdg-desktop-portal isn't up — on this box `xdg-desktop-portal.service` itself **fails its first start**
(its backend isn't ready inside its 15s budget) and isn't back for **~3 minutes** — so each sync call
blocks the full **~25s** D-Bus reply timeout: ~75s of stall (exactly why v1's 45s `TimeoutStartSec`
"was necessary but not sufficient"). swaync never acquires the name in time, its start job fails, and a
*failed* start job still satisfies plasmashell's `After=swaync` ordering → plasmashell grabs the name
and keeps it (swaync requests it without replacement and can't reclaim it). Fail-open, but wrong owner.

**The v2 fix made it worse.** v2 added an `ExecStartPre` that *waited for the portal to answer* before
starting swaync — i.e. it made swaync depend on the one service guaranteed dead at cold boot. Its
busy-wait worst case (`60 × (2s + 0.5s) = 150s`) overran `TimeoutStartSec=90`, so start-pre was
**killed** and swaync's daemon never even ran. It *guaranteed* the failure, every boot.

## The fix (v3)
In `swaync.service.d/nimbus-race.conf`, remove the portal from swaync's path instead of waiting on it:
1. `Environment=ADW_DISABLE_PORTAL=1` — libadwaita stops querying the Settings portal. The blocking
   color-scheme/accent reads disappear (trace: **10 portal calls → 1**, and the survivor is GTK4's own
   *async* query, which does **not** gate name acquisition). swaync reaches `RequestName` in ~0.3s even
   with a dead portal — so it wins the name **and** doesn't stall plasmashell (which waits behind it via
   `After=swaync`). swaync's look comes from its own CSS (`/etc/xdg/swaync/style.css` +
   `~/.config/swaync/style.css`), not the portal color-scheme, so nothing visible changes.
2. The Wayland-socket `ExecStartPre` stays (swaync is a layer-shell client; don't launch before its
   compositor socket exists). Bounded + fail-open.
3. No `Wants=`/`After=` on `xdg-desktop-portal` — swaync no longer depends on it.
4. `TimeoutStartSec=45` — generous margin for cold GTK init; far more than the <3s warm acquire now
   that the portal stall is gone.

swaync's first start now acquires the name promptly → `swaync.service` reaches `active` (= owns the
name, via `Type=dbus`) → plasmashell (`After=swaync`) finds it taken and **defers**, deterministically.

> **Separate disease:** `xdg-desktop-portal.service` failing its cold-boot start also breaks
> plasmashell's own portal registration + screencast at boot. Removing this cascade should let it heal,
> but the portal failure is worth its own follow-up.

Files:
- `swaync.service` — the `Type=dbus` unit override (installed only if absent).
- `swaync.service.d/nimbus-race.conf` — the race fix (above).
- `plasma-plasmashell.service.d/after-swaync.conf` — pulls swaync into plasmashell's transaction + orders it first.
- `plasma-plasmashell.service.d/gate-on-swaync.conf.disabled` — **staged fallback** (deterministic
  gate that holds plasmashell until swaync actually *owns* the name); enable only if swaync *still*
  loses after the v3 fix.

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
