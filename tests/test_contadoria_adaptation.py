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


def test_contabil_identity_and_expected_public_roster():
    soul = (ROOT / "templates/SOUL.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for name in ("Victor", "Daiane", "Agnaldo", "Silvana", "Paulo", "Isaura", "Angélica", "Juliana"):
        assert name in soul
        assert name in agents
    assert set(AGENT_HASHES) == {path.name for path in (ROOT / "agents").glob("*.md")}


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
    stop = install.index("stop_service_for_update")
    begin = install.index("begin_kit_artifact_transaction")
    runtime_begin = install.index("begin_runtime_transaction")
    artifacts = install.index('install_kit_artifacts "${SOURCE_DIR}"')
    assert stop < begin < runtime_begin < artifacts
    assert artifacts < install.index("bash \"${INSTALLER_TMP}\"")
    assert install.index("restore_service_state ||") < install.index("INSTALL_SUCCEEDED=true")
    assert 'source "${SOURCE_DIR}/scripts/service_transaction.sh"' in install
    assert 'rollback_kit_artifacts' in install
    assert 'rollback_runtime_transaction' in install
    assert 'restore_service_state' in install


def _run_failed_service_transaction(tmp_path, initial_state):
    state = tmp_path / "state"
    events = tmp_path / "events"
    state.write_text(initial_state)
    script = r'''
set -Eeuo pipefail
SERVICE_NAME=hermes-contadoria.service
STATE_FILE="$1"
EVENTS_FILE="$2"
HELPER="$3"
systemctl() {
  printf '%s\n' "$*" >> "$EVENTS_FILE"
  case "$1" in
    is-active) [[ "$(<"$STATE_FILE")" == active ]] || return 3 ;;
    stop) printf 'inactive' > "$STATE_FILE" ;;
    start) printf 'active' > "$STATE_FILE" ;;
    *) return 64 ;;
  esac
}
. "$HELPER"
cleanup() {
  status=$?
  trap - EXIT
  restore_service_state
  exit "$status"
}
trap cleanup EXIT
capture_service_state
stop_service_for_update
exit 41
'''
    result = subprocess.run(
        ["bash", "-c", script, "transaction-test", str(state), str(events),
         str(ROOT / "scripts/service_transaction.sh")],
        text=True,
    )
    return result, state.read_text(), events.read_text().splitlines()


def test_failure_after_stop_restores_previously_active_service(tmp_path):
    result, state, events = _run_failed_service_transaction(tmp_path, "active")
    assert result.returncode == 41
    assert state == "active"
    assert "stop hermes-contadoria.service" in events
    assert "start hermes-contadoria.service" in events


def test_failure_preserves_previously_inactive_service(tmp_path):
    result, state, events = _run_failed_service_transaction(tmp_path, "inactive")
    assert result.returncode == 41
    assert state == "inactive"
    assert "stop hermes-contadoria.service" in events
    assert "start hermes-contadoria.service" not in events


def test_service_start_between_capture_and_stop_is_quiesced(tmp_path):
    state = tmp_path / "state"
    events = tmp_path / "events"
    state.write_text("inactive")
    script = r'''
set -u
SERVICE_NAME=hermes-contadoria.service
STATE_FILE="$1"; EVENTS_FILE="$2"; HELPER="$3"
systemctl() {
  printf '%s\n' "$*" >> "$EVENTS_FILE"
  case "$1" in
    is-active) [[ "$(<"$STATE_FILE")" == active ]] || return 3 ;;
    stop) printf inactive > "$STATE_FILE" ;;
    *) return 64 ;;
  esac
}
. "$HELPER"
capture_service_state
printf active > "$STATE_FILE"
stop_service_for_update
'''
    result = subprocess.run([
        "bash", "-c", script, "race-test", str(state), str(events),
        str(ROOT / "scripts/service_transaction.sh"),
    ], check=False)
    assert result.returncode == 0
    assert state.read_text() == "inactive"
    assert "stop hermes-contadoria.service" in events.read_text().splitlines()


def test_failed_update_rolls_back_artifacts_before_service_can_observe_them(tmp_path):
    state = tmp_path / "state"
    source = tmp_path / "new-artifact"
    target = tmp_path / "live-artifact"
    source_two = tmp_path / "new-artifact-two"
    target_two = tmp_path / "live-artifact-two"
    backup = tmp_path / "backup"
    observed = tmp_path / "observed-on-start"
    state.write_text("active")
    source.write_text("new-partial")
    target.write_text("old-consistent")
    source_two.write_text("new-partial-two")
    target_two.write_text("old-consistent-two")
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    script = r'''
set -Eeuo pipefail
SERVICE_NAME=hermes-contadoria.service
STATE_FILE="$1"; SOURCE_FILE="$2"; TARGET_FILE="$3"; SOURCE_TWO="$4"; TARGET_TWO="$5"
BACKUP="$6"; OBSERVED="$7"; OWNER="$8"; GROUP="$9"; SERVICE_HELPER="${10}"; ARTIFACT_HELPER="${11}"
systemctl() {
  case "$1" in
    is-active) [[ "$(<"$STATE_FILE")" == active ]] || return 3 ;;
    stop) printf 'inactive' > "$STATE_FILE" ;;
    start) printf '%s|%s' "$(<"$TARGET_FILE")" "$(<"$TARGET_TWO")" > "$OBSERVED"; printf 'active' > "$STATE_FILE" ;;
    *) return 64 ;;
  esac
}
. "$SERVICE_HELPER"
. "$ARTIFACT_HELPER"
cleanup() {
  status=$?
  trap - EXIT
  quiesce_service_for_recovery
  rollback_kit_artifacts
  restore_service_state
  exit "$status"
}
trap cleanup EXIT
capture_service_state
stop_service_for_update
begin_kit_artifact_transaction "$BACKUP" "$OWNER" "$GROUP"
install_versioned "$SOURCE_FILE" "$TARGET_FILE" 0644 "$OWNER" "$GROUP" "$BACKUP"
install_versioned "$SOURCE_TWO" "$TARGET_TWO" 0644 "$OWNER" "$GROUP" "$BACKUP"
[[ "$(<"$TARGET_FILE")" == new-partial ]]
[[ "$(<"$TARGET_TWO")" == new-partial-two ]]
restore_service_state
exit 41
'''
    result = subprocess.run(
        ["bash", "-c", script, "artifact-test", str(state), str(source), str(target),
         str(source_two), str(target_two), str(backup), str(observed), user, group,
         str(ROOT / "scripts/service_transaction.sh"),
         str(ROOT / "scripts/install_kit_artifacts.sh")],
        text=True,
    )
    assert result.returncode == 41
    assert state.read_text() == "active"
    assert target.read_text() == "old-consistent"
    assert target_two.read_text() == "old-consistent-two"
    assert observed.read_text() == "old-consistent|old-consistent-two"


def test_artifact_transaction_rejects_symlink_target(tmp_path):
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    source.write_text("new")
    outside.write_text("outside-safe")
    target.symlink_to(outside)
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    command = r'''
. "$HELPER"
begin_kit_artifact_transaction "$BACKUP" "$OWNER" "$GROUP"
install_versioned "$SOURCE_FILE" "$TARGET_FILE" 0644 "$OWNER" "$GROUP" "$BACKUP"
'''
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
             "BACKUP": str(backup), "OWNER": user, "GROUP": group,
             "SOURCE_FILE": str(source), "TARGET_FILE": str(target)},
    )
    assert result.returncode != 0
    assert target.is_symlink()
    assert outside.read_text() == "outside-safe"


