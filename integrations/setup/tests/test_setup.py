#!/usr/bin/env python3
"""Tests for the AgentOS onboarding engine (ADR-0044).

Invariants pinned here:
  • the shipped registry is well-formed — every bundle resolves, every fetch artifact has the
    fields its `via` needs, nothing references the safety denylist;
  • brownfield detection is correct — ollama refs (via `ollama list`) and on-disk ComfyUI files
    map to have / partial / fetch, and a present artifact is never re-fetched (idempotent);
  • the fetch command is constructed correctly per lane (ollama pull / curl HF resolve / curl
    civitai by versionId) and a COMPLETE download lands atomically (.part → rename);
  • credentials go to the keyring via stdin, NEVER argv; a token is required for the gated lanes;
  • the Mature lane is excluded unless explicitly included; the CSAM/real-likeness denylist refuses.

Nothing downloads, pulls, or touches the real keyring. Run:
    python3 -m unittest discover -s integrations/setup/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup  # noqa: E402


class _R:  # a fake subprocess.CompletedProcess
    def __init__(self, rc=0, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


class ComfyTmp(unittest.TestCase):
    """Base: an isolated COMFY_ROOT + a controlled ollama list."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentos-setup-test-")
        self._env = os.environ.get("COMFY_ROOT")
        os.environ["COMFY_ROOT"] = self.tmp
        self._old_cache = setup._OLLAMA_LIST_CACHE
        setup._OLLAMA_LIST_CACHE = []                 # default: nothing pulled

    def tearDown(self):
        if self._env is None:
            os.environ.pop("COMFY_ROOT", None)
        else:
            os.environ["COMFY_ROOT"] = self._env
        setup._OLLAMA_LIST_CACHE = self._old_cache
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _have_ollama(self, *refs):
        setup._OLLAMA_LIST_CACHE = list(refs)

    def _place(self, dest, size=10):
        p = Path(self.tmp) / "models" / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p


# ── the shipped registry is well-formed ──────────────────────────────────────────────────────
class RegistryIntegrity(unittest.TestCase):
    def setUp(self):
        self.reg = setup.load_registry()

    def test_bundles_resolve_to_real_models(self):
        for b in setup.bundles(self.reg):
            for mid in b["models"]:
                self.assertIsNotNone(setup.find_model(self.reg, mid), f"{b['id']} → {mid}")

    def test_every_model_has_modality_and_rating(self):
        for m in setup.models(self.reg):
            self.assertIn(m.get("modality"), ("text", "image", "video", "safety", "selector"), m["id"])
            self.assertIn(m.get("rating"), ("sfw", "mature"), m["id"])

    def test_fetch_artifacts_well_formed(self):
        for m in setup.models(self.reg):
            for a in setup.artifacts(m):
                via = a.get("via")
                self.assertIn(via, ("ollama", "hf", "civitai", "manual"), m["id"])
                if via == "ollama":
                    self.assertTrue(a.get("ref"), m["id"])
                elif via == "hf":
                    self.assertTrue(a.get("repo") and a.get("dest"), m["id"])
                elif via in ("civitai", "manual"):
                    self.assertTrue(a.get("dest"), m["id"])

    def test_nothing_references_the_denylist(self):
        for m in setup.models(self.reg):
            for a in setup.artifacts(m):
                self.assertFalse(setup.is_denied(a), f"{m['id']} references a denylisted repo")

    def test_mature_models_are_flagged(self):
        # the wan + 10eros video lanes must be rating=mature (so they're opt-in)
        for mid in ("i2v-dream", "i2v-dream-10eros"):
            self.assertEqual(setup.find_model(self.reg, mid)["rating"], "mature")


