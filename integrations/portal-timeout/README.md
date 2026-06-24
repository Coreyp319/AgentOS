# portal-timeout — fix xdg-desktop-portal's cold-boot start failure

Gives `xdg-desktop-portal.service` a sane **90s** start timeout (user-scope drop-in) so it stops
failing its first start at cold boot. This is the root-cause fix beneath the notification/boot-delay
cascade that the (now-retired) `swaync-race` integration was fighting downstream.

## The problem
On CachyOS the user manager runs a global **`DefaultTimeoutStartSec=15s`**
(`/usr/lib/systemd/user.conf.d/00-timeout.conf` — a vendor fast-boot tweak). `xdg-desktop-portal`
is `Type=dbus`: its start "completes" only once it acquires `org.freedesktop.portal.Desktop`, and
that needs its KDE backend (`plasma-xdg-desktop-portal-kde`, `After=plasma-core.target`) to be up.

When the portal is **D-Bus-activated early** at cold boot (before Plasma core is ready), it can't
finish init within 15s, so systemd kills it:

```
xdg-desktop-portal.service: start operation timed out. Terminating.
xdg-desktop-portal.service: Failed with result 'timeout'.
```

…and it stays `Failed` until some client re-activates it minutes later. A dead portal:
- breaks **screencast / screen sharing** (the ScreenCast portal is gone),
- breaks **plasmashell's portal registration** (`org.freedesktop.DBus.Error.NoReply`),
- and stalls **every GTK/Qt client that queries `portal.Settings` at boot** ~25s (the D-Bus reply
  timeout) — this is what made cold-boot notifications/theming fragile.

Once the session is actually ready the portal acquires its name in **<1s** — so the 15s budget is
simply too short for an *early* activation to wait out the session-ready boundary.

## The fix
A single per-service override, `xdg-desktop-portal.service.d/timeout.conf`:

```ini
[Service]
TimeoutStartSec=90
```

Restores the upstream-stock 90s for **this service only** — every other unit keeps CachyOS's fast
15s. An early-activated portal now waits for Plasma core instead of being aborted.

**No `After=`/`Wants=` edges are added on purpose.** Adding ordering around the portal previously
formed a `plasmashell` ordering cycle (systemd deleted the plasmashell start job to break it → the
whole shell failed to launch). The timeout is the safe lever; ordering is not.

## Install / revert
- `./apply.sh` — installs the drop-in to `~/.config/systemd/user/` + `daemon-reload`. Effective at
  the **next cold boot**.
- `./restore.sh` — removes it; portal reverts to the manager default (15s).
- Verify now: `systemctl --user show xdg-desktop-portal.service -p TimeoutStartUSec` → `1min 30s`.

## Related (manual, root) — not done by this integration
The portal also logs `Failed to load RealtimeKit property: ... name is not activatable` because
`rtkit-daemon` isn't installed. Harmless (a warning), but installing it removes the noise and gives
PipeWire realtime scheduling: `sudo pacman -S rtkit`. Left as a manual step (needs root).