def test_artifact_install_rejects_symlinked_managed_directory(tmp_path):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    backup = tmp_path / "backup"
    workspace = tmp_path / "workspace"
    home.mkdir(); outside.mkdir(); workspace.mkdir()
    (home / "skills").symlink_to(outside, target_is_directory=True)
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    command = '. "$HELPER"; install_kit_artifacts "$SOURCE" "$HOME_DIR" "$OWNER" "$GROUP" "$BACKUP" "$WORKSPACE"'
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
             "SOURCE": str(ROOT), "HOME_DIR": str(home), "OWNER": user, "GROUP": group,
             "BACKUP": str(backup), "WORKSPACE": str(workspace)},
    )
    assert result.returncode != 0
    assert list(outside.iterdir()) == []


def test_artifact_install_failure_never_replaces_live_target(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    source.write_text("new")
    target.write_text("old")
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    command = r'''
set -u
. "$HELPER"
begin_kit_artifact_transaction "$BACKUP" "$OWNER" "$GROUP" || exit
register_kit_artifact_target "$TARGET_FILE" "$OWNER" "$GROUP" || exit
safe_artifact() { return 73; }
atomic_install_artifact "$SOURCE_FILE" "$TARGET_FILE" 0644 "$OWNER" "$GROUP"
'''
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
             "BACKUP": str(backup), "OWNER": user, "GROUP": group,
             "SOURCE_FILE": str(source), "TARGET_FILE": str(target)},
    )
    assert result.returncode == 73
    assert target.read_text() == "old"


def test_artifact_restore_failure_never_replaces_live_target(tmp_path):
    source = tmp_path / "backup"
    target = tmp_path / "target"
    source.write_text("old")
    target.write_text("new")
    command = r'''
set -u
. "$HELPER"
safe_artifact() { return 74; }
atomic_restore_artifact "$SOURCE_FILE" "$TARGET_FILE"
'''
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
             "SOURCE_FILE": str(source), "TARGET_FILE": str(target)},
    )
    assert result.returncode == 74
    assert target.read_text() == "new"


