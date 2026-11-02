
"""Preparation stage only: exact wheels, provenance evidence, offline installation."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import venv
import zipfile
from email.parser import BytesParser


def run(*args):
    subprocess.run(args, check=True, timeout=900)


def main():
    pins = json.loads(Path('/preparation/candidate.json').read_text())
    evidence = Path('/opt/evidence'); evidence.mkdir()
    wheels = Path('/preparation/wheels'); wheels.mkdir()
    candidate = Path('/preparation/pypi.txt')

    candidate.write_text(''.join(
        f'{n}=={v}\n' for n, v in sorted(pins.items()) if n != 'torch'
    ))

    common = [sys.executable, '-I', '-m', 'pip', '--isolated', 'download',
              '--only-binary=:all:', '--no-deps', '--dest', str(wheels)]

    run(*common, '--index-url', 'https://pypi.org/simple', '-r', str(candidate))
    run(*common, '--index-url', 'https://download.pytorch.org/whl/cpu',
        'torch==' + pins['torch'])

    records = []

    for path in sorted(wheels.iterdir()):
        if path.suffix != '.whl':
            raise ValueError('LINUX_WHEEL_ONLY_REQUIRED')

        # FIX: Only accept top-level wheel distribution metadata.
        # Ignore nested metadata files from bundled dependencies.
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                n for n in archive.namelist()
                if n.count('/') == 1
                and n.endswith('.dist-info/METADATA')
            ]

            if len(metadata_files) != 1:
                raise ValueError(
                    f'LINUX_WHEEL_METADATA_AMBIGUOUS: '
                    f'{path.name}: {metadata_files}'
                )

            metadata = BytesParser().parsebytes(
                archive.read(metadata_files[0])
            )

        if not metadata['Name'] or not metadata['Version']:
            raise ValueError(
                f'LINUX_WHEEL_METADATA_INVALID: {path.name}'
            )

        name = metadata['Name'].lower().replace('_', '-').replace('.', '-')
        version = metadata['Version']

        if pins.get(name) != version:
            raise ValueError('LINUX_WHEEL_VERSION_MISMATCH')

        records.append(dict(
            name=name,
            version=version,
            filename=path.name,
            bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            index='https://download.pytorch.org/whl/cpu'
                  if name == 'torch' else 'https://pypi.org/simple'
        ))

    if len(records) != len(pins) or {r['name'] for r in records} != set(pins):
        raise ValueError('LINUX_WHEEL_CLOSURE_INCOMPLETE')

    lock = evidence / 'resolved-linux-amd64.lock.txt'
    lock.write_text(''.join(
        f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}\n"
        for r in records
    ))

    (evidence / 'wheels.json').write_text(
        json.dumps(records, indent=2) + '\n'
    )

    venv.create('/opt/venv', with_pip=True)

    run('/opt/venv/bin/python', '-I', '-m', 'pip', '--isolated',
        'install', '--no-index', '--find-links', str(wheels),
        '--no-deps', '--require-hashes', '-r', str(lock))

    run('/opt/venv/bin/python', '-I', '-m', 'pip', 'check')

    run('/opt/venv/bin/python', '-I', '-c',
        "import torch; assert torch.__version__ == '2.14.1+cpu'; "
        "assert torch.version.cuda is None and torch.version.hip is None "
        "and not torch.cuda.is_available()")

    (evidence / 'installer.json').write_text(json.dumps(dict(
        builder_python=sys.version,
        builder_pip=importlib.metadata.version('pip'),
        wheel_hash_review='Mechanically captured from explicit HTTPS indexes; independent review pending',
        dependency_versions_changed=False
    ), indent=2) + '\n')


if __name__ == '__main__':
    main()
