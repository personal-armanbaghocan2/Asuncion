"""Copy reviewed UI assets to Vercel public/static without importing the app.

No database, models, credentials, uploads or document directories are inspected.
The allowlist preserves existing /static URLs. Original static inputs stay in
the function for existing asset-existence checks, including the dashboard photo.
Conflicts and unexpected public files stop the build; this helper never removes
or overwrites existing files.
Run with --check for read-only input and destination validation.
"""
import argparse
from pathlib import Path


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


def build_assets(project_root, *, check=False):
    root = Path(project_root).resolve(strict=True)
    expected = {'static/' + name for name in ASSETS}
    public = _contained_path(root, 'public')
    inputs = []
    for name in ASSETS:
        source = _contained_path(root, 'static/' + name)
        target = _contained_path(root, 'public/static/' + name)
        if not source.is_file():
            raise RuntimeError('A reviewed UI asset is missing.')
        if target.exists() and (not target.is_file() or target.read_bytes() != source.read_bytes()):
            raise RuntimeError('Existing public asset conflicts with the reviewed source.')
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
    if not check:
        for source, target in inputs:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as output:
                    output.write(source.read_bytes())
    return dict(assets=len(inputs), bytes=sum(p.stat().st_size for p, _ in inputs),
                check_only=check)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    print(build_assets(Path(__file__).resolve().parents[1], check=parser.parse_args().check))