def test_artifact_paths_reject_lexical_traversal(tmp_path):
    managed = tmp_path / "managed"
    managed.mkdir()
    command = '. "$HELPER"; assert_safe_artifact_target "$TARGET"'
    result = subprocess.run(["bash", "-c", command], env={
        **os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
        "TARGET": str(managed / "sub" / ".." / "escaped"),
    })
    assert result.returncode != 0


def test_absent_artifact_rollback_removes_concurrent_empty_directory(tmp_path):
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    command = r'''
. "$HELPER"
begin_kit_artifact_transaction "$BACKUP" "$OWNER" "$GROUP"
register_kit_artifact_target "$TARGET" "$OWNER" "$GROUP"
mkdir "$TARGET"
rollback_kit_artifacts
'''
    result = subprocess.run(["bash", "-c", command], env={
        **os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
        "BACKUP": str(backup), "OWNER": user, "GROUP": group, "TARGET": str(target),
    })
    assert result.returncode == 0
    assert not target.exists()


def test_absent_artifact_rollback_reports_nonempty_directory(tmp_path):
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    user = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    command = r'''
. "$HELPER"
begin_kit_artifact_transaction "$BACKUP" "$OWNER" "$GROUP"
register_kit_artifact_target "$TARGET" "$OWNER" "$GROUP"
mkdir "$TARGET"; printf concurrent > "$TARGET/item"
rollback_kit_artifacts
'''
    result = subprocess.run(["bash", "-c", command], env={
        **os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
        "BACKUP": str(backup), "OWNER": user, "GROUP": group, "TARGET": str(target),
    })
    assert result.returncode != 0
    assert (target / "item").read_text() == "concurrent"


