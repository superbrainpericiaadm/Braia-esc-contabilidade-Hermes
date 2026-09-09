#!/usr/bin/env python3
"""Select Braia routes from connected credentials, preserving unrelated settings."""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
import time

import yaml

PROVIDERS = ("openai-codex", "anthropic")
POLICY_VERSION = "braia-routing-v1"
# Reviewed candidates supplement stale Hermes catalogs, never treated as proof
# of an account entitlement. Unknown models require a reviewed policy update.
MODEL_POLICY = {
    "openai-codex": {
        "gpt-5.6-luna": "routine", "gpt-5.6-terra": "normal",
        "gpt-5.6-sol": "complex", "gpt-6-astra": "exceptional",
    },
    "anthropic": {
        "claude-haiku-4-5": "routine", "claude-haiku-4-5-20251001": "routine",
        "claude-sonnet-5": "normal", "claude-opus-5": "complex",
        "claude-fable-5": "exceptional", "claude-fable-5-1": "exceptional",
    },
}
TIERS = {"routine", "normal", "complex", "exceptional"}
LEARNING_DEFAULTS = dict(enabled=True, min_samples=8, window_days=30,
                         promotion_margin=0.15, cooldown_seconds=86400,
                         sustained_windows=2)


def discover_catalog(providers):
    from hermes_cli.models import curated_models_for_provider
    catalog = {}
    for provider in providers:
        try:
            ids = {item[0] for item in curated_models_for_provider(provider)}
        except Exception:
            # Discovery may fail offline; reviewed candidates remain unverified.
            ids = set()
        models = {}
        for model, tier in MODEL_POLICY[provider].items():
            efforts = [] if tier == "routine" and provider == "anthropic" else ["low", "medium", "high"]
            models[model] = dict(tier=tier, efforts=efforts,
                                 default_effort="medium" if efforts else None,
                                 availability="unverified",
                                 source="hermes" if model in ids else "reviewed_policy")
        catalog[provider] = {"models": models}
    return catalog


def validate_catalog(catalog, providers):
    if not isinstance(catalog, dict):
        raise ValueError("INVALID_CATALOG: expected provider mapping")
    result = {}
    for provider in providers:
        block = catalog.get(provider)
        if not isinstance(block, dict) or not isinstance(block.get("models"), dict):
            raise ValueError("INVALID_CATALOG: missing connected provider models")
        models = {}
        for model, raw in block["models"].items():
            if not isinstance(model, str) or not isinstance(raw, dict):
                raise ValueError("INVALID_CATALOG: model entry")
            # Exact names, known provider and reviewed capabilities are required.
            if model not in MODEL_POLICY[provider] or raw.get("tier") != MODEL_POLICY[provider][model]:
                raise ValueError("INVALID_CATALOG: unknown model or mismatched capability")
            efforts = raw.get("efforts")
            if not isinstance(efforts, list) or any(e not in {"low", "medium", "high", "max", "xhigh"} for e in efforts):
                raise ValueError("INVALID_CATALOG: effort capabilities")
            if provider == "anthropic" and raw["tier"] == "routine" and efforts:
                raise ValueError("INVALID_CATALOG: Haiku has no adaptive effort")
            if raw.get("default_effort") is not None and raw["default_effort"] not in efforts:
                raise ValueError("INVALID_CATALOG: default effort")
            availability = raw.get("availability", "unverified")
            if availability not in {"unverified", "available", "unavailable"}:
                raise ValueError("INVALID_CATALOG: availability")
            if availability == "unavailable":
                continue
            # Copy an explicit metadata allowlist: never persist arbitrary input
            # fields that could contain account secrets or customer content.
            models[model] = {k: copy.deepcopy(raw[k]) for k in ("tier", "efforts", "default_effort") if k in raw}
            models[model].setdefault("default_effort", None)
            models[model]["availability"] = availability
            models[model]["source"] = (raw["source"] if raw.get("source") in {"hermes", "reviewed_policy"}
                                       else "installation-catalog")
        if not any(m["tier"] == "normal" for m in models.values()):
            raise ValueError("INVALID_CATALOG: connected provider needs a normal model")
        result[provider] = {"models": models}
    return result