# ── brownfield detection ─────────────────────────────────────────────────────────────────────
class Detection(ComfyTmp):
    def test_ollama_artifact_presence(self):
        art = {"via": "ollama", "ref": "qwen2.5vl:3b"}
        self.assertFalse(setup.artifact_present(art))
        self._have_ollama("qwen2.5vl:3b", "hermes3:3b")
        self.assertTrue(setup.artifact_present(art))

    def test_hf_artifact_presence_by_file(self):
        art = {"via": "hf", "repo": "x/y", "file": "f.safetensors", "dest": "checkpoints/f.safetensors"}
        self.assertFalse(setup.artifact_present(art))
        self._place("checkpoints/f.safetensors")
        self.assertTrue(setup.artifact_present(art))

    def test_partial_file_is_not_present(self):
        art = {"via": "hf", "repo": "x/y", "dest": "vae/v.safetensors"}
        self._place("vae/v.safetensors")
        self._place("vae/v.safetensors.part")          # a half-written download
        self.assertFalse(setup.artifact_present(art))

    def test_model_status_have_partial_fetch(self):
        m = {"id": "z", "fetch": [
            {"via": "ollama", "ref": "a:1"},
            {"via": "hf", "repo": "x/y", "dest": "loras/l.safetensors"}]}
        self.assertEqual(setup.model_status(m)["state"], "fetch")        # nothing present
        self._have_ollama("a:1")
        self.assertEqual(setup.model_status(m)["state"], "partial")      # 1 of 2
        self._place("loras/l.safetensors")
        self.assertEqual(setup.model_status(m)["state"], "have")         # both


# ── bundle planning ──────────────────────────────────────────────────────────────────────────
class Planning(ComfyTmp):
    def _reg(self):
        return {
            "models": [
                {"id": "t", "modality": "text", "rating": "sfw", "size_gb": 2,
                 "fetch": [{"via": "ollama", "ref": "t:1"}]},
                {"id": "v", "modality": "video", "rating": "mature", "size_gb": 24,
                 "fetch": [{"via": "civitai", "version_id": "123", "dest": "unet/v.gguf", "auth": "civitai"}]},
            ],
            "bundles": [{"id": "b", "modality": "video", "rating": "mature", "models": ["t", "v"]}],
        }

    def test_mature_excluded_unless_included(self):
        reg = self._reg()
        b = reg["bundles"][0]
        self._have_ollama("t:1")                                        # the SFW text model is present
        sfw = setup.plan_bundle(reg, b, include_mature=False)
        self.assertNotIn("civitai", sfw["needs_auth"])                   # mature model skipped
        self.assertTrue(any(r["state"] == "skipped-mature" for r in sfw["rows"]))
        mature = setup.plan_bundle(reg, b, include_mature=True)
        self.assertIn("civitai", mature["needs_auth"])
        self.assertEqual(len(mature["gap"]), 1)

    def test_present_models_not_in_gap(self):
        reg = self._reg()
        self._have_ollama("t:1")                                         # text model present
        plan = setup.plan_bundle(reg, reg["bundles"][0], include_mature=True)
        self.assertNotIn("t:1", [a.get("ref") for a in plan["gap"]])
        self.assertEqual(plan["approx_gb"], 24.0)                        # only the missing video


