import hashlib
import os
from pathlib import Path
import pwd
import grp
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


def test_existing_marker_update_preserves_local_state_and_adds_operational_artifacts(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    backup = tmp_path / "backup"
    home.mkdir(); workspace.mkdir()
    (home / ".contadoria-kit-installed").write_text("existing\n")
    preserved = {
        ".env": "TELEGRAM_BOT_TOKEN=local-secret\n",
        "config.yaml": "model: {default: local-model}\n",
        "SOUL.md": "identidade local\n",
        "AGENTS.md": "orquestração local\n",
    }
    for name, value in preserved.items():
        (home / name).write_text(value)
    (home / "scripts").mkdir()
    (home / "scripts/configure_multi_ai.py").write_text("old configurator\n")
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    command = '. "$HELPER"; install_kit_artifacts "$SOURCE" "$HOME_DIR" "$OWNER" "$GROUP" "$BACKUP" "$WORKSPACE"'
    env = {**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
           "SOURCE": str(ROOT), "HOME_DIR": str(home), "OWNER": user, "GROUP": group,
           "BACKUP": str(backup), "WORKSPACE": str(workspace)}
    subprocess.run(["bash", "-c", command], check=True, env=env)
    for name, value in preserved.items():
        assert (home / name).read_text() == value
    assert (home / "scripts/configure_multi_ai.py").read_bytes() == (ROOT / "scripts/configure_multi_ai.py").read_bytes()
    assert (home / "skills/braia-claude-login/SKILL.md").is_file()
    assert any(path.name == "configure_multi_ai.py" for path in backup.rglob("configure_multi_ai.py"))


def test_existing_install_is_validated_before_mutation_and_coordinates_service():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    preflight = install.index('--check >/dev/null')
    assert preflight < install.index("apt-get update")
    assert preflight < install.index("bash \"${INSTALLER_TMP}\"")
    assert install.index("systemctl stop hermes-contadoria.service") < install.index("bash \"${INSTALLER_TMP}\"")
    assert "SERVICE_WAS_ACTIVE" in install and "systemctl start hermes-contadoria.service" in install


def test_fallback_documentation_is_self_contained():
    policy = (ROOT / "POLITICA-FALLBACK.md").read_text(encoding="utf-8")
    assert "docs/RELEASE-v1.0.11.md" not in policy
    assert "runtime-patches/README.md" in policy


def test_shell_scripts_parse():
    for path in (ROOT / "install.sh", ROOT / "configure.sh", ROOT / "bootstrap.sh", ROOT / "scripts/check-no-secrets.sh", ROOT / "scripts/install_kit_artifacts.sh"):
        subprocess.run(["bash", "-n", str(path)], check=True)
