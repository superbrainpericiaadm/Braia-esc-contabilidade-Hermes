import hashlib
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).parents[1]

AGENT_HASHES = {
    "agnaldo.md": "4e92ff931ab80c57afa9fce4d80ccb09a41c69d090e950eaececfc929e9bfe50",
    "angelica.md": "0aa120db35fd9fc96a4aaec6043dd5c33acb6df8f9d1eb8c80f2be81b129c936",
    "braia.md": "0aaeb096138205d94a3f0e6e41c52f830d2af2b85b065912144f6ea438da9adc",
    "daiane.md": "5e641e8c58b5bf15fcd8712422b1f21d4e32870687bad8433cd2b0e4a79ad5c0",
    "isaura.md": "b8b2df3ca9a3ada132f02a12d8e98a3c7384b8f3fa5791ca0801b96d7f64183c",
    "juliana.md": "0553c7846465cef38bb9a14af90700d9d2b40fc4e95c3cd0da323aa28e4ad9e4",
    "paulo.md": "e5d3e36b67c8c4515bb9630bde4e0dfedd3ace7c9e71b6dee044617127080f8c",
    "silvana.md": "3c99d1c05197a68cb6c0618dc6e8e2a3cc53c8def359424273810608307a3389",
    "victor.md": "8cb16d3f5d87f455432503f3faa384e86630ddb9e57322de73d1d789ecd94290",
}


def test_contabilidade_personas_are_preserved_byte_for_byte():
    observed = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "agents").glob("*.md"))
    }
    assert observed == AGENT_HASHES


def test_routing_policy_has_expected_invariants():
    cfg = yaml.safe_load((ROOT / "templates/config.yaml").read_text(encoding="utf-8"))
    routing = cfg["braia_routing"]
    assert routing["enabled"] is True
    assert routing["initialized"] is False
    assert routing["manual_provider"] is None
    assert routing["fallback"] == {"enabled": True, "max_attempts": 2}
    assert routing["learning"]["enabled"] is True
    assert cfg["delegation"]["child_timeout_seconds"] == 0
    assert "provider" not in cfg["delegation"]
    assert "model" not in cfg["delegation"]
    assert "fallback_providers" not in cfg


def test_contabil_identity_and_no_pericial_roster_import():
    soul = (ROOT / "templates/SOUL.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for name in ("Victor", "Daiane", "Agnaldo", "Silvana", "Paulo", "Isaura", "Angélica", "Juliana"):
        assert name in soul
        assert name in agents
    for imported in ("agente-rebeca-pericia", "agente-rogerio", "equipe-braia-pericias"):
        assert imported not in soul
        assert imported not in agents


def test_installer_pins_reviewed_runtime_and_installs_extension():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    configure = (ROOT / "configure.sh").read_text(encoding="utf-8")
    assert "29112bef099274229cadff79cdff7bf7b99c4b77" in install
    assert "apply_runtime_patch.py" in install
    assert "braia-claude-login" in install
    assert "configure_multi_ai.py" in configure
    assert "CODEX_ONLY" not in install + configure


def test_shell_scripts_parse():
    for path in (ROOT / "install.sh", ROOT / "configure.sh", ROOT / "bootstrap.sh", ROOT / "scripts/check-no-secrets.sh"):
        subprocess.run(["bash", "-n", str(path)], check=True)