# ── fetch command construction (no real downloads) ───────────────────────────────────────────
class FetchCmd(ComfyTmp):
    def test_ollama_pull_cmd(self):
        res = setup.fetch_artifact({"via": "ollama", "ref": "m:1"}, dry=True)
        self.assertEqual(res["cmd"], ["ollama", "pull", "m:1"])

    def test_hf_curl_resolve_url_no_token(self):
        res = setup.fetch_artifact(
            {"via": "hf", "repo": "a/b", "file": "f.safetensors", "dest": "vae/f.safetensors", "auth": "none"}, dry=True)
        url = res["cmd"][-1]
        self.assertEqual(url, "https://huggingface.co/a/b/resolve/main/f.safetensors")
        self.assertNotIn("Authorization: Bearer", " ".join(res["cmd"]))

    def test_civitai_token_not_in_argv_uses_stdin(self):
        res = setup.fetch_artifact(
            {"via": "civitai", "version_id": "42", "dest": "unet/u.gguf", "auth": "civitai"},
            token="secrettoken", dry=True)
        joined = " ".join(res["cmd"])
        self.assertIn("https://civitai.com/api/download/models/42", joined)
        self.assertNotIn("secrettoken", joined)             # token must NOT be in argv (/proc leak)
        self.assertIn("--config", res["cmd"])                # auth header comes from stdin instead
        self.assertTrue(res.get("stdin_auth"))

    def test_token_fed_to_curl_on_stdin(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["input"] = kw.get("input")
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"x")    # "download" the .part
            return _R(0)

        setup.fetch_artifact({"via": "civitai", "version_id": "42", "dest": "unet/u.gguf", "auth": "civitai"},
                             token="sekret", run=fake_run)
        self.assertIn("sekret", captured["input"] or "")
        self.assertIn("Authorization: Bearer", captured["input"] or "")

    def test_gated_without_token_is_skipped_not_failed(self):
        res = setup.fetch_artifact({"via": "civitai", "version_id": "42", "dest": "u.gguf", "auth": "civitai"})
        self.assertEqual(res["skipped"], "needs-token")

    def test_civitai_without_version_is_manual(self):
        res = setup.fetch_artifact({"via": "civitai", "version_id": None, "dest": "u.gguf", "auth": "civitai"},
                                   token="t")
        self.assertEqual(res["skipped"], "manual")

    def test_ollama_fetch_invalidates_presence_cache(self):
        # after a real `ollama pull`, the cached `ollama list` is stale — fetch must invalidate it
        # so a follow-up detect/skip-check re-reads (the bug found in live validation).
        self._have_ollama("old:1")
        setup.fetch_artifact({"via": "ollama", "ref": "new:1"}, run=lambda c, **k: _R(0))
        self.assertIsNone(setup._OLLAMA_LIST_CACHE)

    def test_present_artifact_skipped(self):
        self._place("vae/here.safetensors")
        res = setup.fetch_artifact({"via": "hf", "repo": "a/b", "dest": "vae/here.safetensors"})
        self.assertEqual(res["skipped"], "present")

    def test_denied_artifact_refused(self):
        res = setup.fetch_artifact({"via": "hf", "repo": "deadman44/Wan2.2_T2i_T2v_LoRA", "dest": "loras/x.safetensors"})
        self.assertEqual(res["skipped"], "denied")

    def test_complete_download_lands_atomically(self):
        # a fake run that "downloads" by writing the .part file; fetch must rename it into place.
        art = {"via": "hf", "repo": "a/b", "file": "f.bin", "dest": "checkpoints/f.bin", "auth": "none"}

        def fake_run(cmd, **kw):
            part = cmd[-2]                          # -o <dest>.part <url>  → the .part path
            self.assertTrue(part.endswith(".part"))
            Path(part).write_bytes(b"complete")
            return _R(0)

        res = setup.fetch_artifact(art, run=fake_run)
        dest = Path(self.tmp) / "models" / "checkpoints" / "f.bin"
        self.assertTrue(res["ok"])
        self.assertTrue(dest.is_file())
        self.assertFalse(Path(str(dest) + ".part").exists())            # .part renamed away
        self.assertTrue(setup.artifact_present(art))                    # now detected present


# ── keyring (mock secret-tool; token never in argv) ──────────────────────────────────────────
class Keyring(unittest.TestCase):
    def setUp(self):
        self._old = (setup.shutil.which, setup.subprocess.run)
        self.calls = []
        setup.shutil.which = lambda x: "/usr/bin/secret-tool" if x == "secret-tool" else None

    def tearDown(self):
        setup.shutil.which, setup.subprocess.run = self._old

    def test_set_passes_token_on_stdin_never_argv(self):
        def fake_run(cmd, **kw):
            self.calls.append((cmd, kw))
            self.assertNotIn("supersecret", " ".join(cmd))               # token NOT in argv
            self.assertEqual(kw.get("input"), "supersecret")             # token on stdin
            return _R(0)
        setup.subprocess.run = fake_run
        self.assertTrue(setup.keyring_set("civitai", "supersecret"))

    def test_get_returns_token(self):
        setup.subprocess.run = lambda cmd, **kw: _R(0, "tok123\n")
        self.assertEqual(setup.keyring_get("civitai"), "tok123")

    def test_get_empty_is_none(self):
        setup.subprocess.run = lambda cmd, **kw: _R(1, "")
        self.assertIsNone(setup.keyring_get("civitai"))


