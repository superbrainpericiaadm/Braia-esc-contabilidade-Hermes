"""Runtime migration must recognize complete reviewed states and recover failures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

TARGETS = ('tools/delegate_tool.py', 'hermes_cli/config_defaults.py')
NEW_TARGET = 'new_package/nested/policy.py'
BASE = b'value = 1\n' + b'# unchanged context\n' * 10
OLD = BASE.replace(b'value = 1', b'value = 2')
NEW = BASE.replace(b'value = 1', b'value = 3')
PORTAL_TREE = '59d8aa0a3099a0a74aab6f9abd2c4ffcc8ddf5d2'
UPSTREAM_COMMIT = '29112bef099274229cadff79cdff7bf7b99c4b77'
UPSTREAM_TREE = 'daaffc303ae437041b7f76be17c5f61b14f2ce99'


@pytest.fixture
def fixture_runtime(tmp_path, repo_root, monkeypatch):
    spec = importlib.util.spec_from_file_location('runtime_patch_fixture', repo_root / 'scripts/apply_runtime_patch.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    root = tmp_path / 'runtime'
    root.mkdir()

    def git(*args):
        return subprocess.check_output(['git', '-c', 'core.autocrlf=false', '-C', str(root), *args], text=True).strip()

    for name in TARGETS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BASE)
    (root / 'hermes_cli' / '__init__.py').write_text(
        '__version__ = "0.21.0"\n', encoding='utf-8')
    git('init', '-q')
    git('config', 'core.autocrlf', 'false')
    git('add', '-A')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'first installation')
    fixture_tree = git('rev-parse', 'HEAD^{tree}')
    monkeypatch.setattr(helper, 'BASE_TREE', fixture_tree)
    monkeypatch.setattr(helper, 'KNOWN_BASE_TREES', {'0.21.0': (fixture_tree,)})
    previous = tmp_path / 'previous-delegation-fallback.patch'
    patch = tmp_path / 'change.patch'
    for content, destination in ((OLD, previous), (NEW, patch)):
        for name in TARGETS:
            (root / name).write_bytes(content)
        destination.write_bytes(subprocess.check_output(['git', '-C', str(root), 'diff', '--no-ext-diff']))
    git('restore', '--', *TARGETS)
    with patch.open('ab') as stream:
        stream.write((f'diff --git a/{NEW_TARGET} b/{NEW_TARGET}\nnew file mode 100644\n'
                      f'--- /dev/null\n+++ b/{NEW_TARGET}\n@@ -0,0 +1 @@\n+policy = True\n').encode())
    return helper, root, patch, tmp_path / 'backups', git


def filesystem_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_identical_source_in_another_installation_is_supported(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    first = git('rev-parse', 'HEAD')
    git('-c', 'user.name=Other installation', '-c', 'user.email=fixture@example.invalid',
        'commit', '--allow-empty', '-qm', 'different installation metadata')
    second = git('rev-parse', 'HEAD')
    assert first != second
    before = filesystem_bytes(root)
    result = helper.install(root, patch, backup, check=True)
    assert result['status'] == 'ready' and result['runtime_commit'] == second
    assert not backup.exists()
    assert filesystem_bytes(root) == before
    assert not (root / 'new_package').exists()


def test_known_runtime_base_manifest_is_not_a_global_head_tree_gate(repo_root):
    spec = importlib.util.spec_from_file_location('runtime_patch_flavio', repo_root / 'scripts/apply_runtime_patch.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    assert helper.KNOWN_BASE_TREES['0.21.0'] == (PORTAL_TREE, UPSTREAM_TREE)


def test_arbitrary_commit_and_tree_from_unowned_file_are_accepted(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    (root / 'different_source.py').write_bytes(b'different = True\n')
    git('add', '-A')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'different source')
    result = helper.install(root, patch, backup, check=True)
    assert result['status'] == 'ready'
    assert result['runtime_commit'] == git('rev-parse', 'HEAD')
    assert result['runtime_tree'] == git('rev-parse', 'HEAD^{tree}')


def test_unowned_commit_between_snapshot_and_verify_does_not_block(fixture_runtime, monkeypatch):
    """HEAD/tree are evidence; concurrency is decided only on managed targets."""
    helper, root, patch, backup, git = fixture_runtime
    original_snapshot = helper._snapshot
    injected = False

    def snapshot_then_commit(runtime, names):
        nonlocal injected
        result = original_snapshot(runtime, names)
        if not injected:
            injected = True
            extra = root / 'customer-unowned.py'
            extra.write_text('customer = True\n', encoding='utf-8')
            git('add', extra.name)
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'unowned concurrent')
        return result

    monkeypatch.setattr(helper, '_snapshot', snapshot_then_commit)
    assert helper.install(root, patch, backup, check=True)['status'] == 'ready'
    assert all((root / name).read_bytes() == BASE for name in TARGETS)
    assert not backup.exists()


def test_untracked_owned_file_is_ignored(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    (root / 'customer-plugin.py').write_text('keep = True\n', encoding='utf-8')
    before = (root / 'customer-plugin.py').read_bytes()
    assert helper.install(root, patch, backup, check=True)['status'] == 'ready'
    assert (root / 'customer-plugin.py').read_bytes() == before


def test_clean_managed_three_way_merge_preserves_both_changes(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    path = root / TARGETS[0]
    path.write_bytes(BASE + b'# local extension\n')
    result = helper.install(root, patch, backup)
    assert result['status'] == 'applied'
    merged = path.read_bytes()
    assert b'value = 3' in merged and b'# local extension' in merged


def test_same_line_managed_conflict_is_specific_and_preserved(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    path = root / TARGETS[0]
    path.write_bytes(BASE.replace(b'value = 1', b'value = 99'))
    before = path.read_bytes()
    with pytest.raises(ValueError, match=r'three-way conflict: tools/delegate_tool.py'):
        helper.install(root, patch, backup)
    assert path.read_bytes() == before


def test_local_deletion_is_compatible_when_target_does_not_modify(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    name = 'legacy.py'
    path = root / name
    path.write_text('legacy = True\n', encoding='utf-8')
    git('add', name)
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'known base with legacy')
    monkey_tree = git('rev-parse', 'HEAD^{tree}')
    helper.KNOWN_BASE_TREES = {'0.21.0': (monkey_tree,)}
    path.write_text('legacy = False\n', encoding='utf-8')
    patch.with_name('previous-delegation-fallback.patch').write_bytes(
        subprocess.check_output(['git', '-C', str(root), 'diff', '--no-ext-diff', '--', name]))
    path.unlink()
    assert helper.install(root, patch, backup, check=True)['status'] == 'ready'
    assert not path.exists()


def test_local_deletion_conflicts_when_target_modifies(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    path = root / TARGETS[0]
    path.unlink()
    with pytest.raises(ValueError, match=r'local deletion conflicts with target: tools/delegate_tool.py'):
        helper.install(root, patch, backup, check=True)
    assert not path.exists()


def test_runtime_ephemeral_untracked_files_are_ignored(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    cache = root / '__pycache__' / 'delegate.cpython-311.pyc'
    cache.parent.mkdir()
    cache.write_bytes(b'ephemeral')
    assert helper.install(root, patch, backup, check=True)['status'] == 'ready'
    assert cache.read_bytes() == b'ephemeral'


def test_unknown_runtime_version_or_base_is_blocked_specifically(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    helper.KNOWN_BASE_TREES = {'9.9.9': ('0' * 40,)}
    with pytest.raises(ValueError, match='Unknown runtime base/version'):
        helper.install(root, patch, backup, check=True)


def test_uncommitted_target_changes_are_preserved(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    path = root / TARGETS[0]
    path.write_bytes(b'owner_customization = True\n')
    with pytest.raises(ValueError, match='three-way conflict'):
        helper.install(root, patch, backup)
    assert path.read_bytes() == b'owner_customization = True\n'
    assert not backup.exists()


@pytest.mark.parametrize('migration', [False, True])
def test_clean_and_previous_migrate_with_new_file_and_idempotency(fixture_runtime, migration):
    helper, root, patch, backup, git = fixture_runtime
    if migration:
        git('apply', str(patch.with_name('previous-delegation-fallback.patch')))
    # Unrelated staged content, plus the exact target state staged during migration,
    # must survive without changing the real index or object store.
    (root / 'unrelated.txt').write_bytes(b'keep me\n')
    git('add', '-A')
    index = (root / '.git/index').read_bytes()
    objects = filesystem_bytes(root / '.git/objects')
    before_stats = {name: (root / name).stat() for name in TARGETS}
    result = helper.install(root, patch, backup)
    assert result['status'] == 'applied'
    assert result['runtime_commit'] == git('rev-parse', 'HEAD')
    assert all((root / name).read_bytes() == NEW for name in TARGETS)
    assert (root / NEW_TARGET).read_bytes() == b'policy = True\n'
    assert (root / 'unrelated.txt').read_bytes() == b'keep me\n'
    assert (root / '.git/index').read_bytes() == index
    assert filesystem_bytes(root / '.git/objects') == objects
    for name, prior in before_stats.items():
        after = (root / name).stat()
        assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
            prior.st_uid, prior.st_gid, stat.S_IMODE(prior.st_mode))
    saved = Path(result['backup'])
    assert not saved.is_relative_to(root)
    manifest = json.loads((saved / 'manifest.json').read_text())
    assert manifest['patch_sha256'] == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert manifest['before'][NEW_TARGET] is None
    assert manifest['after'][NEW_TARGET] == hashlib.sha256(b'policy = True\n').hexdigest()
    for name in TARGETS:
        assert (saved / name).read_bytes() == (OLD if migration else BASE)
        assert manifest['before'][name] == hashlib.sha256((saved / name).read_bytes()).hexdigest()
    after = filesystem_bytes(root)
    assert helper.install(root, patch, backup)['status'] == 'already_applied'
    assert filesystem_bytes(root) == after
    assert len(list(backup.iterdir())) == 1


def test_explicit_previous_fixture_path_and_check_migration(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    previous = patch.with_name('previous-delegation-fallback.patch')
    custom = previous.with_name('reviewed-old.patch')
    previous.rename(custom)
    git('apply', str(custom))
    before = filesystem_bytes(root)
    assert helper.install(root, patch, backup, check=True, previous_patch=custom)['status'] == 'ready'
    assert filesystem_bytes(root) == before
    assert not backup.exists()
    assert helper.install(root, patch, backup, previous_patch=custom)['status'] == 'applied'


def test_extra_edit_on_target_state_is_preserved(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    git('apply', str(patch))
    path = root / TARGETS[0]
    with path.open('ab') as stream:
        stream.write(b'owner_customization = True\n')
    before = filesystem_bytes(root)
    assert helper.install(root, patch, backup)['status'] == 'already_applied'
    assert filesystem_bytes(root) == before
    assert not backup.exists()


def test_extra_edit_on_previous_same_line_state_conflicts(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    git('apply', str(patch.with_name('previous-delegation-fallback.patch')))
    path = root / TARGETS[0]
    with path.open('ab') as stream:
        stream.write(b'owner_customization = True\n')
    before = filesystem_bytes(root)
    with pytest.raises(ValueError, match='three-way conflict'):
        helper.install(root, patch, backup)
    assert filesystem_bytes(root) == before
    assert not backup.exists()


def test_mixed_base_and_previous_is_resolved_per_file(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    (root / TARGETS[0]).write_bytes(OLD)
    assert helper.install(root, patch, backup)['status'] == 'applied'
    assert all((root / name).read_bytes() == NEW for name in TARGETS)


def test_existing_new_target_is_not_overwritten(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    path = root / NEW_TARGET
    path.parent.mkdir(parents=True)
    path.write_bytes(b'private = True\n')
    with pytest.raises(ValueError, match='target addition collides'):
        helper.install(root, patch, backup)
    assert path.read_bytes() == b'private = True\n'
    assert not backup.exists()


def symlink(source, target, directory=False):
    try:
        source.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f'Symlink capability unavailable: {exc}')


@pytest.mark.parametrize('position', ['file', 'parent', 'new_parent', 'root'])
def test_symlink_components_are_rejected(fixture_runtime, tmp_path, position):
    helper, root, patch, backup, git = fixture_runtime
    outside = tmp_path / 'outside'
    outside.mkdir()
    if position == 'file':
        victim = outside / 'source.py'
        victim.write_bytes(BASE)
        (root / TARGETS[0]).unlink()
        symlink(root / TARGETS[0], victim)
    elif position == 'parent':
        # Even a symlink resolving INSIDE the checkout must be rejected.
        (root / 'tools').rename(root / 'real_tools')
        symlink(root / 'tools', root / 'real_tools', directory=True)
    elif position == 'new_parent':
        symlink(root / 'new_package', outside, directory=True)
    else:
        alias = tmp_path / 'alias'
        symlink(alias, root, directory=True)
        root = alias
    outside_before = filesystem_bytes(outside)
    with pytest.raises(ValueError, match='symlink|reparse'):
        helper.install(root, patch, backup)
    assert filesystem_bytes(outside) == outside_before
    assert not backup.exists()


@pytest.mark.parametrize('unsafe', ['../escape.py', '/absolute.py', '.git/config',
                                   'tools/../../escape.py', 'C:/escape.py', 'tools/NUL.py'])
def test_unsafe_patch_paths_are_rejected(fixture_runtime, unsafe):
    helper, root, patch, backup, git = fixture_runtime
    patch.write_bytes((f'--- /dev/null\n+++ b/{unsafe}\n@@ -0,0 +1 @@\n+escape\n').encode())
    before = filesystem_bytes(root)
    with pytest.raises(ValueError, match='path|Git validation'):
        helper.install(root, patch, backup)
    assert filesystem_bytes(root) == before
    assert not backup.exists()


def test_patch_rename_is_rejected(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    patch.write_bytes(b'diff --git a/tools/delegate_tool.py b/moved.py\nsimilarity index 100%\n'
                      b'rename from tools/delegate_tool.py\nrename to moved.py\n')
    with pytest.raises(ValueError, match='rename'):
        helper.install(root, patch, backup)
    assert (root / TARGETS[0]).read_bytes() == BASE
    assert not backup.exists()


def test_new_symlink_in_patch_is_rejected(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    patch.write_bytes(b'diff --git a/escape b/escape\nnew file mode 120000\n'
                      b'--- /dev/null\n+++ b/escape\n@@ -0,0 +1 @@\n+../outside\n')
    with pytest.raises(ValueError, match='file mode'):
        helper.install(root, patch, backup)
    assert not (root / 'escape').exists()
    assert not backup.exists()


@pytest.mark.parametrize('migration', [False, True])
def test_mid_transition_failure_restores_all_bytes_and_new_directories(fixture_runtime, monkeypatch, migration):
    helper, root, patch, backup, git = fixture_runtime
    if migration:
        git('apply', str(patch.with_name('previous-delegation-fallback.patch')))
    before = filesystem_bytes(root)
    original = helper._replace
    calls = []

    def fail_third(source, target):
        calls.append(str(target))
        if len(calls) == 3:
            raise OSError('injected replacement failure')
        original(source, target)

    monkeypatch.setattr(helper, '_replace', fail_third)
    with pytest.raises(ValueError, match='previous bytes restored'):
        helper.install(root, patch, backup)
    assert len(calls) >= 4  # At least one rollback replacement happened.
    assert filesystem_bytes(root) == before
    assert not (root / 'new_package').exists()
    assert len(list(backup.glob('*/manifest.json'))) == 1
    monkeypatch.setattr(helper, '_replace', original)
    assert helper.install(root, patch, backup)['status'] == 'applied'  # Lock released.


def test_failure_delivered_after_rename_still_restores_state(fixture_runtime, monkeypatch):
    helper, root, patch, backup, git = fixture_runtime
    before = filesystem_bytes(root)
    original = helper._replace
    calls = 0

    def fail_after_rename(source, target):
        nonlocal calls
        original(source, target)
        calls += 1
        if calls == 2:
            raise OSError('injected after committed rename')

    monkeypatch.setattr(helper, '_replace', fail_after_rename)
    with pytest.raises(ValueError, match='previous bytes restored'):
        helper.install(root, patch, backup)
    assert filesystem_bytes(root) == before
    assert not (root / 'new_package').exists()


def test_concurrent_edit_after_backup_is_preserved(fixture_runtime, monkeypatch):
    helper, root, patch, backup, git = fixture_runtime
    original = helper._backup

    def modify_after_backup(*args):
        result = original(*args)
        (root / TARGETS[0]).write_bytes(b'concurrent edit\n')
        return result

    monkeypatch.setattr(helper, '_backup', modify_after_backup)
    with pytest.raises(ValueError, match='Patch failed'):
        helper.install(root, patch, backup)
    assert (root / TARGETS[0]).read_bytes() == b'concurrent edit\n'
    assert (root / TARGETS[1]).read_bytes() == BASE
    assert not (root / 'new_package').exists()


def test_concurrent_edit_mid_transition_is_preserved_and_other_writes_restored(fixture_runtime, monkeypatch):
    helper, root, patch, backup, git = fixture_runtime
    original = helper._replace
    calls = 0

    def edit_after_first(source, target):
        nonlocal calls
        original(source, target)
        calls += 1
        if calls == 1:
            (root / TARGETS[0]).write_bytes(b'concurrent edit\n')

    monkeypatch.setattr(helper, '_replace', edit_after_first)
    with pytest.raises(ValueError, match='previous bytes restored'):
        helper.install(root, patch, backup)
    assert (root / TARGETS[0]).read_bytes() == b'concurrent edit\n'
    assert (root / TARGETS[1]).read_bytes() == BASE
    assert not (root / 'new_package').exists()


def test_unknown_edit_of_replaced_file_is_preserved_during_rollback(fixture_runtime, monkeypatch):
    helper, root, patch, backup, git = fixture_runtime
    original = helper._replace
    calls = 0

    def overwrite_after_first(source, target):
        nonlocal calls
        original(source, target)
        calls += 1
        if calls == 1:
            Path(target).write_bytes(b'concurrent edit of installed file\n')

    monkeypatch.setattr(helper, '_replace', overwrite_after_first)
    with pytest.raises(ValueError, match='recovery blocked'):
        helper.install(root, patch, backup)
    assert (root / TARGETS[1]).read_bytes() == b'concurrent edit of installed file\n'
    assert (root / TARGETS[0]).read_bytes() == BASE
    assert not (root / 'new_package').exists()
    saved = next(backup.glob('*/manifest.json')).parent
    assert (saved / TARGETS[1]).read_bytes() == BASE


def test_lock_blocks_second_installer_even_with_different_backup_root(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    with helper._lock(root):
        with pytest.raises(ValueError, match='lock'):
            helper.install(root, patch, backup.with_name('different-backups'))
    assert not backup.exists()
    assert helper.install(root, patch, backup, check=True)['status'] == 'ready'


def test_expected_managed_identity_is_checked_before_backup(fixture_runtime, monkeypatch):
    helper, root, patch, backup, git = fixture_runtime
    approved = helper.install(root, patch, backup, check=True)
    (root / TARGETS[0]).write_bytes(b'changed after coordinator inspection\n')
    backup_called = False

    def forbidden_backup(*args):
        nonlocal backup_called
        backup_called = True

    monkeypatch.setattr(helper, '_backup', forbidden_backup)
    with pytest.raises(ValueError, match='approved managed runtime identity'):
        helper.install(
            root, patch, backup,
            expected_runtime_base_tree=approved['runtime_base_tree'],
            expected_managed_snapshot_sha256=approved['managed_snapshot_sha256'],
            expected_patch_sha256=approved['patch_sha256'],
        )
    assert backup_called is False
    assert not backup.exists()


def test_parent_symlink_introduced_after_backup_is_rejected(fixture_runtime, monkeypatch, tmp_path):
    helper, root, patch, backup, git = fixture_runtime
    outside = tmp_path / 'outside'
    outside.mkdir()
    # Probe capability before entering installer recovery (which catches errors).
    probe = tmp_path / 'probe'
    symlink(probe, outside, directory=True)
    probe.unlink()
    original = helper._backup

    def change_parent(*args):
        saved = original(*args)
        (root / 'new_package').symlink_to(outside, target_is_directory=True)
        return saved

    monkeypatch.setattr(helper, '_backup', change_parent)
    with pytest.raises(ValueError, match='symlink|reparse'):
        helper.install(root, patch, backup)
    assert list(outside.iterdir()) == []
    assert all((root / name).read_bytes() == BASE for name in TARGETS)


def test_backup_inside_runtime_is_rejected_in_check_mode(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    with pytest.raises(ValueError, match='outside'):
        helper.install(root, patch, root / 'backup', check=True)
    assert not (root / 'backup').exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permissions and ownership')
def test_custom_permissions_and_owner_survive_apply_and_rollback(fixture_runtime, monkeypatch):
    helper, root, patch, backup, git = fixture_runtime
    for name in TARGETS:
        (root / name).chmod(0o640)
    owners = {name: (root / name).stat() for name in TARGETS}
    original = helper._replace
    calls = 0

    def fail_third(source, target):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('injected failure')
        original(source, target)

    monkeypatch.setattr(helper, '_replace', fail_third)
    with pytest.raises(ValueError, match='previous bytes restored'):
        helper.install(root, patch, backup)
    for name, before in owners.items():
        after = (root / name).stat()
        assert (root / name).read_bytes() == BASE
        assert stat.S_IMODE(after.st_mode) == 0o640
        assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    monkeypatch.setattr(helper, '_replace', original)
    helper.install(root, patch, backup)
    for name, before in owners.items():
        after = (root / name).stat()
        assert stat.S_IMODE(after.st_mode) == 0o640
        assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX executable bits')
def test_executable_bit_drift_is_reconciled(fixture_runtime):
    helper, root, patch, backup, git = fixture_runtime
    path = root / TARGETS[0]
    path.chmod(0o744)
    assert helper.install(root, patch, backup, check=True)['status'] == 'ready'
    assert helper.install(root, patch, backup)['status'] == 'applied'
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert helper.install(root, patch, backup)['status'] == 'already_applied'
