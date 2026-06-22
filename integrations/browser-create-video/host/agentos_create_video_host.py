#!/usr/bin/env python3
"""AgentOS native-messaging host for the "Create Video from Image" browser surface.

A browser WebExtension speaks to this script over stdin/stdout using Chrome/Firefox
native-messaging framing: a 4-byte little-endian length prefix, then that many bytes of
UTF-8 JSON. We read ONE message {"url": str, "private": bool}, validate it, spawn the
already-written governed launcher DETACHED, frame a tiny {"ok": true} reply, and exit.

This is a TRUST BOUNDARY. The url originates from a web page's image srcUrl, so it is
attacker-influenced. We therefore:
  * accept only http(s):// and data: schemes (reject everything else, incl. file:// — a web page
    must not reach local files through us; the Dolphin ServiceMenu owns local files);
  * never pass the url through a shell — argv only, no shell=True, no f-strings into a command;
  * spawn in its own session with stdin/stdout/stderr to /dev/null so the browser's native
    call returns immediately and a long (minutes-long) generation can't hang the browser.

We do NOT make any safety decision here. The launcher re-validates the scheme and runs the
full B2 seed-likeness gate (real person -> consent dialog; possible minor -> hard block;
can't-verify -> refuse) plus the VRAM lease and ADR-0016 private mode. A crafted srcUrl
cannot bypass that — the page never reaches the launcher except through this argv.

IMPORTANT: stdout must carry ONLY the framed reply. Any stray print() to stdout corrupts
the native-messaging stream, so all diagnostics go to stderr.
"""
import json
import os
import struct
import subprocess
import sys

# Absolute path to the governed launcher. This TRACKED source keeps the @LAUNCHER@ placeholder so
# the repo stays clean and machine-independent; apply.sh COPIES this file into $HOME and substitutes
# the real path in the copy (the browsers' native-host manifests point at that copy, never at the
# repo). So the working tree is never dirtied by an install. The placeholder is intentionally not a
# valid path — an uninstalled run fails the os.path.isfile check below with a clear error.
LAUNCHER = "@LAUNCHER@"

# The web-image schemes we forward. Mirror the launcher's _fetch_raw allowlist, MINUS file:// —
# a web page must not reach local files through us (the Dolphin ServiceMenu owns local files, with
# full desktop context). Dropping file:// removes a web→local-path existence/size oracle. The trust
# boundary rejects junk early, before we ever spawn anything.
ALLOWED_SCHEMES = ("http://", "https://", "data:")


def read_message(stdin_buffer):
    """Read one native-messaging frame. Returns a parsed dict, or None at clean EOF."""
    raw_len = stdin_buffer.read(4)
    if len(raw_len) == 0:
        return None  # browser closed the pipe — normal shutdown
    if len(raw_len) != 4:
        raise ValueError("truncated length prefix")
    (msg_len,) = struct.unpack("<I", raw_len)
    # Native messaging caps a single message at 1 MB; our payload is a URL, far smaller.
    if msg_len > (1 << 20):
        raise ValueError("message too large")
    data = stdin_buffer.read(msg_len)
    if len(data) != msg_len:
        raise ValueError("truncated message body")
    return json.loads(data.decode("utf-8"))


def write_message(stdout_buffer, obj):
    """Write one native-messaging frame to stdout."""
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    stdout_buffer.write(struct.pack("<I", len(data)))
    stdout_buffer.write(data)
    stdout_buffer.flush()


def validate(message):
    """Return (url, private) or raise ValueError. The web page is not trusted."""
    if not isinstance(message, dict):
        raise ValueError("message is not an object")
    url = message.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("missing url")
    low = url.lower()
    if not any(low.startswith(scheme) for scheme in ALLOWED_SCHEMES):
        # The only place a scheme is whitelisted. Anything else (javascript:, blob:, about:,
        # chrome:, a bare path, ftp:, …) is refused — the page can't smuggle a non-image source.
        raise ValueError("disallowed url scheme")
    private = bool(message.get("private", False))
    return url, private


def spawn_launcher(url, private):
    """Spawn the governed launcher fully detached. argv only — never a shell."""
    if not os.path.isfile(LAUNCHER):
        raise FileNotFoundError("launcher not found: " + LAUNCHER)
    argv = ["python3", LAUNCHER]
    if private:
        argv.append("--private")
    # `--` terminates option parsing so a url can never be misread as a flag, even if ALLOWED_SCHEMES
    # is ever broadened (mirrors the Dolphin .desktop's `-- %f`). The url is one argv element — no
    # shell, no interpolation.
    argv += ["--", url]
    subprocess.Popen(
        argv,
        start_new_session=True,                 # own session: survives the host exiting
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def main():
    try:
        message = read_message(sys.stdin.buffer)
        if message is None:
            return 0
        url, private = validate(message)
        spawn_launcher(url, private)
        write_message(sys.stdout.buffer, {"ok": True})
        return 0
    except Exception as e:
        # Frame the error back to the extension when we still can; the extension toasts it.
        try:
            write_message(sys.stdout.buffer, {"ok": False, "error": str(e)})
        except Exception:
            pass
        print(f"[agentos-create-video-host] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