def connected(entry, now=None):
    if not isinstance(entry, dict):
        return False
    if entry.get("disabled") or entry.get("revoked"):
        return False
    if str(entry.get("last_status", "")).lower() in {"dead", "revoked", "disabled", "invalid", "relogin_required"}:
        return False
    if not entry.get("access_token"):
        return False
    expiry = entry.get("expires_at_ms")
    if isinstance(expiry, (int, float)):
        expiry /= 1000
    else:
        expiry = entry.get("expires_at")
        if isinstance(expiry, str):
            try:
                expiry = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00")).timestamp()
            except ValueError:
                raise ValueError("Invalid credential expiry") from None
    # An expired access token with a refresh token is still a connected account.
    return not isinstance(expiry, (int, float)) or expiry > (now or time.time()) + 60 or bool(entry.get("refresh_token"))


def build_config(original, has_codex, has_claude, catalog=None):
    providers = [p for p, active in zip(PROVIDERS, (has_codex, has_claude)) if active]
    if not providers:
        raise ValueError("NO_CONNECTED_PROVIDER: connect ChatGPT or Claude first")
    cfg = copy.deepcopy(original)
    if not isinstance(cfg, dict):
        raise ValueError("Configuration must be a mapping")
    for key in ("model", "delegation", "auxiliary", "braia_routing"):
        if key in cfg and not isinstance(cfg[key], dict):
            raise ValueError(f"{key} must be a mapping")
        cfg.setdefault(key, {})
    policy = cfg["braia_routing"]
    if policy.get("schema_version", 1) != 1:
        raise ValueError("Unsupported routing schema")
    resolved = validate_catalog(catalog if catalog is not None else discover_catalog(providers), providers)
    chosen = cfg["model"].get("provider")
    if chosen is None and cfg["model"].get("default"):
        matches = [p for p in PROVIDERS if cfg["model"]["default"] in MODEL_POLICY[p]]
        if len(matches) != 1:
            raise ValueError("SELECT_AVAILABLE_MODEL: saved model needs an explicit subscription provider")
        chosen = matches[0]
        cfg["model"]["provider"] = chosen
    if chosen is not None and chosen not in PROVIDERS:
        raise ValueError("SUBSCRIPTION_ONLY: select Claude or ChatGPT subscription")
    from_template = policy.get("initialized") is False
    if from_template:
        # The updater merges missing template keys into an existing profile.
        # Its factory preference is not the customer's original selection.
        for key in ("initial_provider", "initial_model", "initial_tier"):
            policy.pop(key, None)
    initial = policy.get("initial_provider") or chosen or providers[0]
    if initial not in PROVIDERS:
        raise ValueError("Invalid initial provider")
    manual = policy.get("manual_provider")
    if manual is not None and manual not in PROVIDERS:
        raise ValueError("Invalid manual provider")
    previous = {} if from_template else policy.get("providers", {})
    if not isinstance(previous, dict):
        raise ValueError("Invalid routing providers")
    saved = {} if from_template else policy.get("provider_preferences", {})
    if not isinstance(saved, dict):
        raise ValueError("Invalid provider preferences")
    preferences = {}
    # Persist only the orchestration choice, never credentials, stale catalog
    # availability or arbitrary provider metadata. Live choices supersede the
    # saved snapshot; neither map makes a disconnected account available.
    for source in (saved, previous):
        for provider, entry in source.items():
            if provider not in PROVIDERS or not isinstance(entry, dict):
                raise ValueError("Invalid provider preference entry")
            model = entry.get("orchestrator_model")
            if model is not None:
                if not isinstance(model, str) or model not in MODEL_POLICY[provider]:
                    raise ValueError("SELECT_AVAILABLE_MODEL: invalid saved orchestrator")
                preferences[provider] = {"orchestrator_model": model}
    if chosen and cfg["model"].get("default"):
        preferences[chosen] = {"orchestrator_model": cfg["model"]["default"]}
    for provider, block in resolved.items():
        normal = next(m for m, details in block["models"].items() if details["tier"] == "normal")
        model = preferences.get(provider, {}).get("orchestrator_model") or normal
        if model not in block["models"]:
            raise ValueError("SELECT_AVAILABLE_MODEL: configured orchestrator is not in authorized catalog")
        block["orchestrator_model"] = model
        preferences[provider] = {"orchestrator_model": model}
    # Preserve the user's configured orchestrator even while its account is
    # unavailable. Runtime selects a connected fallback without rewriting it.
    if chosen is None:
        chosen = initial if initial in resolved else providers[0]
        cfg["model"].update(provider=chosen, default=resolved[chosen]["orchestrator_model"])
    elif not cfg["model"].get("default"):
        if chosen not in resolved:
            raise ValueError("SELECT_AVAILABLE_MODEL: disconnected initial provider has no saved model")
        cfg["model"]["default"] = resolved[chosen]["orchestrator_model"]
    policy.update(enabled=True, initialized=True, schema_version=1, initial_provider=initial,
                  providers=resolved, provider_preferences=preferences)
    policy.setdefault("initial_model", cfg["model"]["default"] if chosen == initial else
                      resolved.get(initial, {}).get("orchestrator_model"))
    policy.setdefault("initial_tier", MODEL_POLICY[initial].get(policy.get("initial_model")))
    if manual:
        manual_model = (cfg["model"]["default"] if chosen == manual else
                        preferences.get(manual, {}).get("orchestrator_model"))
        manual_tier = MODEL_POLICY[manual].get(manual_model)
        if manual_tier:
            policy["manual_tier"] = manual_tier
        elif manual == initial:
            policy["manual_tier"] = policy.get("initial_tier")
    policy.setdefault("policy_version", POLICY_VERSION)
    policy.setdefault("manual_provider", None)
    for name, defaults in (("learning", LEARNING_DEFAULTS), ("fallback", dict(enabled=True, max_attempts=2))):
        if name in policy and not isinstance(policy[name], dict):
            raise ValueError(f"Invalid {name} policy")
        policy.setdefault(name, {})
        for key, value in defaults.items():
            policy[name].setdefault(key, value)
    # No static child pin or legacy cross-family cascade survives migration.
    for key in ("provider", "model", "base_url", "api_key", "api_mode", "fallback_providers"):
        cfg["delegation"].pop(key, None)
    cfg["delegation"].update(child_timeout_seconds=0)
    cfg["delegation"].setdefault("max_concurrent_children", 3)
    cfg["delegation"].setdefault("max_spawn_depth", 1)
    cfg["delegation"].setdefault("subagent_auto_approve", True)
    for key in ("base_url", "api_key", "api_mode"):
        cfg["model"].pop(key, None)
    aliases = {model: {"provider": provider, "model": model}
               for provider, block in resolved.items() for model in block["models"]}
    cfg["model"]["aliases"] = {m: item["provider"] + "/" + m for m, item in aliases.items()}
    cfg["model_aliases"] = aliases
    for key in ("fallback_providers", "fallback_model", "fallback"):
        cfg.pop(key, None)
    # Compression follows the active parent instead of a removed mini default.
    # Keep deliberate custom auxiliary settings outside legacy migration.
    compression = cfg["auxiliary"].get("compression")
    if isinstance(compression, dict) and compression.get("model") == "gpt-5.4-mini":
        cfg["auxiliary"].pop("compression")
    return cfg, "DYNAMIC_ROUTING"


