#!/usr/bin/env bash
# Lucid dev helper — keeps a SINGLE owner of :8765 so manual runs and the systemd service never
# collide on EADDRINUSE (see lucid_web.py `_bind_server`).
#
#   dev.sh            restart the agentos-lucid service + follow its log   (the normal path)
#   dev.sh run        run lucid in THIS terminal, taking the port from the service (--takeover)
#   dev.sh stop       stop the service
#
# `run` is for iterating on the code: it SIGTERMs whatever lucid holds :8765 (which releases its
# warm-keep lease and exits cleanly first), then serves from here. Ctrl-C to stop; `dev.sh restart`
# to hand the port back to the service.
set -euo pipefail
WEB="$HOME/Documents/AgentOS/spikes/dreaming/lucid/lucid_web.py"
UNIT="agentos-lucid.service"
case "${1:-restart}" in
  run)
    exec python3 "$WEB" --takeover ;;
  stop)
    systemctl --user stop "$UNIT" ;;
  restart|"")
    systemctl --user reset-failed "$UNIT" 2>/dev/null || true
    systemctl --user restart "$UNIT"
    exec journalctl --user -fu "$UNIT" ;;
  *)
    echo "usage: dev.sh [restart|run|stop]" >&2; exit 2 ;;
esac
