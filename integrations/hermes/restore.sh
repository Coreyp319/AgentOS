#!/usr/bin/env bash
# Standard restore alias (component-driver convention) → delegates to uninstall.sh.
exec "$(cd "$(dirname "$0")" && pwd)/uninstall.sh" "$@"
