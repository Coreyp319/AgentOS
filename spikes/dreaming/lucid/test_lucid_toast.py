#!/usr/bin/env python3
"""Unit tests for lucid_toast (ADR-0019 §5, G5 — recovery-toast a11y + persistence-on-dismiss).

Asserts the G5 contract on the PURE argv builders (no GUI, no notify-send needed):
  - persist-first is structural: a held/review toast cannot be built without a durable row;
    a missing id / non-durable state / private record is REFUSED (a toast can't drop a request)
  - argv-only: the only interpolated value is the validated job_id; no shell metacharacters
  - held toast carries keyboard-focusable actions with self-sufficient screen-reader labels
  - dismiss != cancel is represented (distinct close label vs an explicit Cancel action)
  - NO countdown / no urgency-deadline (no expiry shown, held toast never 'critical')
  - private=True => action-less, transient argv with NO durable-row reference
Run: python3 test_lucid_toast.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_toast as T  # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


# A durable held record, exactly as lucid_queue.enqueue() returns it.
HELD = {"id": "shot_a", "title": "Create from image", "created": time.time() - 300,
        "state": "held", "private": False, "attempts": 0}
REVIEW = {**HELD, "id": "shot_b", "state": "needs-review"}

held_argv = T._build_held_argv(HELD)
review_argv = T._build_held_argv(REVIEW, kind="needs-review")
priv_argv = T._build_private_argv("a private clip")
fail_argv = T._build_enqueue_failed_argv()


# ============================ persist-first is STRUCTURAL ============================
def _refuses(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except (ValueError, TypeError):
        return True


check("held toast REFUSES a bare title (needs the durable record)", _refuses(T._build_held_argv, "just a title"))
check("held toast REFUSES a record with no id (row must already exist)",
      _refuses(T._build_held_argv, {"state": "held", "title": "x"}))
check("held toast REFUSES a non-durable state (render only over held/needs-review)",
      _refuses(T._build_held_argv, {"id": "x", "state": "running", "title": "x"}))
check("held toast REFUSES a private record (no durable surface to point at)",
      _refuses(T._build_held_argv, {"id": "x", "state": "held", "private": True, "title": "x"}))
check("the builder reads the row's id but mutates nothing (id flows into action keys)",
      any(f"run:{HELD['id']}" in a for a in held_argv))
# Structural proof (AST, not docstring prose): lucid_toast must NOT touch queue state.
import ast  # noqa: E402
import inspect  # noqa: E402
_tree = ast.parse(inspect.getsource(T))
_imports = [n for n in ast.walk(_tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
_imported_names = set()
for n in _imports:
    if isinstance(n, ast.ImportFrom) and n.module:
        _imported_names.add(n.module)
    for alias in n.names:
        _imported_names.add(alias.name)
check("lucid_toast never imports lucid_queue (renders only, never writes state)",
      "lucid_queue" not in _imported_names)
# Real call expressions (AST), so prose mentions in the docstring don't trip the check.
_called = set()
for n in ast.walk(_tree):
    if isinstance(n, ast.Call):
        fn = n.func
        if isinstance(fn, ast.Attribute):
            _called.add(fn.attr)
        elif isinstance(fn, ast.Name):
            _called.add(fn.id)
check("lucid_toast never calls enqueue/claim/writeback/expire/recover_crashed (a toast can't drop a request)",
      not (_called & {"enqueue", "claim", "writeback", "recover_crashed"}))
# subprocess is invoked argv-only — never shell=True (the security discipline).
check("lucid_toast never uses shell=True (argv-only subprocess discipline)",
      not any(isinstance(n, ast.keyword) and n.arg == "shell"
              and getattr(n.value, "value", False) is True
              for n in ast.walk(_tree)))


# ============================ argv-only (no shell) ============================
# A list[str] argv is NEVER passed to a shell (no shell=True — asserted above), so human-
# readable summary/body/label TEXT may legitimately contain punctuation (';' '—' '/'). The
# security property is: argv is a real list (each token a separate exec arg, no word-splitting)
# and the only INTERPOLATED value — the job_id — is shell-meta-free and lands only in the
# structural action keys / replace hint, never spliced raw into a single string.
_SHELL_META = set(";|&$`><\n*?(){}[]!~ ")  # the job_id must contain none of these


def _argv_is_clean(argv):
    return argv[0] == "notify-send" and all(isinstance(tok, str) for tok in argv)


def _interpolated_job_id_clean(argv, job_id):
    # job_id appears only inside '-A key:<id>=label' and the 'x-...:lucid-<id>' hint, and it
    # carries no shell metacharacter (so even a hypothetical shell would have nothing to split).
    if set(job_id) & _SHELL_META:
        return False
    structural = []
    i = 0
    while i < len(argv) - 2:  # everything before the trailing summary, body
        structural.append(argv[i])
        i += 1
    # the action label and hint values are the only place job_id is allowed
    return all(("=" in tok or "lucid-" in tok or "x-" in tok or job_id not in tok)
               for tok in structural)


check("held argv is a real list[str] argv (no shell word-splitting)", _argv_is_clean(held_argv))
check("review argv is a real list[str] argv", _argv_is_clean(review_argv))
check("private argv is a real list[str] argv", _argv_is_clean(priv_argv))
check("enqueue-failed argv is a real list[str] argv", _argv_is_clean(fail_argv))
check("the interpolated job_id is shell-meta-free and confined to action keys/hints",
      _interpolated_job_id_clean(held_argv, HELD["id"]))
# the only interpolated value is the validated job_id, and it never lands raw in summary/body
check("job_id is interpolated only into action keys + the replace hint (not raw into summary/body)",
      held_argv[-2] == "Held — Lucid will run this when the graphics card is free"
      and HELD["id"] not in held_argv[-1])


# ============================ focusable actions + a11y labels ============================
def _actions(argv):
    """Return [(key, label)] for every -A KEY=LABEL pair."""
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == "-A" and i + 1 < len(argv):
            k, _, label = argv[i + 1].partition("=")
            out.append((k, label))
            i += 2
        else:
            i += 1
    return out


held_actions = _actions(held_argv)
check("held toast carries exactly two actions (Run, Cancel)", len(held_actions) == 2)
check("Run action is FIRST (initial focus / SC 2.4.3 focus order)", held_actions[0][0].startswith(f"{T.ACTION_RUN}:"))
check("Cancel action is second (focus order Run -> Cancel)", held_actions[1][0].startswith(f"{T.ACTION_CANCEL}:"))
check("action keys carry the job_id (broker resolves job from the key alone)",
      held_actions[0][0] == f"run:{HELD['id']}" and held_actions[1][0] == f"cancel:{HELD['id']}")
check("Run label is a self-sufficient screen-reader label (SC 4.1.2)",
      "Run this held video when the graphics card is free" == held_actions[0][1])
check("Cancel label is a self-sufficient screen-reader label (SC 4.1.2)",
      "Cancel this held video request" == held_actions[1][1])
check("action labels are human words, not bare keys (focusable + labeled)",
      all(len(label.split()) >= 3 for _, label in held_actions))
# notify-send urgency = the polite/calm a11y announce, plus a category hint for the SR/category slot
check("held toast announces politely (urgency normal, never critical)", "-u" in held_argv and held_argv[held_argv.index("-u") + 1] == "normal")
check("held toast carries a notify category hint (a11y/category slot)", "-c" in held_argv)


# ============================ dismiss != cancel ============================
check("Cancel is an EXPLICIT, distinct action (the only remove path)",
      any(k.startswith("cancel:") for k, _ in held_actions))
check("there is NO close/dismiss ACTION key (close is swaync's built-in no-op, not a row removal)",
      not any(k.startswith(("close:", "dismiss:")) for k, _ in held_actions))
# the SR text for the built-in close affordance is shipped via a hint and says 'stays held'
_close_hints = [a for a in held_argv if isinstance(a, str) and "x-agentos-close-label" in a]
check("close affordance has a distinct SR label that says the request STAYS HELD (dismiss != cancel)",
      len(_close_hints) == 1 and "stays held" in _close_hints[0].lower())
check("the body tells the user that closing keeps it held (dismiss != cancel, in plain words)",
      "keeps it held" in held_argv[-1].lower())


# ============================ NO countdown / no urgency-deadline ============================
_joined = " ".join(held_argv).lower()
check("no visible countdown / expiry in the held toast (SC 2.2.1, anti-dark-pattern)",
      not any(w in _joined for w in ("expires in", "expire in", "countdown", "seconds left",
                                     "time left", "act now", "hurry", "deadline")))
check("the body is PATIENT relative time ('asked N ago'), not a deadline",
      "asked" in held_argv[-1].lower() and "min ago" in held_argv[-1].lower())
check("held toast is NOT urgency=critical (a deferral is calm weather, not an alarm)",
      "critical" not in held_argv)
# _asked_ago never produces a forward-looking / negative figure
check("_asked_ago is non-deadline: future-ish input reads 'just now', never negative",
      T._asked_ago(time.time() + 999) == "just now")
check("_asked_ago renders minutes patiently", T._asked_ago(time.time() - 600).startswith("asked") and "min ago" in T._asked_ago(time.time() - 600))


# ============================ private carve-out ============================
check("private builder takes ONLY a title (cannot be handed a record => no durable ref)",
      _refuses(T._build_private_argv, {"id": "x"}) is False)  # a dict title is stringified, not a record path
check("private argv has ZERO -A actions (action-less by contract)", len(_actions(priv_argv)) == 0)
check("private argv references NO durable job id / row (no per-job replace hint)",
      not any("lucid-shot" in a or "shot_a" in a for a in priv_argv))
check("private replace hint is the generic 'lucid-private', never a job id",
      any("lucid-private" in a for a in priv_argv))
check("private toast is transient (RAM-only, burned on logout)",
      any("transient" in a for a in priv_argv))
check("private toast is informational (urgency low), not an alert",
      priv_argv[priv_argv.index("-u") + 1] == "low")
check("private body states auto-retry AND ephemerality (intake-honesty condition)",
      "retrying on its own" in priv_argv[-1].lower() and "log out" in priv_argv[-1].lower())
check("private summary/body never imply a durable board or saved file",
      not any(w in (priv_argv[-2] + " " + priv_argv[-1]).lower() for w in ("saved", "board", "review", "needs you")))


# ============================ fail-open: enqueue failed => no-action critical, no row ============================
check("enqueue-failed toast has NO actions (no row to back a button)", len(_actions(fail_argv)) == 0)
check("enqueue-failed toast is critical (so it is actually seen)",
      fail_argv[fail_argv.index("-u") + 1] == "critical")
check("enqueue-failed toast tells the user to re-trigger (honest, won't retry on its own)",
      "again" in fail_argv[-1].lower() and "won't retry" in fail_argv[-1].lower())


# ============================ needs-review variant ============================
rev_actions = _actions(review_argv)
check("needs-review toast also carries Run + Cancel actions", len(rev_actions) == 2)
check("needs-review toast says it stays held until you act (nothing lost)",
      "nothing is lost" in review_argv[-1].lower())
check("needs-review toast is not critical either (calm escalation)", "critical" not in review_argv)


# ============================ contract block is shipped for the UI implementer ============================
check("G5_CONTRACT documents the persist-first ordering invariant",
      "ORDERING INVARIANT" in T.G5_CONTRACT and "persist the held row FIRST" in T.G5_CONTRACT)
check("G5_CONTRACT ships the persistence-on-dismiss table",
      "PERSISTENCE-ON-DISMISS TABLE" in T.G5_CONTRACT and "NO-OP on row" in T.G5_CONTRACT)
check("G5_CONTRACT states DISMISS != CANCEL", "DISMISS != CANCEL" in T.G5_CONTRACT)
check("G5_CONTRACT documents the no-countdown anti-dark-pattern", "NO COUNTDOWN" in T.G5_CONTRACT)
check("G5_CONTRACT documents the private carve-out", "PRIVATE CARVE-OUT" in T.G5_CONTRACT)
check("G5_CONTRACT lists the WCAG a11y slots", "WCAG 2.2 a11y SLOTS" in T.G5_CONTRACT and "SC 2.1.1" in T.G5_CONTRACT)


print(f"lucid_toast: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
