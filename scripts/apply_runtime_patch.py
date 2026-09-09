#!/usr/bin/env python3
"""Install reviewed runtime changes with filewise three-way merges.

The lock serializes installers, not arbitrary editors. Every replacement is
rechecked; rollback refuses to overwrite a concurrent editor's unknown bytes.
Stop the runtime while installing: a multi-file change is not reader-atomic.
"""
import argparse
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

# Known 0.21.0 bases provide reviewed blobs.  HEAD and its whole tree are
# evidence only: unrelated customer files must never decide patch eligibility.
BASE_TREE = '59d8aa0a3099a0a74aab6f9abd2c4ffcc8ddf5d2'
UPSTREAM_TREE = 'daaffc303ae437041b7f76be17c5f61b14f2ce99'
KNOWN_BASE_TREES = {'0.21.0': (BASE_TREE, UPSTREAM_TREE)}


def _git(root, *args, env=None, data=None):
    clean = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    clean['GIT_OPTIONAL_LOCKS'] = '0'
    clean.update(env or {})
    result = subprocess.run(
        ['git', '-c', 'core.autocrlf=false', '-c', 'core.fsmonitor=false',
         '-C', str(root), *args], input=data, capture_output=True, env=clean)
    if result.returncode:
        raise ValueError('Git validation failed: ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def _known_base(root):
    """Resolve reviewed base blobs by their embedded Hermes version marker."""
    for version, trees in KNOWN_BASE_TREES.items():
        marker = f'__version__ = "{version}"'.encode()
        for tree in trees:
            try:
                version_file = _git(root, 'show', tree + ':hermes_cli/__init__.py')
            except ValueError:
                continue
            if marker in version_file:
                return version, tree
    raise ValueError('Unknown runtime base/version; preserve without modification')


def _identity(root):
    try:
        head = _git(root, 'rev-parse', 'HEAD').decode().strip()
        tree = _git(root, 'rev-parse', 'HEAD^{tree}').decode().strip()
        top = Path(_git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    except ValueError as exc:
        raise ValueError('Unsupported runtime source tree') from exc
    if top != root:
        raise ValueError('Unsupported runtime source tree; inspect and validate before adapting the patch')
    version, base_tree = _known_base(root)
    return {'runtime_commit': head, 'runtime_tree': tree,
            'runtime_base_version': version, 'runtime_base_tree': base_tree}


def _safe_path(path):
    """Reject symlinks/junctions in every existing component, before resolve."""
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Unexpected runtime target path: symlink or reparse point')
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Unexpected runtime target path: non-directory parent')
    return path


def _relative(name):
    # Only portable unquoted source paths: no Git metadata, NTFS streams,
    # drive names, dot traversal, aliases or case collisions.
    if not re.fullmatch(r'[A-Za-z0-9_.\-/]+', name):
        raise ValueError('Unsafe patch target path')
    for part in name.split('/'):
        if (not part or part in ('.', '..') or part.endswith('.')
                or part.lower() == '.git'
                or re.fullmatch(r'(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?', part)):
            raise ValueError('Unsafe patch target path')
    return name


def _patch_targets(stage, patch, env):
    # Git parses the hunks. Restrict headers as well to forbid renames, copies,
    # deletions and non-regular files, including metadata-only patches.
    text = patch.decode('utf-8')
    for line in text.splitlines():
        if line.startswith(('rename ', 'copy ', 'deleted file mode ', 'old mode ', 'new mode ')):
            raise ValueError('Unsupported patch rename, deletion or mode change')
        if line.startswith('new file mode ') and line[14:] not in ('100644', '100755'):
            raise ValueError('Unsupported patch file mode')
    pairs = re.findall(r'^--- ([^\n]+)\n\+\+\+ ([^\n]+)', text, re.M)
    declared = set()
    for old, new in pairs:
        old, new = old.rstrip('\r'), new.rstrip('\r')
        if not new.startswith('b/') or (old != '/dev/null' and old != 'a/' + new[2:]):
            raise ValueError('Unsafe patch target path or unexpected rename')
        declared.add(_relative(new[2:]))
    records = _git(stage, 'apply', '--numstat', '-z', '-', env=env, data=patch).split(b'\0')
    targets = set()
    for record in filter(None, records):
        fields = record.decode('utf-8').split('\t')
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise ValueError('Unsupported patch format')
        targets.add(_relative(fields[2]))
    if not targets or targets != declared:
        raise ValueError('Unsupported patch target headers')
    return targets


def _entries(stage, env):
    entries = {}
    for record in _git(stage, 'ls-files', '--stage', '-z', env=env).split(b'\0'):
        if record:
            meta, name = record.split(b'\t', 1)
            mode, oid, level = meta.decode().split()
            if level != '0':
                raise ValueError('Unmerged staging index')
            entries[name.decode('utf-8')] = (mode, oid)
    return entries


def _states(root, base_tree, patch, previous):
    """Private Git index AND object store; --check leaves real Git intact."""
    objects = Path(_git(root, 'rev-parse', '--git-path', 'objects').decode().strip())
    if not objects.is_absolute():
        objects = root / objects
    with tempfile.TemporaryDirectory(prefix='braia-patch-stage-') as directory:
        stage = Path(directory)
        _git(stage, 'init', '--bare', '-q')
        env = {'GIT_ALTERNATE_OBJECT_DIRECTORIES': str(objects.resolve())}
        targets = _patch_targets(stage, patch, env)
        if previous is not None:
            targets |= _patch_targets(stage, previous, env)
        if len({name.lower() for name in targets}) != len(targets):
            raise ValueError('Unsafe patch target case collision')
        targets = sorted(targets)
        states = []
        for content in (None, patch, previous):
            _git(stage, 'read-tree', base_tree, env=env)
            base = _entries(stage, env)
            if content is not None:
                _git(stage, 'apply', '--cached', '--whitespace=nowarn', '-', env=env, data=content)
            entries = _entries(stage, env)
            changed = {name for name in base.keys() | entries.keys() if base.get(name) != entries.get(name)}
            if not changed.issubset(targets):
                raise ValueError('Patch changed undeclared targets')
            state = {}
            for name in targets:
                for parent in Path(name).parents:
                    if parent.as_posix() in entries:
                        raise ValueError('Unexpected runtime target path in base Git tree')
                entry = entries.get(name)
                if entry is None:
                    state[name] = None
                else:
                    mode, oid = entry
                    if mode not in ('100644', '100755'):
                        raise ValueError('Unexpected runtime target path: non-regular Git entry')
                    state[name] = (_git(stage, 'cat-file', 'blob', oid, env=env), int(mode, 8) & 0o777)
            states.append(state)
        return states


def _snapshot(root, names):
    result = {}
    for name in names:
        path = _safe_path(root / name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            result[name] = None
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unexpected runtime target path: non-regular file or hardlink')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read()
            after = os.fstat(stream.fileno())
        # Windows lstat/fstat disagree on ctime (birth time vs change time).
        fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns,
                                 s.st_ctime_ns if os.name != 'nt' else None,
                                 stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid)
        if (fingerprint(info) != fingerprint(opened) or fingerprint(opened) != fingerprint(after)
                or fingerprint(after) != fingerprint(_safe_path(path).lstat())):
            raise ValueError('Runtime changed concurrently; preserved without modification')
        result[name] = {'data': data, 'mode': stat.S_IMODE(info.st_mode),
                        'uid': info.st_uid, 'gid': info.st_gid,
                        'fingerprint': fingerprint(after)}
    return result


def _matches(snapshot, state):
    # Executable bits are part of the reviewed Git state. Other permissions
    # and ownership are retained when replacing a file.
    return all((snapshot[name] is None if expected is None else
                snapshot[name] is not None and snapshot[name]['data'] == expected[0]
                and (os.name == 'nt' or bool(snapshot[name]['mode'] & 0o111) == bool(expected[1] & 0o111)))
               for name, expected in state.items())


def _merge_text(name, base, current, target):
    if any(data is None or b'\0' in data for data in (base, current, target)):
        raise ValueError(f'Runtime managed binary conflict: {name}; preserved without modification')
    try:
        for data in (base, current, target):
            data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError(f'Runtime managed binary conflict: {name}; preserved without modification') from exc
    with tempfile.TemporaryDirectory(prefix='braia-runtime-merge-') as directory:
        paths = [Path(directory) / part for part in ('local', 'base', 'target')]
        for path, data in zip(paths, (current, base, target)):
            path.write_bytes(data)
        result = subprocess.run(
            ['git', 'merge-file', '-p', '-L', 'local', '-L', 'base', '-L', 'target',
             *(str(path) for path in paths)], capture_output=True)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1:
        raise ValueError(f'Runtime managed three-way conflict: {name}; preserved without modification')
    raise ValueError(f'Runtime managed merge failed: {name}; preserved without modification')


def _planned_state(before, base, target, previous):
    """Classify and resolve each managed path independently."""
    planned = {}
    for name in sorted(target):
        observed = before[name]
        current = None if observed is None else (observed['data'], observed['mode'])
        old, desired = base[name], target[name]
        if current == desired or (previous is not None and current == previous[name]):
            planned[name] = desired
        elif desired == old:
            # Upstream does not modify this managed path: preserve local edits,
            # including a deliberate local deletion.
            planned[name] = current
        elif current == old:
            planned[name] = desired
        elif current is None:
            raise ValueError(f'Runtime local deletion conflicts with target: {name}; preserved without modification')
        elif old is None:
            raise ValueError(f'Runtime target addition collides with local file: {name}; preserved without modification')
        elif desired is None:
            raise ValueError(f'Runtime target deletion conflicts with local change: {name}; preserved without modification')
        else:
            planned[name] = (_merge_text(name, old[0], current[0], desired[0]), desired[1])
    return planned


def _managed_snapshot_sha256(snapshot):
    value = {
        name: None if item is None else {
            'sha256': _hash(item['data']),
            'mode': item['mode'],
            'uid': item['uid'],
            'gid': item['gid'],
        }
        for name, item in sorted(snapshot.items())
    }
    return _hash(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def _verify(root, identity, snapshot):
    current = _identity(root)
    base_fields = ('runtime_base_version', 'runtime_base_tree')
    if (any(current[field] != identity[field] for field in base_fields)
            or _snapshot(root, snapshot) != snapshot):
        raise ValueError('Runtime changed concurrently; preserved without modification')


@contextmanager
def _lock(root):
    key = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    path = Path(tempfile.gettempdir()) / ('braia-runtime-patch-' + key + '.lock')
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError('Runtime patch lock is held; inspect any interrupted installer before retrying') from exc
    try:
        yield
    finally:
        path.rmdir()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _save(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _backup(root, backup_root, before, expected, identity, patch, previous):
    _safe_path(backup_root)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = backup_root / ('delegation-fallback-' + stamp)
    backup.mkdir(parents=True, mode=0o700)
    for name, item in before.items():
        if item is not None:
            dest = backup / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            _save(dest, item['data'])
            dest.chmod(0o600)
    manifest = {**identity, 'runtime': str(root), 'patch_sha256': _hash(patch),
                'previous_patch_sha256': _hash(previous) if previous is not None else None,
                'before': {name: _hash(item['data']) if item else None for name, item in before.items()},
                'after': {name: _hash(item[0]) if item else None for name, item in expected.items()},
                'metadata': {name: {k: item[k] for k in ('mode', 'uid', 'gid')} if item else None
                             for name, item in before.items()}}
    _save(backup / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode('utf-8'))
    return backup


def _replacement(path, data, metadata):
    """Prepare bytes and metadata on the same filesystem before atomic rename."""
    _safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix='.braia-patch-', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            current = os.fstat(stream.fileno())
            if hasattr(os, 'fchown') and (current.st_uid, current.st_gid) != (metadata['uid'], metadata['gid']):
                os.fchown(stream.fileno(), metadata['uid'], metadata['gid'])
            os.chmod(temporary, metadata['mode'])
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _replace(source, target):
    os.replace(source, target)


def _transition(root, before, expected, identity, backup):
    prepared, intended_metadata, created_dirs, changed = {}, {}, [], []
    current = dict(before)
    try:
        for name, desired in expected.items():
            if desired is None or (before[name] is not None
                                   and before[name]['data'] == desired[0]
                                   and (os.name == 'nt' or bool(before[name]['mode'] & 0o111)
                                        == bool(desired[1] & 0o111))):
                continue
            path = _safe_path(root / name)
            for directory in reversed(path.parent.parents):
                if directory.is_relative_to(root) and not directory.exists():
                    directory.mkdir()
                    created_dirs.append(directory)
            if not path.parent.exists():
                path.parent.mkdir()
                created_dirs.append(path.parent)
            parent = path.parent.stat()
            metadata = before[name] or {'mode': desired[1], 'uid': parent.st_uid, 'gid': parent.st_gid}
            if before[name] is not None and os.name != 'nt':
                metadata = dict(metadata)
                metadata['mode'] = ((metadata['mode'] & ~0o111)
                                    | (desired[1] & 0o111))
            prepared[name] = _replacement(path, desired[0], metadata)
            staged = prepared[name].stat()
            intended_metadata[name] = (stat.S_IMODE(staged.st_mode), staged.st_uid, staged.st_gid)
        _verify(root, identity, current)
        for name, source in prepared.items():
            _verify(root, identity, current)
            _safe_path(root / name)
            # Include the attempted replacement in recovery, even if a failure
            # is delivered just after rename committed but before it returned.
            changed.append(name)
            _replace(source, root / name)
            current[name] = _snapshot(root, [name])[name]
            if (current[name]['data'] != expected[name][0]
                    or tuple(current[name][k] for k in ('mode', 'uid', 'gid')) != intended_metadata[name]):
                raise ValueError('Patch verification failed')
        _verify(root, identity, current)
        if not _matches(current, expected):
            raise ValueError('Patch verification failed')
    except BaseException as exc:
        failures = []
        for name in reversed(changed):
            try:
                observed = _snapshot(root, [name])[name]
                if observed == before[name]:
                    continue  # Rename failed before changing this path.
                # Preserve unknown writes even during recovery.
                if observed is None or observed['data'] != expected[name][0]:
                    raise ValueError('concurrent edit')
                if tuple(observed[k] for k in ('mode', 'uid', 'gid')) != intended_metadata[name]:
                    raise ValueError('concurrent metadata edit')
                if current[name] != before[name] and observed != current[name]:
                    raise ValueError('concurrent replacement or metadata edit')
                if before[name] is None:
                    (root / name).unlink()
                else:
                    restore = _replacement(root / name, before[name]['data'], before[name])
                    try:
                        _safe_path(root / name)
                        _replace(restore, root / name)
                    finally:
                        restore.unlink(missing_ok=True)
            except BaseException:
                failures.append(name)
        # Remove prepared files before trying to remove their new directories.
        for temporary in prepared.values():
            temporary.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        detail = ('; recovery blocked for ' + ', '.join(failures)) if failures else '; previous bytes restored'
        raise ValueError(f'Patch failed ({exc}){detail}; backup: {backup}') from exc
    finally:
        for path in prepared.values():
            path.unlink(missing_ok=True)


def install(runtime, patch, backup_root, check=False, previous_patch=None,
            expected_runtime_base_tree=None, expected_managed_snapshot_sha256=None,
            expected_patch_sha256=None):
    root = _safe_path(runtime).resolve(strict=True)
    patch_path = Path(patch).resolve(strict=True)
    patch = patch_path.read_bytes()
    if previous_patch is None:
        candidate = patch_path.with_name('previous-delegation-fallback.patch')
        previous_patch = candidate if candidate.exists() else None
    previous = Path(previous_patch).read_bytes() if previous_patch is not None else None
    backup_root = _safe_path(backup_root).resolve()
    if backup_root.is_relative_to(root):
        raise ValueError('Keep runtime backups outside the runtime checkout')
    with _lock(root):
        identity = _identity(root)
        base, target, old = _states(root, identity['runtime_base_tree'], patch, previous)
        before = _snapshot(root, target)
        _verify(root, identity, before)
        managed_snapshot_sha256 = _managed_snapshot_sha256(before)
        observed_gate = (
            identity['runtime_base_tree'], managed_snapshot_sha256, _hash(patch)
        )
        expected_gate = (
            expected_runtime_base_tree, expected_managed_snapshot_sha256, expected_patch_sha256
        )
        if any(value is not None for value in expected_gate) and observed_gate != expected_gate:
            raise ValueError('Runtime no longer matches approved managed runtime identity; preserved without modification')
        expected = _planned_state(before, base, target, old if previous is not None else None)
        if _matches(before, expected):
            return {'status': 'already_applied', **identity, 'patch_sha256': _hash(patch),
                    'managed_snapshot_sha256': managed_snapshot_sha256}
        if check:
            return {'status': 'ready', **identity, 'patch_sha256': _hash(patch),
                    'managed_snapshot_sha256': managed_snapshot_sha256}
        backup = _backup(root, backup_root, before, expected, identity, patch, previous)
        _transition(root, before, expected, identity, backup)
        return {'status': 'applied', 'backup': str(backup), **identity, 'patch_sha256': _hash(patch),
                'managed_snapshot_sha256': managed_snapshot_sha256}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--backup-root', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--expected-runtime-base-tree')
    parser.add_argument('--expected-managed-snapshot-sha256')
    parser.add_argument('--expected-patch-sha256')
    args = parser.parse_args()
    try:
        result = install(
            args.runtime,
            Path(__file__).resolve().parent.parent / 'runtime-patches/delegation-fallback.patch',
            args.backup_root,
            args.check,
            expected_runtime_base_tree=args.expected_runtime_base_tree,
            expected_managed_snapshot_sha256=args.expected_managed_snapshot_sha256,
            expected_patch_sha256=args.expected_patch_sha256,
        )
        print(json.dumps(result))
    except (ValueError, OSError) as exc:
        print('ERROR:', str(exc), file=sys.stderr)
        raise SystemExit(2)
