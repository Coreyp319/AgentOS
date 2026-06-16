# Hermes web dashboard — boot service

Brings up the Hermes web UI (config, API keys, sessions, and the kanban board) on
`http://127.0.0.1:9119` at login. This is the same `:9119` the keyhole tray links out to.

## Install / remove
```
./apply.sh      # install + enable + start the --user service
./restore.sh    # disable + remove the unit
```

## Behavior
- Runs `hermes dashboard --no-open` as a foreground systemd service. The server is
  always *up*; it does not pop a browser. Open `http://127.0.0.1:9119` whenever.
- Independent of `hermes-gateway.service` (the always-on Hermes agent daemon, which is
  already installed). The dashboard reads `kanban.db` directly, so it works on its own;
  it's merely ordered `After=` the gateway to keep the board populated.
- Default profile, localhost-only. No login gate unless you set `dashboard.basic_auth`
  in `~/.hermes/config.yaml`.

## Want it to auto-open a browser tab at login too?
The service deliberately doesn't (calmer, and no DISPLAY in the service context). To
also pop a tab on graphical login, add a desktop autostart entry:
```
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/hermes-dashboard-open.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Open Hermes dashboard
Exec=xdg-open http://127.0.0.1:9119
X-GNOME-Autostart-Delay=8
EOF
```
