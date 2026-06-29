#!/usr/bin/env python3
"""Tests for the AgentOS model policy resolver (ADR-0049).

The load-bearing invariants:
  • the safety DENYLIST is checked FIRST and is UNCONDITIONAL — a denied ref is refused even with
    allow_any_ollama=True AND its family on the allowlist (curation can never reach under the red line);
  • precedence is exactly  safety > family_block > allow_any > family_allow  (an explicit block beats a
    broad allow, and even blocks a curated/registry model);
  • family is derived from the NAME segment, so a typosquatted namespace does not inherit a family;
  • a missing/malformed policy fails CLOSED to curated-only (never to allow-any).

Run:  python3 -m unittest discover -s integrations/setup/tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import policy  # noqa: E402


class DenylistFirst(unittest.TestCase):
    def test_denied_ref_refused_even_under_allow_any_and_allowlisted_family(self):
        # the adversarial case: the most permissive policy that still must NOT let a denied ref through.
        pol = {"allow_any_ollama": True, "family_allow": ["qwen"], "family_block": []}
        ok, reason = policy.permits("ollama.com/deadman44/something:latest", pol=pol)
        self.assertFalse(ok)
        self.assertEqual(reason, "safety-denied")

    def test_denied_ref_refused_even_when_in_registry(self):
        ok, reason = policy.permits("deadman44/x", in_registry=True, family="qwen", pol=dict(policy.DEFAULT_POLICY))
        self.assertFalse(ok)
        self.assertEqual(reason, "safety-denied")

    def test_is_denied_ref_substring_anywhere(self):
        self.assertTrue(policy.is_denied_ref("hf.co/DEADMAN44/foo"))      # case-insensitive
        self.assertFalse(policy.is_denied_ref("qwen2.5vl:3b"))


class Precedence(unittest.TestCase):
    def test_block_beats_allow_any(self):
        pol = {"allow_any_ollama": True, "family_allow": [], "family_block": ["dolphin"]}
        ok, reason = policy.permits("dolphin3.0-mistral-24b", pol=pol)
        self.assertFalse(ok)
        self.assertEqual(reason, "blocked-family")

    def test_block_beats_curated(self):
        # a user can block a family even though the model is a curated registry entry.
        pol = {"allow_any_ollama": False, "family_allow": [], "family_block": ["wan"]}
        ok, reason = policy.permits("wan22_enhNSFW_nolight", in_registry=True, family="wan", pol=pol)
        self.assertFalse(ok)
        self.assertEqual(reason, "blocked-family")

    def test_curated_only_default_permits_registry_denies_raw(self):
        pol = dict(policy.DEFAULT_POLICY)
        ok, reason = policy.permits("qwen2.5vl:3b", in_registry=True, family="qwen", pol=pol)
        self.assertTrue(ok)
        self.assertEqual(reason, "curated")
        ok, reason = policy.permits("some-random/model:latest", in_registry=False, pol=pol)
        self.assertFalse(ok)
        self.assertEqual(reason, "not-curated")

    def test_allow_any_permits_raw(self):
        pol = {"allow_any_ollama": True, "family_allow": [], "family_block": []}
        ok, reason = policy.permits("qwen3.6-27b-64k", pol=pol)
        self.assertTrue(ok)
        self.assertEqual(reason, "allow-any")

    def test_allow_any_with_allowlist_narrows(self):
        pol = {"allow_any_ollama": True, "family_allow": ["qwen"], "family_block": []}
        ok, _ = policy.permits("qwen3.6-27b-64k", pol=pol)
        self.assertTrue(ok)
        ok, reason = policy.permits("gemma4:latest", pol=pol)
        self.assertFalse(ok)
        self.assertEqual(reason, "not-in-allowlist")

    def test_empty_allowlist_means_allow_any_not_allow_none(self):
        pol = {"allow_any_ollama": True, "family_allow": [], "family_block": []}
        ok, _ = policy.permits("llama3.1:8b", pol=pol)
        self.assertTrue(ok)


class FamilyDerivation(unittest.TestCase):
    def test_real_refs_map_to_families(self):
        cases = {
            "qwen2.5vl:3b": "qwen",
            "qwen3.6-27b-64k": "qwen",
            "huihui_ai/qwen2.5-abliterate:3b": "qwen",
            "hf.co/bartowski/MN-12B-Mag-Mell-R1-GGUF:Q5_K_M": "mistral-nemo",
            "hf.co/bartowski/Rocinante-12B-v1.1-GGUF:Q5_K_M": "mistral-nemo",
            "hf.co/NousResearch/Hermes-4.3-36B-GGUF:Q4_K_M": "hermes",
            "hermes3:3b": "hermes",
            "gemma4:latest": "gemma",
            "moondream:latest": "moondream",
            "dolphin3.0-mistral-24b": "dolphin",
            "divingIllustriousReal_v40VAE.safetensors": "illustrious",
            "wan22_enhNSFW_nolight_cf_Q6K_high.gguf": "wan",
            "10Eros_v1-Q6_K.gguf": "ltx",
        }
        for ref, fam in cases.items():
            self.assertEqual(policy.derive_family(ref), fam, ref)

    def test_typosquat_namespace_does_not_inherit_family(self):
        # 'qwen-safe' is the NAMESPACE; the model NAME is 'backdoor' → 'other', so an allow-qwen
        # policy does NOT auto-permit it (the security-lens typosquat case).
        self.assertEqual(policy.derive_family("qwen-safe/backdoor:latest"), "other")
        pol = {"allow_any_ollama": True, "family_allow": ["qwen"], "family_block": []}
        ok, reason = policy.permits("qwen-safe/backdoor:latest", pol=pol)
        self.assertFalse(ok)
        self.assertEqual(reason, "not-in-allowlist")

    def test_unknown_is_other(self):
        self.assertEqual(policy.derive_family("totally-made-up-thing:v1"), "other")
        self.assertEqual(policy.derive_family(""), "other")


class FailClosed(unittest.TestCase):
    def test_normalize_drops_junk(self):
        pol = policy.normalize_policy({"allow_any_ollama": "yes", "family_allow": "qwen",
                                       "family_block": ["Dolphin", 5, "n/s f*w!"], "extra": 1})
        self.assertFalse(pol["allow_any_ollama"])          # non-bool → False (fail closed)
        self.assertEqual(pol["family_allow"], [])           # non-list → []
        self.assertIn("dolphin", pol["family_block"])       # sanitized + lowercased
        self.assertNotIn("extra", pol)

    def test_malformed_file_loads_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "policy.json"
            p.write_text("{ this is not json")
            self.assertEqual(policy.load_policy(p), policy.DEFAULT_POLICY)

    def test_missing_file_loads_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(policy.load_policy(Path(d) / "nope.json"), policy.DEFAULT_POLICY)

    def test_save_then_load_roundtrip_and_0600(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "agentos" / "policy.json"
            pol = {"allow_any_ollama": True, "family_allow": ["qwen", "llama"], "family_block": ["dolphin"],
                   "mature_affirmed_at": 123.0}
            self.assertTrue(policy.save_policy(pol, p))
            self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
            back = policy.load_policy(p)
            self.assertTrue(back["allow_any_ollama"])
            self.assertEqual(back["family_allow"], ["llama", "qwen"])     # sorted+deduped
            self.assertEqual(back["family_block"], ["dolphin"])
            self.assertEqual(back["mature_affirmed_at"], 123.0)


class HostPin(unittest.TestCase):
    def test_ref_host(self):
        self.assertIsNone(policy.ref_host("qwen3.6:27b"))            # no '/', dot is in the name not a host
        self.assertIsNone(policy.ref_host("bartowski/model"))       # namespace/name on the default registry
        self.assertEqual(policy.ref_host("hf.co/bartowski/MN-12B:Q5"), "hf.co")
        self.assertEqual(policy.ref_host("evil.com/ns/model"), "evil.com")
        self.assertEqual(policy.ref_host("localhost:5000/m"), "localhost:5000")

    def test_host_allowed(self):
        self.assertTrue(policy.host_allowed("qwen3.6:27b"))         # default registry
        self.assertTrue(policy.host_allowed("library/qwen:7b"))     # namespace, default registry
        self.assertTrue(policy.host_allowed("hf.co/bartowski/X:Q5"))
        self.assertFalse(policy.host_allowed("evil.com/ns/model"))  # arbitrary host → refused
        self.assertFalse(policy.host_allowed("localhost:11434/x"))


class Mature(unittest.TestCase):
    def test_markers(self):
        self.assertTrue(policy.is_mature_marker("huihui_ai/qwen2.5-abliterate:3b"))
        self.assertTrue(policy.is_mature_marker("dolphin3.0-mistral-24b"))
        self.assertFalse(policy.is_mature_marker("qwen2.5vl:3b"))

    def test_allow_any_requires_affirm(self):
        self.assertTrue(policy.requires_mature_affirm("qwen2.5vl:3b", {"allow_any_ollama": True}))
        self.assertFalse(policy.requires_mature_affirm("qwen2.5vl:3b", {"allow_any_ollama": False}))
        self.assertTrue(policy.requires_mature_affirm("x-uncensored:v1", {"allow_any_ollama": False}))

    def test_is_affirmed(self):
        self.assertFalse(policy.is_affirmed({"mature_affirmed_at": None}))
        self.assertTrue(policy.is_affirmed({"mature_affirmed_at": 1.0}))


if __name__ == "__main__":
    unittest.main()