def atomic_write(path, content):
    path = Path(path)
    encoded = content if isinstance(content, bytes) else content.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return False
    old_stat = path.stat() if path.exists() else None
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(old_stat.st_mode) if old_stat else 0o600)
        if old_stat and hasattr(os, "chown"):
            os.chown(tmp, old_stat.st_uid, old_stat.st_gid)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--catalog", type=Path, help="Reviewed provider/model capability JSON; contains no credentials")
    args = parser.parse_args()
    profile = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    path = args.config or profile / "config.yaml"
    # Fail before any credential operation if a local file is corrupt.
    auth = json.loads((profile / "auth.json").read_text(encoding="utf-8"))
    if not isinstance(auth.get("credential_pool"), dict):
        raise ValueError("Invalid credential pool")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("Invalid YAML configuration")
    from agent.braia_routing import validate_policy
    from agent.credential_pool import load_pool
    available = []
    for provider in ("openai-codex", "anthropic"):
        # Native Hermes materializes reference credentials and preserves rotation.
        pool = load_pool(provider)
        if any(e.auth_type != "oauth" for e in pool._entries):
            raise ValueError("SUBSCRIPTION_ONLY: API keys are outside this installation's configuration")
        available.append(any(connected(vars(e)) for e in pool._entries))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8")) if args.catalog else None
    candidate, mode = build_config(cfg, *available, catalog=catalog)
    validate_policy(candidate["braia_routing"])
    text = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False)
    assert yaml.safe_load(text) == candidate
    changed = candidate != cfg
    if changed and not args.check:
        backup = path.with_name(path.name + ".bak-multi-ai-" + str(time.time_ns()))
        atomic_write(backup, path.read_bytes())
        atomic_write(path, text)
    print("MULTI_AI_CONFIGURED:", mode)
    print("CONFIG_WRITE:", "pending" if args.check and changed else "atomic" if changed else "unchanged")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", type(exc).__name__, str(exc))
        raise SystemExit(2)
