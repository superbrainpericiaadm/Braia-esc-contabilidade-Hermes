import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from configure_multi_ai import build_config, connected, atomic_write, validate_catalog, discover_catalog
import configure_multi_ai as configure

spec = importlib.util.spec_from_file_location("claude_login", Path(__file__).parents[1] / "skills/braia-claude-login/scripts/claude_login.py")
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


class MultiAI(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "openai-codex": {"models": {
                "gpt-5.6-luna": {"tier": "routine", "efforts": ["low", "medium", "high"], "default_effort": "medium"},
                "gpt-5.6-terra": {"tier": "normal", "efforts": ["low", "medium", "high"], "default_effort": "medium"},
                "gpt-5.6-sol": {"tier": "complex", "efforts": ["low", "medium", "high"], "default_effort": "medium"},
                "gpt-6-astra": {"tier": "exceptional", "efforts": ["medium", "high"], "default_effort": "medium"},
            }},
            "anthropic": {"models": {
                "claude-haiku-4-5": {"tier": "routine", "efforts": [], "default_effort": None},
                "claude-sonnet-5": {"tier": "normal", "efforts": ["medium", "high"], "default_effort": "medium"},
                "claude-opus-5": {"tier": "complex", "efforts": ["medium", "high"], "default_effort": "medium"},
            }},
        }

    def build(self, original, codex=True, claude=True):
        return build_config(original, codex, claude, catalog=self.catalog)

    def test_second_subscription_preserves_either_initial_choice(self):
        for provider, model in [("anthropic", "claude-opus-5"), ("openai-codex", "gpt-5.6-sol")]:
            original = {"model": {"provider": provider, "default": model},
                        "skills": {"config": {"braia": {"owner_name": "Example"}}},
                        "auxiliary": {"vision": {"model": "existing"}},
                        "delegation": {"max_iterations": 250}, "display": {"busy_input_mode": "queue"}}
            before = copy.deepcopy(original)
            single, _ = self.build(original, provider == "openai-codex", provider == "anthropic")
            cfg, mode = self.build(single)
            self.assertEqual(mode, "DYNAMIC_ROUTING")
            self.assertEqual(cfg["model"]["provider"], provider)
            self.assertEqual(cfg["model"]["default"], model)
            self.assertEqual(cfg["braia_routing"]["initial_provider"], provider)
            self.assertEqual(cfg["braia_routing"]["initial_model"], model)
            self.assertEqual(cfg["braia_routing"]["initial_tier"], "complex")
            self.assertNotIn("provider", cfg["delegation"])
            self.assertNotIn("model", cfg["delegation"])
            self.assertEqual(set(cfg["braia_routing"]["providers"]), {"anthropic", "openai-codex"})
            self.assertTrue(cfg["braia_routing"]["learning"]["enabled"])
            self.assertEqual(cfg["delegation"]["child_timeout_seconds"], 0)
            self.assertEqual(cfg["delegation"]["max_iterations"], 250)
            self.assertEqual(cfg["skills"], before["skills"])
            self.assertEqual(cfg["auxiliary"]["vision"], before["auxiliary"]["vision"])
            self.assertEqual(original, before)
            self.assertEqual(self.build(cfg)[0], cfg)

    def test_one_subscription_uses_normal_default_without_second_login(self):
        for codex, claude, provider, model in [(True, False, "openai-codex", "gpt-5.6-terra"), (False, True, "anthropic", "claude-sonnet-5")]:
            cfg, _ = self.build({}, codex, claude)
            self.assertEqual(cfg["model"]["provider"], provider)
            self.assertEqual(cfg["model"]["default"], model)
            self.assertEqual(set(cfg["braia_routing"]["providers"]), {provider})
            self.assertNotIn("fallback_providers", cfg)
            self.assertTrue(all(x["provider"] == provider for x in cfg["model_aliases"].values()))

    def test_updater_recursive_template_merge_preserves_claude_customer_choice(self):
        import yaml
        template = yaml.safe_load((Path(__file__).parents[1] / "templates/config.yaml").read_text(encoding="utf-8"))
        customer = {"model": {"provider": "anthropic", "default": "claude-opus-5"},
                    "skills": {"config": {"braia": {"owner_name": "Keep owner"}}}}
        def merge_missing(target, defaults):
            for key, value in defaults.items():
                if key not in target:
                    target[key] = copy.deepcopy(value)
                elif isinstance(target[key], dict) and isinstance(value, dict):
                    merge_missing(target[key], value)
        merge_missing(customer, template)
        self.assertEqual(customer["braia_routing"]["initial_provider"], "openai-codex")
        cfg, _ = self.build(customer)
        self.assertEqual(cfg["braia_routing"]["initial_provider"], "anthropic")
        self.assertEqual(cfg["braia_routing"]["initial_model"], "claude-opus-5")
        self.assertEqual(cfg["braia_routing"]["initial_tier"], "complex")
        self.assertTrue(cfg["braia_routing"]["initialized"])
        self.assertEqual(cfg["model"]["default"], "claude-opus-5")
        self.assertEqual(cfg["skills"]["config"]["braia"]["owner_name"], "Keep owner")
        # A later manual current-model change must not rewrite the saved origin.
        cfg["model"].update(provider="openai-codex", default="gpt-5.6-sol")
        merge_missing(cfg, template)
        second, _ = self.build(cfg)
        self.assertEqual(second["braia_routing"]["initial_provider"], "anthropic")
        self.assertEqual(second["braia_routing"]["initial_model"], "claude-opus-5")

    def test_unavailable_account_does_not_erase_initial_choice(self):
        cfg, _ = self.build({"model": {"provider": "anthropic", "default": "claude-opus-5"}})
        reduced, _ = self.build(cfg, True, False)
        self.assertEqual(reduced["model"], {**cfg["model"], "aliases": reduced["model"]["aliases"]})
        self.assertEqual(reduced["braia_routing"]["initial_provider"], "anthropic")
        self.assertEqual(reduced["braia_routing"]["initial_tier"], "complex")
        self.assertEqual(set(reduced["braia_routing"]["providers"]), {"openai-codex"})

    def test_saved_model_without_provider_retains_its_family_and_choice(self):
        cfg, _ = self.build({"model": {"default": "claude-opus-5"}})
        self.assertEqual(cfg["model"]["provider"], "anthropic")
        self.assertEqual(cfg["model"]["default"], "claude-opus-5")
        self.assertEqual(cfg["braia_routing"]["initial_model"], "claude-opus-5")
        with self.assertRaisesRegex(ValueError, "explicit subscription provider"):
            self.build({"model": {"default": "unknown-saved-model"}})

    def test_reconnect_preserves_main_and_alternative_for_both_providers(self):
        choices = {"anthropic": "claude-opus-5", "openai-codex": "gpt-5.6-sol"}
        for main in choices:
            for disconnected_provider in choices:
                with self.subTest(main=main, disconnected=disconnected_provider):
                    cfg, _ = self.build({"model": {"provider": main, "default": choices[main]}})
                    policy = cfg["braia_routing"]
                    for provider, model in choices.items():
                        policy["providers"][provider]["orchestrator_model"] = model
                    policy.update(manual_provider=disconnected_provider, manual_tier="complex",
                                  history_path="routing-history.sqlite", policy_version="customer-v2")
                    policy["learning"].update(min_samples=23, cooldown_seconds=172800)
                    policy["fallback"].update(max_attempts=1)
                    before = copy.deepcopy(cfg)
                    reduced, _ = self.build(cfg, disconnected_provider != "openai-codex",
                                            disconnected_provider != "anthropic")
                    self.assertEqual(cfg, before)
                    self.assertNotIn(disconnected_provider, reduced["braia_routing"]["providers"])
                    self.assertTrue(all(item["provider"] != disconnected_provider
                                        for item in reduced["model_aliases"].values()))
                    self.assertEqual(reduced["braia_routing"]["provider_preferences"][disconnected_provider],
                                     {"orchestrator_model": choices[disconnected_provider]})
                    self.assertEqual(self.build(reduced, disconnected_provider != "openai-codex",
                                                disconnected_provider != "anthropic")[0], reduced)
                    restored, _ = self.build(reduced)
                    for provider, model in choices.items():
                        self.assertEqual(restored["braia_routing"]["providers"][provider]["orchestrator_model"], model)
                    for key in ("initial_provider", "initial_model", "initial_tier", "manual_provider",
                                "manual_tier", "learning", "fallback", "history_path", "policy_version"):
                        self.assertEqual(reduced["braia_routing"][key], before["braia_routing"][key])
                        self.assertEqual(restored["braia_routing"][key], before["braia_routing"][key])
                    self.assertEqual(self.build(restored)[0], restored)
                    self.assertEqual(self.build(restored, disconnected_provider != "openai-codex",
                                                disconnected_provider != "anthropic")[0], reduced)

    def test_reconnect_exact_anthropic_main_codex_sol_manual_regression(self):
        cfg, _ = self.build({"model": {"provider": "anthropic", "default": "claude-sonnet-5"}})
        cfg["braia_routing"]["providers"]["openai-codex"]["orchestrator_model"] = "gpt-5.6-sol"
        cfg["braia_routing"]["manual_provider"] = "openai-codex"
        # Profiles written before preferences existed must migrate too.
        cfg["braia_routing"].pop("provider_preferences")
        reduced, _ = self.build(cfg, False, True)
        restored, _ = self.build(reduced)
        self.assertEqual(reduced["braia_routing"]["manual_tier"], "complex")
        self.assertEqual(restored["braia_routing"]["manual_tier"], "complex")
        self.assertEqual(restored["braia_routing"]["providers"]["openai-codex"]["orchestrator_model"], "gpt-5.6-sol")

    def test_reconnect_missing_or_unavailable_model_fails_without_mutation(self):
        choices = {"anthropic": "claude-opus-5", "openai-codex": "gpt-5.6-sol"}
        for main in choices:
            for provider, model in choices.items():
                for missing in (True, False):
                    with self.subTest(main=main, provider=provider, missing=missing):
                        cfg, _ = self.build({"model": {"provider": main, "default": choices[main]}})
                        cfg["braia_routing"]["providers"][provider]["orchestrator_model"] = model
                        reduced, _ = self.build(cfg, provider != "openai-codex", provider != "anthropic")
                        before = copy.deepcopy(reduced)
                        catalog = copy.deepcopy(self.catalog)
                        if missing:
                            del catalog[provider]["models"][model]
                        else:
                            catalog[provider]["models"][model]["availability"] = "unavailable"
                        with self.assertRaisesRegex(ValueError, "SELECT_AVAILABLE_MODEL"):
                            build_config(reduced, True, True, catalog)
                        self.assertEqual(reduced, before)

    def test_preferences_copy_only_model_and_refresh_catalog_on_reconnect(self):
        cfg, _ = self.build({})
        cfg["braia_routing"]["providers"]["anthropic"].update(
            orchestrator_model="claude-opus-5", access_token="fake-access-secret",
            refresh_token="fake-refresh-secret", api_key="fake-api-secret")
        cfg["braia_routing"]["provider_preferences"]["anthropic"]["credentials"] = "fake-nested-secret"
        reduced, _ = self.build(cfg, True, False)
        self.assertEqual(reduced["braia_routing"]["provider_preferences"]["anthropic"],
                         {"orchestrator_model": "claude-opus-5"})
        self.catalog["anthropic"]["models"]["claude-opus-5"].update(
            efforts=["high"], default_effort="high", availability="available")
        restored, _ = self.build(reduced)
        model = restored["braia_routing"]["providers"]["anthropic"]["models"]["claude-opus-5"]
        self.assertEqual(model["efforts"], ["high"])
        self.assertEqual(model["default_effort"], "high")
        for candidate in (reduced, restored):
            self.assertNotIn("secret", str(candidate))

    def test_factory_marker_ignores_preferences_and_preserves_customer_origin(self):
        original = {"model": {"provider": "anthropic", "default": "claude-opus-5"},
                    "braia_routing": {"initialized": False, "initial_provider": "openai-codex",
                                      "initial_model": "gpt-5.6-terra", "initial_tier": "normal",
                                      "provider_preferences": {"openai-codex": {"orchestrator_model": "gpt-6-astra"}},
                                      "providers": {"anthropic": {"orchestrator_model": "claude-sonnet-5"}}}}
        cfg, _ = self.build(original)
        self.assertEqual(cfg["braia_routing"]["initial_provider"], "anthropic")
        self.assertEqual(cfg["braia_routing"]["initial_model"], "claude-opus-5")
        self.assertEqual(cfg["braia_routing"]["initial_tier"], "complex")
        self.assertEqual(cfg["braia_routing"]["provider_preferences"]["openai-codex"],
                         {"orchestrator_model": "gpt-5.6-terra"})
        self.assertEqual(self.build(cfg)[0], cfg)

    def test_current_choice_supersedes_saved_preferences(self):
        cfg, _ = self.build({})
        cfg["model"]["default"] = "gpt-5.6-sol"
        updated, _ = self.build(cfg)
        self.assertEqual(updated["braia_routing"]["provider_preferences"]["openai-codex"],
                         {"orchestrator_model": "gpt-5.6-sol"})
        self.assertEqual(updated["braia_routing"]["initial_model"], "gpt-5.6-terra")
        self.assertEqual(self.build(updated)[0], updated)

    def test_migration_removes_legacy_pins_and_mini_compression(self):
        cfg, _ = self.build({"model": {"provider": "openai-codex", "default": "gpt-5.6-sol", "api_key": "fake"},
                            "delegation": {"provider": "anthropic", "model": "claude-opus-5", "fallback_providers": [{"provider": "anthropic"}]},
                            "fallback_providers": [], "fallback_model": "old", "fallback": "old",
                            "auxiliary": {"compression": {"model": "gpt-5.4-mini"}}})
        self.assertNotIn("api_key", cfg["model"])
        self.assertNotIn("model", cfg["delegation"])
        self.assertNotIn("fallback_providers", cfg["delegation"])
        self.assertNotIn("compression", cfg["auxiliary"])
        for key in ("fallback_providers", "fallback_model", "fallback"):
            self.assertNotIn(key, cfg)

    def test_manual_preference_learning_and_custom_compression_preserved(self):
        original = {"braia_routing": {"initial_provider": "anthropic", "manual_provider": "openai-codex",
                                    "learning": {"enabled": False, "min_samples": 20}},
                    "model": {"provider": "anthropic", "default": "claude-opus-5"},
                    "auxiliary": {"compression": {"model": "deliberate-custom"}}}
        cfg, _ = self.build(original)
        self.assertEqual(cfg["braia_routing"]["manual_provider"], "openai-codex")
        self.assertFalse(cfg["braia_routing"]["learning"]["enabled"])
        self.assertEqual(cfg["braia_routing"]["learning"]["min_samples"], 20)
        self.assertEqual(cfg["auxiliary"], original["auxiliary"])

    def test_catalog_rejects_invalid_capabilities(self):
        for name, entry in [("gpt-voice", {"tier": "unknown", "efforts": []}),
                            ("gpt-5.6-terra", {"tier": "normal", "efforts": ["invalid"]})]:
            catalog = copy.deepcopy(self.catalog)
            catalog["openai-codex"]["models"][name] = entry
            with self.assertRaisesRegex(ValueError, "INVALID_CATALOG"):
                build_config({}, True, False, catalog=catalog)

    def test_catalog_rejects_haiku_effort_and_missing_normal(self):
        self.catalog["anthropic"]["models"]["claude-haiku-4-5"]["efforts"] = ["low"]
        with self.assertRaisesRegex(ValueError, "Haiku"):
            self.build({})
        del self.catalog["openai-codex"]["models"]["gpt-5.6-terra"]
        with self.assertRaisesRegex(ValueError, "normal model"):
            self.build({}, True, False)

    def test_unavailable_optional_model_excluded_without_promoting(self):
        self.catalog["openai-codex"]["models"]["gpt-6-astra"]["availability"] = "unavailable"
        cfg, _ = self.build({})
        self.assertNotIn("gpt-6-astra", cfg["braia_routing"]["providers"]["openai-codex"]["models"])
        self.assertEqual(cfg["model"]["default"], "gpt-5.6-terra")

    def test_unavailable_saved_model_fails_without_silent_replacement(self):
        original = {"model": {"provider": "openai-codex", "default": "gpt-5.4-mini"}}
        with self.assertRaisesRegex(ValueError, "SELECT_AVAILABLE_MODEL"):
            self.build(original)
        self.assertEqual(original["model"]["default"], "gpt-5.4-mini")

    def test_discovery_supplements_stale_native_ids_without_inventing_entitlement(self):
        from types import ModuleType
        native = ModuleType("hermes_cli.models")
        native.curated_models_for_provider = lambda p: [("gpt-5.6-terra", ""), ("gpt-5.4-mini", ""), ("gpt-new-guess", "")]
        with patch.dict(sys.modules, {"hermes_cli.models": native}):
            catalog = discover_catalog(["openai-codex"])
        self.assertEqual(set(catalog["openai-codex"]["models"]), {"gpt-5.6-terra", "gpt-5.4-mini", "gpt-new-guess"})
        self.assertEqual(catalog["openai-codex"]["models"]["gpt-5.6-terra"]["availability"], "unverified")
        self.assertEqual(catalog["openai-codex"]["models"]["gpt-5.6-terra"]["source"], "hermes")
        self.assertEqual(catalog["openai-codex"]["models"]["gpt-5.4-mini"]["tier"], "routine")
        self.assertEqual(catalog["openai-codex"]["models"]["gpt-new-guess"]["tier"], "normal")

    def test_live_chatgpt_catalog_accepts_gpt_5_5_as_orchestrator(self):
        catalog = {"openai-codex": {"models": {
            "gpt-5.5": {"tier": "normal", "efforts": ["none", "low", "medium", "high", "xhigh"],
                        "default_effort": "medium", "source": "hermes"},
        }}}
        cfg, _ = build_config({"model": {"provider": "openai-codex", "default": "gpt-5.5"}},
                              True, False, catalog)
        self.assertEqual(cfg["model"]["default"], "gpt-5.5")
        self.assertEqual(cfg["braia_routing"]["providers"]["openai-codex"]["orchestrator_model"], "gpt-5.5")

    def test_offline_catalog_keeps_opus_and_optional_fable_without_second_login(self):
        from types import ModuleType
        native = ModuleType("hermes_cli.models")
        native.curated_models_for_provider = lambda p: (_ for _ in ()).throw(TimeoutError())
        with patch.dict(sys.modules, {"hermes_cli.models": native}):
            cfg, _ = build_config({"model": {"provider": "anthropic", "default": "claude-opus-5"}}, False, True)
        models = cfg["braia_routing"]["providers"]["anthropic"]["models"]
        self.assertEqual(models["claude-opus-5"]["source"], "reviewed_policy")
        self.assertEqual(models["claude-fable-5-1"]["availability"], "unverified")
        self.assertEqual(cfg["model"]["default"], "claude-opus-5")

    def test_catalog_cannot_copy_secrets_into_config(self):
        self.catalog["anthropic"]["models"]["claude-opus-5"]["access_token"] = "fake-secret"
        cfg, _ = self.build({})
        self.assertNotIn("fake-secret", str(cfg))

    def test_no_accounts_and_invalid_config_do_not_mutate(self):
        for cfg, codex, claude in [({"model": []}, True, False), ({"custom": 1}, False, False)]:
            before = copy.deepcopy(cfg)
            with self.assertRaises(ValueError):
                build_config(cfg, codex, claude)
            self.assertEqual(cfg, before)

    def test_cli_check_atomic_migration_and_auth_preservation(self):
        import json
        import os
        from types import ModuleType, SimpleNamespace
        import yaml
        native = ModuleType("agent.credential_pool")
        native.load_pool = lambda provider: SimpleNamespace(_entries=[SimpleNamespace(
            auth_type="oauth", access_token="fake", refresh_token="fake-refresh"
        )])
        runtime = ModuleType("agent.braia_routing")
        runtime.validate_policy = lambda policy: None
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.yaml"
            config.write_text("model:\n  provider: anthropic\n  default: claude-opus-5\n", encoding="utf-8")
            auth = root / "auth.json"
            auth.write_text(json.dumps({"credential_pool": {"retained": "fake-reference"}}))
            before, auth_before = config.read_bytes(), auth.read_bytes()
            with patch.dict(os.environ, {"HERMES_HOME": folder}), \
                 patch.dict(sys.modules, {"agent.credential_pool": native, "agent.braia_routing": runtime}), \
                 patch.object(configure, "discover_catalog", return_value=self.catalog), \
                 patch.object(sys, "argv", ["configure_multi_ai.py", "--check"]):
                configure.main()
                self.assertEqual(config.read_bytes(), before)
                self.assertEqual(list(root.glob("*.bak-*")), [])
                sys.argv = ["configure_multi_ai.py"]
                configure.main()
                cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
                self.assertEqual(cfg["model"]["default"], "claude-opus-5")
                self.assertEqual(cfg["braia_routing"]["initial_provider"], "anthropic")
                backups = list(root.glob("*.bak-*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), before)
                stamp = config.stat().st_mtime_ns
                configure.main()
                self.assertEqual(config.stat().st_mtime_ns, stamp)
                self.assertEqual(len(list(root.glob("*.bak-*"))), 1)
                self.assertEqual(auth.read_bytes(), auth_before)

    def test_cli_rejects_api_key_or_invalid_runtime_before_write(self):
        import json
        import os
        from types import ModuleType, SimpleNamespace
        native = ModuleType("agent.credential_pool")
        runtime = ModuleType("agent.braia_routing")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config.yaml"
            config.write_text("model: {}\n", encoding="utf-8")
            (root / "auth.json").write_text(json.dumps({"credential_pool": {}}))
            before = config.read_bytes()
            for api_key in (True, False):
                native.load_pool = lambda provider: SimpleNamespace(_entries=[SimpleNamespace(
                    auth_type="api_key" if api_key else "oauth", access_token="fake")])
                runtime.validate_policy = lambda policy: (_ for _ in ()).throw(ValueError("runtime policy rejected"))
                with patch.dict(os.environ, {"HERMES_HOME": folder}), \
                     patch.dict(sys.modules, {"agent.credential_pool": native, "agent.braia_routing": runtime}), \
                     patch.object(configure, "discover_catalog", return_value=self.catalog), \
                     patch.object(sys, "argv", ["configure_multi_ai.py"]):
                    with self.assertRaises(ValueError):
                        configure.main()
                self.assertEqual(config.read_bytes(), before)
                self.assertEqual(list(root.glob("*.bak-*")), [])

    def test_refreshable_and_revoked_credentials(self):
        self.assertFalse(connected({"access_token": "test", "expires_at_ms": 1000}, now=2000))
        self.assertTrue(connected({"access_token": "test", "expires_at_ms": 1000, "refresh_token": "test"}, now=2000))
        self.assertFalse(connected({"access_token": "test", "refresh_token": "test", "last_status": "dead"}))
        self.assertFalse(connected({"id": "reference-only"}))

    def test_atomic_write_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "config.yaml"
            self.assertTrue(atomic_write(p, "first"))
            stamp = p.stat().st_mtime_ns
            self.assertFalse(atomic_write(p, "first"))
            self.assertEqual(p.stat().st_mtime_ns, stamp)
            self.assertTrue(atomic_write(p, "second"))
            self.assertEqual(p.read_text(), "second")

    def test_pkce_rejects_mismatch_missing_state_and_expiry(self):
        ctx = {"created_at": 1000, "state": "expected"}
        self.assertEqual(login.validate_code("code#expected", ctx, now=1001), ("code", "expected"))
        for raw, now in [("code#wrong", 1001), ("code", 1001), ("code#expected", 2000), ("code#expected", 999)]:
            with self.assertRaises(ValueError):
                login.validate_code(raw, ctx, now=now)

    def test_exchange_uses_subscription_oauth_and_consumes_context(self):
        from types import SimpleNamespace
        import io
        import json
        import time
        native = SimpleNamespace(_OAUTH_CLIENT_ID='client', _OAUTH_REDIRECT_URI='https://example.test/callback',
                                 _OAUTH_TOKEN_URLS=['https://example.test/oauth/token'], _OAUTH_TOKEN_USER_AGENT='test')
        with tempfile.TemporaryDirectory() as folder:
            ctx = Path(folder)/'context.json'
            ctx.write_text(json.dumps({'created_at':time.time(),'state':'expected','verifier':'test-verifier'}))
            with patch.object(login,'helpers',return_value=(native,None)), patch.object(login,'context_path',return_value=ctx), \
                 patch.object(login,'persist') as persist, patch.object(login,'configure_routes') as configure, patch.object(login.urllib.request,'urlopen',return_value=io.BytesIO(b'{"access_token":"fake-oauth","refresh_token":"fake-refresh","expires_in":3600}')) as request:
                login.exchange('code#expected')
                payload=json.loads(request.call_args.args[0].data)
                self.assertEqual(payload['grant_type'],'authorization_code')
                self.assertEqual(payload['code_verifier'],'test-verifier')
                self.assertEqual(persist.call_args.args[0]['refresh_token'],'fake-refresh')
                self.assertFalse(ctx.exists())
                configure.assert_called_once()

    def test_repeated_start_preserves_link_until_expiry(self):
        from types import SimpleNamespace
        import io
        import contextlib
        native=SimpleNamespace(_generate_pkce=lambda:('verifier','challenge'),_OAUTH_CLIENT_ID='client',
                               _OAUTH_REDIRECT_URI='https://example.test/callback',_OAUTH_SCOPES='user:inference')
        with tempfile.TemporaryDirectory() as folder:
            ctx=Path(folder)/'context.json'
            with patch.object(login,'helpers',return_value=(native,atomic_write)),patch.object(login,'context_path',return_value=ctx):
                first=io.StringIO();second=io.StringIO()
                with contextlib.redirect_stdout(first): login.start()
                with contextlib.redirect_stdout(second): login.start()
                self.assertEqual(first.getvalue(),second.getvalue())
                saved=ctx.read_bytes()
                with patch.object(login.time,'time',return_value=9999999999),contextlib.redirect_stdout(io.StringIO()): login.start()
                self.assertNotEqual(ctx.read_bytes(),saved)

    def test_config_failure_does_not_replay_authorization_code(self):
        from types import SimpleNamespace
        import io,json,time
        native=SimpleNamespace(_OAUTH_CLIENT_ID='client',_OAUTH_REDIRECT_URI='https://example.test/callback',
                               _OAUTH_TOKEN_URLS=['https://example.test/oauth/token'],_OAUTH_TOKEN_USER_AGENT='test')
        with tempfile.TemporaryDirectory() as folder:
            ctx=Path(folder)/'context.json'
            ctx.write_text(json.dumps({'created_at':time.time(),'state':'expected','verifier':'test'}))
            with patch.object(login,'helpers',return_value=(native,None)),patch.object(login,'context_path',return_value=ctx), \
                 patch.object(login,'persist') as persist,patch.object(login,'configure_routes',side_effect=ValueError('bad config')), \
                 patch.object(login.urllib.request,'urlopen',return_value=io.BytesIO(b'{"access_token":"fake-oauth"}')):
                with self.assertRaises(ValueError): login.exchange('code#expected')
                persist.assert_called_once()
                self.assertFalse(ctx.exists())

    def test_cancel_preserves_connected_account_files(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx=Path(folder)/'context.json';ctx.write_text('{}')
            auth=Path(folder)/'auth.json';auth.write_text('existing-account-placeholder')
            with patch.object(login,'context_path',return_value=ctx): login.cancel()
            self.assertFalse(ctx.exists())
            self.assertEqual(auth.read_text(),'existing-account-placeholder')

    def test_reauthorization_revives_only_its_pool_entry_and_preserves_other_accounts(self):
        from types import ModuleType, SimpleNamespace
        from unittest.mock import Mock
        auth = ModuleType("hermes_cli.auth")
        pool = ModuleType("agent.credential_pool")
        existing = [
            {"id": "own-pkce", "source": "hermes_pkce", "label": "Keep label", "priority": 7,
             "last_status": "dead", "disabled": True, "revoked": True},
            {"id": "other-account", "source": "other-source", "refresh_token": "fake-other"},
        ]
        before = copy.deepcopy(existing)
        auth.read_credential_pool = Mock(return_value=existing)
        auth.write_credential_pool = Mock()
        auth.unsuppress_credential_source = Mock()
        pool.load_pool = Mock()
        native = SimpleNamespace(_write_hermes_oauth_credentials=Mock())
        with patch.object(login, "helpers", return_value=(native, None)), \
             patch.dict(sys.modules, {"hermes_cli.auth": auth, "agent.credential_pool": pool}):
            login.persist({"access_token": "fake-new", "refresh_token": "fake-new-refresh"})
        written = auth.write_credential_pool.call_args.args[1]
        own = next(item for item in written if item["id"] == "own-pkce")
        self.assertEqual(own["priority"], 7)
        self.assertEqual(own["label"], "Keep label")
        self.assertTrue(connected(own))
        self.assertEqual(next(item for item in written if item["id"] == "other-account"), before[1])
        self.assertEqual(existing, before)
        auth.unsuppress_credential_source.assert_called_once_with("anthropic", "hermes_pkce")

    def test_bad_state_never_contacts_provider_or_writes_credentials(self):
        import json
        import time
        with tempfile.TemporaryDirectory() as folder:
            ctx=Path(folder)/'context.json'
            ctx.write_text(json.dumps({'created_at':time.time(),'state':'expected','verifier':'test'}))
            with patch.object(login,'helpers',return_value=(None,None)), patch.object(login,'context_path',return_value=ctx), \
                 patch.object(login,'persist') as persist, patch.object(login.urllib.request,'urlopen') as request:
                with self.assertRaises(ValueError): login.exchange('code#wrong')
                request.assert_not_called()
                persist.assert_not_called()
                self.assertTrue(ctx.exists())


if __name__ == "__main__":
    unittest.main()
