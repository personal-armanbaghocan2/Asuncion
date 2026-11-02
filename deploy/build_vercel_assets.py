"""Copy reviewed UI assets to Vercel public/static without importing the app.

No database, models, credentials, uploads or document directories are inspected.
The allowlist preserves existing /static URLs. Original static inputs stay in
the function for existing asset-existence checks, including the dashboard photo.
Conflicts and unexpected public files stop the build; this helper never removes
or overwrites existing files.
Run with --check for read-only input and destination validation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid


ASSETS = (
    'css/style.css',
    'css/reference-layout.css',
    'css/procurement-print.css',
    'css/ocr-table.css',
    'css/dashboard.css',
    'css/confirmation-dialog.css',
    'js/dashboard.js',
    'js/csrf.js',
    'js/confirm-actions.js',
    'js/ppmp-ocr-upload.js',
    'js/ppmp-ocr-review.js',
    'js/ppmp-editor.js',
    'js/ocr-table.js',
    'js/source-preview.js',
    'js/record-selectors.js',
    'js/ui.js',
    'images/asuncion_municipal.jpg',
    'images/asuncion-seal.png',
)


def _replace_generated(source, target):
    # Windows scanners can briefly retain freshly read/generated assets. A
    # persistent access denial still fails with the original backup retained.
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != 'nt' or attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _contained_path(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise RuntimeError('Asset path escapes the project root.')
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise RuntimeError('Linked asset paths are not allowed.')
    return path


def build_assets(project_root, *, check=False, refresh_generated=False):
    root = Path(project_root).resolve(strict=True)
    expected = {'static/' + name for name in ASSETS}
    public = _contained_path(root, 'public')
    inputs = []
    conflicts = []
    for name in ASSETS:
        source = _contained_path(root, 'static/' + name)
        target = _contained_path(root, 'public/static/' + name)
        if not source.is_file():
            raise RuntimeError('A reviewed UI asset is missing.')
        if target.exists() and (not target.is_file() or target.read_bytes() != source.read_bytes()):
            if not target.is_file() or not refresh_generated:
                raise RuntimeError('Existing public asset conflicts with the reviewed source.')
            conflicts.append((source, target, target.read_bytes()))
        inputs.append((source, target))
    if public.exists():
        if not public.is_dir():
            raise RuntimeError('The public destination must be a directory.')
        # Walk only validated directories, never an unreviewed link target.
        pending = [public]
        while pending:
            for path in pending.pop().iterdir():
                _contained_path(root, path.relative_to(root))
                if path.is_dir():
                    pending.append(path)
                elif path.relative_to(public).as_posix() not in expected:
                    raise RuntimeError('Unreviewed public files are not allowed.')
    backup_id = None
    if not check and conflicts:
        # Explicit local cache refresh only. Preserve every replaced generated
        # byte under ignored output before changing any reviewed destination.
        backup_id = uuid.uuid4().hex
        backup = _contained_path(root, 'output/vercel_asset_backups/' + backup_id)
        backup.mkdir(parents=True)
        inventory = {}
        for source, target, old in conflicts:
            name = target.relative_to(public).as_posix()
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            with saved.open('xb') as stream: stream.write(old)
            inventory[name] = {'old_sha256': hashlib.sha256(old).hexdigest(),
                               'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        with (backup / 'backup.json').open('x', encoding='utf-8') as stream:
            json.dump(inventory, stream, indent=2)
        for source, target, old in conflicts:
            if target.read_bytes() != old:
                raise RuntimeError('Generated asset changed during refresh; backup retained.')
            descriptor, name = tempfile.mkstemp(prefix='.psms-ui-', dir=target.parent)
            os.close(descriptor)
            temporary = Path(name)
            try:
                temporary.write_bytes(source.read_bytes())
                _replace_generated(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    if not check:
        for source, target in inputs:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write(source.read_bytes())
    result = dict(assets=len(inputs), bytes=sum(p.stat().st_size for p, _ in inputs), check_only=check)
    if refresh_generated:
        result.update(refreshed=len(conflicts), backup_id=backup_id)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--refresh-generated', action='store_true',
                        help='Explicitly refresh only reviewed generated UI copies, preserving private backups first.')
    arguments = parser.parse_args()
    print(build_assets(Path(__file__).resolve().parents[1], check=arguments.check,
                       refresh_generated=arguments.refresh_generated))
