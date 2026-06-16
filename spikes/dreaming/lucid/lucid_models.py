#!/usr/bin/env python3
"""Model registry accessor — resolves a role to its model from the single source of truth
(integrations/models/registry.json). Code asks `lucid_models.get("b2-vision")`; the audit panel
renders `all()`. Edit the registry, not the code, to change a model affiliation.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.environ.get(
    "AGENTOS_MODEL_REGISTRY",
    os.path.normpath(os.path.join(_HERE, "..", "..", "..", "integrations", "models", "registry.json")),
)


def _load():
    try:
        with open(REGISTRY) as f:
            return json.load(f)
    except Exception:
        return {"models": []}


def all():
    return _load().get("models", [])


def entry(role_id):
    for m in all():
        if m.get("id") == role_id:
            return m
    return None


def get(role_id, default=None):
    """The model name affiliated with `role_id`, or `default` if the registry lacks it."""
    e = entry(role_id)
    return e.get("model") if e else default
