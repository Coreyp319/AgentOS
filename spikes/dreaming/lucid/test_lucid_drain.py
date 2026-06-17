#!/usr/bin/env python3
"""Unit tests for lucid_drain (ADR-0019 §5, G3) — the one-shot drainer loop, with an INJECTED runner
so no GPU is touched. Covers: one-job-per-fire, strict arrival FIFO, the backoff floor, failure re-hold,
human-cause escalation + sidecar refresh, crash recovery, and runner-exception safety.
Run: python3 test_lucid_drain.py"""
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="lucid_drain_test_")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_queue as Q   # noqa: E402
import lucid_review as R  # noqa: E402
import lucid_drain as D   # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


SPOOL = Q.durable_dir()   # the real durable spool under the temp XDG_DATA_HOME (sidecar lands beside it)


def runner_returning(outcome):
    calls = []

    def run(rec):
        calls.append(rec["id"])
        return outcome
    run.calls = calls
    return run


def _sidecar_pending():
    try:
        return json.load(open(R.review_path()))["pending_review"]
    except (OSError, ValueError):
        return None


# ---- one job per fire, strict arrival FIFO (seq order) ----
Q.enqueue("shot_a", "Create from image", spool=SPOOL)   # seq 1
Q.enqueue("shot_b", "Create from image", spool=SPOOL)   # seq 2
run_done = runner_returning("done")
ran1 = D.drain_once(SPOOL, run_done)
check("drains exactly one job per fire", len(run_done.calls) == 1)
check("FIFO: the first-arrived (seq 1) runs first", ran1[0] == "shot_a")
check("a 'done' job is cleared from the spool", not os.path.isfile(os.path.join(SPOOL, "shot_a.running.json"))
      and not os.path.isfile(os.path.join(SPOOL, "shot_a.held.json")))
check("the other job is still held", os.path.isfile(os.path.join(SPOOL, "shot_b.held.json")))

ran2 = D.drain_once(SPOOL, run_done)
check("second fire runs the second job", ran2[0] == "shot_b")
ran3 = D.drain_once(SPOOL, run_done)
check("an empty spool drains to nothing", ran3 is None)
check("sidecar shows zero needs-review after a clean drain", _sidecar_pending() == 0)

# ---- a failed GPU attempt returns to held (never lost) ----
Q.enqueue("shot_c", "Create from image", spool=SPOOL)
ran = D.drain_once(SPOOL, runner_returning("gpu-busy"))
check("a failed attempt reports gpu-busy and re-holds", ran[1] == "gpu-busy" and ran[2] == "held")
recc = json.load(open(os.path.join(SPOOL, "shot_c.held.json")))
check("the re-held record bumped attempts", recc["attempts"] == 1)
check("the re-held record has a backoff floor", recc["next_retry_after"] > 0)

# ---- the backoff floor is respected: a not-yet-eligible held is skipped ----
ran = D.drain_once(SPOOL, runner_returning("done"))
check("a job inside its backoff window is skipped (not run)", ran is None)

# ---- a human cause escalates to needs-review and lights the sidecar ----
Q.enqueue("shot_d", "Create from image", spool=SPOOL)
ran = D.drain_once(SPOOL, runner_returning("b2-cant-verify"))
check("a human cause escalates to needs-review", ran[2] == "needs-review")
check("needs-review record exists", os.path.isfile(os.path.join(SPOOL, "shot_d.review.json")))
check("the G1 sidecar now counts one needs-review item", _sidecar_pending() == 1)

# ---- crash recovery: an orphaned running record is recovered to held (and backs off before re-running) ----
Q.enqueue("shot_e", "Create from image", spool=SPOOL)
Q.claim(SPOOL, "shot_e")   # leaves shot_e.running.json with no live drainer — simulate a crash
ran = D.drain_once(SPOOL, runner_returning("done"))
check("no orphaned running record survives recovery", not os.path.isfile(os.path.join(SPOOL, "shot_e.running.json")))
rece = json.load(open(os.path.join(SPOOL, "shot_e.held.json")))
check("the orphan is recovered to held with attempts bumped", rece["attempts"] == 1)
check("a recovered (preempted) job backs off before re-running — not run this fire", ran is None)

# ---- a runner exception never loses the request ----
Q.enqueue("shot_f", "Create from image", spool=SPOOL)

def boom(rec):
    raise RuntimeError("simulated runner crash")
ran = D.drain_once(SPOOL, boom)
check("a runner exception is caught and re-held, not lost", ran[1] == "drain-error" and ran[2] == "held")
check("the request survives the runner crash on the spool", os.path.isfile(os.path.join(SPOOL, "shot_f.held.json")))