def test_secure_artifact_parent_swap_never_writes_through_symlink(tmp_path):
    import importlib.util
    helper_path = ROOT / "scripts/secure_artifact.py"
    spec = importlib.util.spec_from_file_location("secure_artifact", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    managed = tmp_path / "managed"
    displaced = tmp_path / "displaced"
    outside = tmp_path / "outside"
    managed.mkdir(); outside.mkdir()
    source = tmp_path / "source"; source.write_text("new")
    (outside / "target").write_text("outside-safe")
    original = helper._copy_fd
    def swap(source_fd, destination_fd):
        managed.rename(displaced)
        managed.symlink_to(outside, target_is_directory=True)
        original(source_fd, destination_fd)
    helper._copy_fd = swap
    helper.atomic_copy(str(source), str(managed / "target"), 0o600, os.getuid(), os.getgid())
    assert (outside / "target").read_text() == "outside-safe"
    assert (displaced / "target").read_text() == "new"


def test_installer_has_global_lock_and_fail_closed_recovery_order():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "flock -n" in install
    assert install.index("flock -n") < install.index("apt-get update")
    failure = install.index("recuperação abortada sem novas mutações")
    assert failure < install.index("rollback_kit_artifacts")
    assert 'exit 1' in install[failure:install.index("rollback_kit_artifacts")]


def test_existing_install_runs_routing_migration_transactionally():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    start = install.index(">> Migrando")
    migration = install.index('"${SOURCE_DIR}/scripts/configure_multi_ai.py"', start)
    assert install.rfind('register_kit_artifact_target "${HERMES_HOME}/config.yaml"', 0, migration) >= 0
    assert install.index("apply_runtime_patch.py", install.index(">> Aplicando")) < migration


def test_runtime_failure_restores_exact_previous_tree(tmp_path):
    runtime = tmp_path / "runtime"
    backup = tmp_path / "backup"
    runtime.mkdir()
    (runtime / "version").write_text("old")
    command = r'''
set -u
. "$HELPER"
begin_runtime_transaction "$RUNTIME" "$BACKUP"
mkdir "$RUNTIME"
printf partial > "$RUNTIME/version"
rollback_runtime_transaction
'''
    result = subprocess.run(
        ["bash", "-c", command], check=False,
        env={**os.environ, "HELPER": str(ROOT / "scripts/runtime_transaction.sh"),
             "RUNTIME": str(runtime), "BACKUP": str(backup)},
    )
    assert result.returncode == 0
    assert (runtime / "version").read_text() == "old"
    assert any(path.name.startswith("failed-runtime") for path in tmp_path.glob(".runtime.transaction.*/failed-runtime.*"))


def test_runtime_rollback_fails_closed_if_snapshot_disappears(tmp_path):
    runtime = tmp_path / "runtime"
    backup = tmp_path / "backup"
    lost = tmp_path / "lost"
    runtime.mkdir()
    (runtime / "version").write_text("old")
    command = r'''
set -u
. "$HELPER"
begin_runtime_transaction "$RUNTIME" "$BACKUP"
mv "$RUNTIME_PREVIOUS_PATH" "$LOST"
mkdir "$RUNTIME"
printf new > "$RUNTIME/version"
rollback_runtime_transaction
'''
    result = subprocess.run(
        ["bash", "-c", command], check=False,
        env={**os.environ, "HELPER": str(ROOT / "scripts/runtime_transaction.sh"),
             "RUNTIME": str(runtime), "BACKUP": str(backup), "LOST": str(lost)},
    )
    assert result.returncode != 0
    assert (runtime / "version").read_text() == "new"


def test_fresh_home_failure_restores_empty_preinstall_tree(tmp_path):
    home = tmp_path / "home"
    backup = tmp_path / "backup"
    home.mkdir()
    command = r'''
set -u
. "$HELPER"
begin_fresh_home_transaction "$HOME_DIR" "$BACKUP"
mkdir -p "$HOME_DIR/skills/braia-claude-login/scripts" "$HOME_DIR/agents"
printf partial > "$HOME_DIR/skills/braia-claude-login/SKILL.md"
printf installer-state > "$HOME_DIR/.no-bundled-skills"
rollback_fresh_home_transaction
'''
    result = subprocess.run(
        ["bash", "-c", command], check=False,
        env={**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
             "HOME_DIR": str(home), "BACKUP": str(backup)},
    )
    assert result.returncode == 0
    assert home.is_dir()
    assert list(home.iterdir()) == []


def test_existing_home_failure_restores_complete_previous_tree(tmp_path):
    home = tmp_path / "home"
    backup = tmp_path / "backup"
    (home / "sessions").mkdir(parents=True)
    (home / ".env").write_text("local-state")
    (home / "sessions/state.db").write_text("old-db")
    command = r'''
set -u
. "$HELPER"
begin_fresh_home_transaction "$HOME_DIR" "$BACKUP"
printf changed > "$HOME_DIR/.env"
printf partial > "$HOME_DIR/new-file"
rollback_fresh_home_transaction
'''
    result = subprocess.run(
        ["bash", "-c", command], check=False,
        env={**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
             "HOME_DIR": str(home), "BACKUP": str(backup)},
    )
    assert result.returncode == 0
    assert (home / ".env").read_text() == "local-state"
    assert (home / "sessions/state.db").read_text() == "old-db"
    assert not (home / "new-file").exists()


def test_successful_home_transaction_archives_snapshot_outside_live_parent(tmp_path):
    live_parent = tmp_path / "live"
    home = live_parent / "home"
    backup = tmp_path / "backups" / "kit"
    home.mkdir(parents=True)
    (home / ".env").write_text("old")
    command = r'''
set -u
. "$HELPER"
begin_fresh_home_transaction "$HOME_DIR" "$BACKUP"
printf new > "$HOME_DIR/.env"
FRESH_HOME_TRANSACTION_ACTIVE=false
archive_home_transaction_snapshot
'''
    result = subprocess.run(
        ["bash", "-c", command], check=False,
        env={**os.environ, "HELPER": str(ROOT / "scripts/install_kit_artifacts.sh"),
             "HOME_DIR": str(home), "BACKUP": str(backup)},
    )
    assert result.returncode == 0
    assert (home / ".env").read_text() == "new"
    assert (backup / "complete-home/previous-home/.env").read_text() == "old"
    assert list(tmp_path.glob(".home.transaction.*")) == []


def test_distribution_documents_the_single_optional_claude_skill():
    setup = (ROOT / "SETUP-HERMES.md").read_text(encoding="utf-8")
    prompt = (ROOT / "prompt-instalador.txt").read_text(encoding="utf-8")
    for document in (setup, prompt):
        assert "zero skills" not in document
        assert "braia-claude-login" in document
        assert "opcional" in document.lower()
        assert "conversa" in document.lower()
    assert "-type d" not in setup


def test_fallback_documentation_is_self_contained():
    policy = (ROOT / "POLITICA-FALLBACK.md").read_text(encoding="utf-8")
    assert "docs/RELEASE-v1.0.11.md" not in policy
    assert "runtime-patches/README.md" in policy


def test_shell_scripts_parse():
    for path in (ROOT / "install.sh", ROOT / "configure.sh", ROOT / "bootstrap.sh", ROOT / "scripts/check-no-secrets.sh", ROOT / "scripts/install_kit_artifacts.sh", ROOT / "scripts/service_transaction.sh", ROOT / "scripts/runtime_transaction.sh"):
        subprocess.run(["bash", "-n", str(path)], check=True)