class Runtime(ComfyTmp):
    def test_comfyui_present_false_then_true(self):
        self.assertFalse(setup.comfyui_present())          # COMFY_ROOT is the tmp; no .venv
        (Path(self.tmp) / ".venv" / "bin").mkdir(parents=True)
        (Path(self.tmp) / ".venv" / "bin" / "python").write_text("")
        self.assertTrue(setup.comfyui_present())

    def test_comfyui_setup_skips_when_present(self):
        (Path(self.tmp) / ".venv" / "bin").mkdir(parents=True)
        (Path(self.tmp) / ".venv" / "bin" / "python").write_text("")
        self.assertEqual(setup.comfyui_setup(dry=True)["skipped"], "present")

    def test_comfyui_setup_dry_steps(self):
        steps = " ".join(" ".join(s) for s in setup.comfyui_setup(dry=True)["steps"])
        self.assertIn("git clone", steps)
        self.assertIn("ComfyUI", steps)
        self.assertIn("torch", steps)
        self.assertIn(setup.TORCH_INDEX, steps)

    def test_detect_hardware_parses_nvidia_smi(self):
        ow = setup.shutil.which
        setup.shutil.which = lambda x: "/usr/bin/nvidia-smi" if x == "nvidia-smi" else None
        try:
            hw = setup.detect_hardware(run=lambda *a, **k: type("R", (), {"stdout": "24564, 19000\n", "returncode": 0})())
        finally:
            setup.shutil.which = ow
        self.assertEqual(hw["vram_mib"], 24564)
        self.assertGreater(hw["vram_gb"], 23)

    def test_bundle_fit(self):
        reg = {"models": [{"id": "big", "size_gb": 24}, {"id": "small", "size_gb": 2}],
               "bundles": [{"id": "b", "models": ["big", "small"]}]}
        b = reg["bundles"][0]
        self.assertEqual(setup.bundle_fit(reg, b, {"vram_gb": 48}), "fits")
        self.assertEqual(setup.bundle_fit(reg, b, {"vram_gb": 24}), "tight")
        self.assertEqual(setup.bundle_fit(reg, b, {"vram_gb": 12}), "too-big")
        self.assertEqual(setup.bundle_fit(reg, b, {"vram_gb": 0}), "unknown")


class TextAidsRest(ComfyTmp):
    def test_suggest_prompt_strips_ansi_from_model(self):
        out = "A serene beach at sunset\x1b[1D\x1b[K"
        res = setup.suggest_opening_prompt("image", model="m:1",
                                           run=lambda *a, **k: type("R", (), {"stdout": out, "returncode": 0})())
        self.assertEqual(res, "A serene beach at sunset")

    def test_suggest_prompt_default_without_model(self):
        old = setup._text_model_present
        setup._text_model_present = lambda: None
        try:
            self.assertIn("forest", setup.suggest_opening_prompt("video"))
        finally:
            setup._text_model_present = old

    def test_research_happy_path(self):
        f = tempfile.mkstemp(prefix="fakeclaude-")[1]
        res = setup.research_models("video", hw={"vram_gb": 24, "ram_gb": 62}, claude=f,
                                    run=lambda *a, **k: type("R", (), {"stdout": "1. Wan 2.2 14B fp8 ...\n", "returncode": 0})())
        self.assertTrue(res["ok"])
        self.assertIn("Wan", res["suggestions"])

    def test_research_no_claude_is_honest(self):
        self.assertFalse(setup.research_models("video", claude="/no/such/claude")["ok"])


class KeyringFallbackAndManifest(ComfyTmp):
    def setUp(self):
        super().setUp()
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        os.environ["XDG_STATE_HOME"] = self.tmp
        self._owhich = setup.shutil.which
        setup.shutil.which = lambda x: None if x == "secret-tool" else self._owhich(x)

    def tearDown(self):
        setup.shutil.which = self._owhich
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("XDG_STATE_HOME", None)
        super().tearDown()

    def test_keyring_falls_back_to_0600_file(self):
        import stat
        self.assertTrue(setup.keyring_set("civitai", "tok123"))
        p = setup._token_file("civitai")
        self.assertTrue(p.exists())
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)     # disclosed 0600 fallback
        self.assertEqual(setup.keyring_get("civitai"), "tok123")
        self.assertTrue(setup.keyring_clear("civitai"))
        self.assertIsNone(setup.keyring_get("civitai"))

    def test_record_fetch_writes_manifest(self):
        setup.record_fetch({"via": "ollama", "ref": "m:1"}, "m:1")
        self.assertTrue(any(e["dest"] == "m:1" for e in setup.read_manifest()["fetched"]))


class ResearchArgv(unittest.TestCase):
    def test_allowedtools_is_one_arg(self):
        cap = {}
        f = tempfile.mkstemp(prefix="fakeclaude-")[1]
        setup.research_models("video", hw={"vram_gb": 24, "ram_gb": 62}, claude=f,
                              run=lambda c, **k: (cap.setdefault("cmd", c), type("R", (), {"stdout": "x", "returncode": 0})())[1])
        i = cap["cmd"].index("--allowedTools")
        self.assertEqual(cap["cmd"][i + 1], "WebSearch WebFetch")   # one arg — separate args drop WebFetch


if __name__ == "__main__":
    unittest.main(verbosity=2)