# ---- main() honors the flock + runs end to end with an injected runner ----
for f in list(Q.read_held(SPOOL)):
    Q.expire(SPOOL, f["id"])              # clear the board
Q.enqueue("shot_g", "Create from image", spool=SPOOL)
rc = D.main(runner=runner_returning("done"))
check("main() returns 0 and drains the held job", rc == 0 and not os.path.isfile(os.path.join(SPOOL, "shot_g.held.json")))

# ---- the REAL _governed_runner: re-enters L.start/L.step on a best-effort lease, fail-open ----
# Monkeypatch the module-level lucid_linear (D.L) and lucid_safety (D.S) with fakes so no GPU,
# lease, or daemon is touched; assert the contract (done / gpu-busy / failure-cause-never-raise)
# and that the governed path is driven exactly as create_from_image does (start -> gated step,
# from the SNAPSHOT, _trusted_seed=True, tier="best-effort").
_real_L, _real_S = D.L, D.S


class _FakeLinear:
    """Records how _governed_runner drives the governed path; `step` returns whatever we set."""
    def __init__(self, step_returns=None, start_raises=False):
        self.step_returns = step_returns
        self.start_raises = start_raises
        self.start_calls = []
        self.step_calls = []

    def start(self, session, image, private=False, _trusted_seed=False, **kw):
        self.start_calls.append({"session": session, "image": image,
                                 "private": private, "_trusted_seed": _trusted_seed})
        if self.start_raises:
            raise RuntimeError("simulated lease/start blowup")
        return {"session": session, "nodes": [{"id": 0}]}

    def step(self, session, prompt, label=None, tier=None):
        self.step_calls.append({"session": session, "prompt": prompt,
                                "label": label, "tier": tier})
        return self.step_returns


class _FakeSafety:
    def __init__(self, gate_returns="gated-motion"):
        self._gate_returns = gate_returns

    def gate_prompt(self, text):
        return self._gate_returns


def _run_governed(rec, *, step_returns=None, start_raises=False, gate_returns="gated-motion"):
    fakeL = _FakeLinear(step_returns=step_returns, start_raises=start_raises)
    D.L, D.S = fakeL, _FakeSafety(gate_returns=gate_returns)
    try:
        outcome = D._governed_runner(rec)
    finally:
        D.L, D.S = _real_L, _real_S
    return outcome, fakeL


# a real snapshot file on the spool, so the os.path.isfile(snapshot) guard passes
_snap = os.path.join(SPOOL, "gov_snap.png")
os.makedirs(SPOOL, exist_ok=True)
with open(_snap, "wb") as _f:
    _f.write(b"\x89PNG\r\n")
_rec = {"id": "gov_x", "snapshot": _snap}

# a produced node -> "done"
out, fl = _run_governed(_rec, step_returns={"id": 1, "clip": "/x.mp4"})
check("governed runner: a produced node -> done", out == "done")
check("governed runner: re-runs FROM THE SNAPSHOT (never a URL)", fl.start_calls[0]["image"] == _snap)
check("governed runner: session id is the record id", fl.start_calls[0]["session"] == "gov_x")
check("governed runner: B2 not re-run (_trusted_seed=True)", fl.start_calls[0]["_trusted_seed"] is True)
check("governed runner: never private (durable spool only)", fl.start_calls[0]["private"] is False)
check("governed runner: drains on a best-effort lease (fail-open by construction)",
      fl.step_calls[0]["tier"] == "best-effort")
check("governed runner: steps with the GATED motion prompt", fl.step_calls[0]["prompt"] == "gated-motion")

# step returns None (GPU busy / preempted / ComfyUI cold) -> "gpu-busy"
out, _ = _run_governed(_rec, step_returns=None)
check("governed runner: step returns None -> gpu-busy", out == "gpu-busy")

# the motion prompt is blocked -> a failure cause, the step is never attempted
out, fl = _run_governed(_rec, gate_returns=None)
check("governed runner: a blocked motion prompt -> failure cause (not done)",
      out == "prompt-blocked" and not fl.step_calls)

# an exception inside (e.g. lease/start blows up) -> a failure cause, NOT a raise
try:
    out, _ = _run_governed(_rec, start_raises=True)
    check("governed runner: an internal exception -> failure cause, not a raise", out == "drain-error")
except Exception:
    check("governed runner: an internal exception -> failure cause, not a raise", False)

# a missing snapshot -> a failure cause (never a URL re-fetch), start never called
out, fl = _run_governed({"id": "gov_y", "snapshot": "/no/such/file.png"})
check("governed runner: a missing snapshot -> failure cause, start not called",
      out == "no-snapshot" and not fl.start_calls)

# --- cleanup ---
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"lucid_drain: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
