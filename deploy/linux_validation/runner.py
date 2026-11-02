"""Isolated local Linux validation. Host uses stdlib only; never installs locally.

run: prepare an exclusive allowlisted context, build dependencies, execute offline.
prepare: create the same context without contacting Docker or the network.
No cleanup/prune, database, cloud, Git, training, activation or serving commands.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import inference_releases as releases
from deploy.validate_linux_runtime import pins

BASE_IMAGE = 'python:3.13.16-slim-bookworm'
ORIGINAL_ID = 'nlp-20261101-local-v1'

SOURCE_FILES = (
    'inference_releases.py', 'fasttext_nlp_runtime.py', 'retrained_nlp_runtime.py',
    'offline_ocr_mapper.py', 'aoq_flexible.py',
    'aoq_ocr_tables_v0_1_0.py', 'aoq_ocr_tables_v0_2_0.py',
    'app.py', 'durable_ocr_v0_1_0.py',
    'procurement_document_layout.py', 'procurement_item_contract.py', 'workbook_limits.py',
    'deploy/inference_release.py', 'deploy/validate_linux_runtime.py', 'deploy/runtime_diagnostics.py',
    'deploy/requirements-staging-linux-amd64.lock.txt',
    'deploy/linux_validation/probe.py',
    'tests/OCR_FILETYPE_REGRESSION_TEST.py', 'tests/test_document_structure_ocr.py',
    'tests/test_multi_item_extraction.py', 'tests/test_offline_ocr_mapper.py',
    'tests/test_aoq_ocr_tables_v0_1_0.py',
)

CONTROL_FILES = ('inference_release_registry.json', 'inference_release_sources.json',
                 'deploy/linux_validation/runner.py', 'deploy/linux_validation/Dockerfile',
                 'deploy/linux_validation/build_deps.py')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(releases.canonical(value))


def snapshot():
    contract = releases.read_json(ROOT / releases.SOURCE_CONTRACT)
    releases.require(contract['release_id'] == ORIGINAL_ID
        and contract['feature_contract'] == releases.CONTRACTS
        and set(contract['artifacts']) == set(releases.SOURCES), 'RELEASE_SOURCE_CONTRACT')
    hashes = {}
    for name, record in contract['artifacts'].items():
        releases.require(record['source'] == releases.SOURCES[name], 'RELEASE_SOURCE_CONTRACT')
        path = releases.safe_path(ROOT, record['source'])
        digest = releases.sha256(path)
        releases.require(path.stat().st_size == record['bytes'] and digest == record['sha256'],
                         'RELEASE_SOURCE_CHANGED')
        hashes[record['source']] = digest
    return contract, hashes


def prepare():
    contract, models = snapshot()
    original = releases.verify(ORIGINAL_ID, dependencies=False)
    releases.require(original['manifest']['artifacts'] == {
        name: {k: record[k] for k in ('bytes', 'sha256')}
        for name, record in contract['artifacts'].items()}, 'RELEASE_SOURCE_CONTRACT')
    candidate = pins()
    releases.require(all(candidate[p] == contract['runtime']['packages'][p]
        for p in releases.PACKAGES if p != 'torch')
        and candidate['torch'] == '2.14.1+cpu'
        and contract['runtime']['packages']['torch'] == '2.14.1', 'RELEASE_DEPENDENCY_CONTRACT')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex
    folder = ROOT / 'output/linux_validation' / run_id
    folder.mkdir(parents=True, exist_ok=False)
    context = folder / 'context'; source = context / 'source'; source.mkdir(parents=True)
    for name in SOURCE_FILES:
        origin = releases.safe_path(ROOT, name)
        target = source / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    # A new expectation contract exists ONLY inside this ignored test context.
    # All weights, feature code, labels, selected heads and approval policy match.
    identifier = 'nlp-linux-' + run_id.lower()
    manifest = deepcopy(original['manifest'])
    manifest['release_id'] = identifier
    manifest['runtime'].update(locally_verified_python='3.13.16', linux_verified=False,
        validation_expectation_only=True,
        packages={p: candidate[p] for p in releases.PACKAGES})
    releases.require(manifest['approval'] == dict(status='local_validation_only',
        approved_profiles=[], quality_review=None, deployment_review=None), 'RELEASE_ACTIVATION_BLOCKED')
    model_folder = folder / 'models' / identifier; model_folder.mkdir(parents=True)
    for name in releases.SOURCES:
        target = model_folder / name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(releases.safe_path(original['root'], name), target)
    write(model_folder / releases.MANIFEST, manifest)
    entry = dict(directory='inference_releases/' + identifier,
        model_version=manifest['model_version'], manifest_sha256=releases.sha256(model_folder / releases.MANIFEST),
        approval=manifest['approval'])
    write(source / releases.REGISTRY, dict(schema=releases.SCHEMA, deployment_release=None,
                                         releases={identifier: entry}))
    releases.verify(identifier, base=source, directory=model_folder, dependencies=False)
    for filename in ('Dockerfile', 'build_deps.py'):
        shutil.copyfile(ROOT / 'deploy/linux_validation' / filename, context / filename)
    write(context / 'candidate.json', candidate)
    expected = {name: dict(bytes=(source / name).stat().st_size, sha256=releases.sha256(source / name))
                for name in (*SOURCE_FILES, releases.REGISTRY)}
    write(source / 'validation-inputs.json', dict(release_id=identifier, source_files=expected,
        original_manifest_sha256=original['manifest_sha256'],
        test_manifest_sha256=entry['manifest_sha256'], model_bytes=original['artifact_bytes'],
        runtime_changes=dict(python='3.11 Windows -> 3.13.16 Linux test expectation',
                             torch='2.14.1 Windows -> 2.14.1+cpu Linux exact test expectation'),
        synthetic_reference=dict(path='output/retrained_nlp_integration/test_results.json',
            sha256='708c60acf0ef25048f1cc59c758106398d924fc12458a17275b7eed421a319ec',
            copied=False, scope='Five frozen Windows execution references; not human-reviewed scientific targets'),
        deployment_approved=False, historical_private_pdf='EXCLUDED; not copied'))
    # Exact context allowlist; model weights cannot enter build layers/context.
    context_files = {p.relative_to(context).as_posix() for p in context.rglob('*') if p.is_file()}
    releases.require(context_files == {'Dockerfile', 'build_deps.py', 'candidate.json',
        'source/validation-inputs.json'} | {'source/' + n for n in expected}, 'RELEASE_UNEXPECTED_FILE')
    before = {n: releases.sha256(ROOT / n) for n in (*SOURCE_FILES, *CONTROL_FILES)}
    write(folder / 'preservation-before.json', dict(models=models, source=before,
          original_release={n: releases.sha256(original['root'] / n)
                            for n in (*releases.SOURCES, releases.MANIFEST)}))
    write(folder / 'preparation.json', dict(run_id=run_id, release_id=identifier,
        model_files=28, model_bytes=original['artifact_bytes'],
        context_files={n: dict(bytes=(context / n).stat().st_size, sha256=releases.sha256(context / n))
                       for n in sorted(context_files)},
        dependency_candidate=candidate, base_image_requested=BASE_IMAGE,
        status='PREPARED_ONLY', linux_executed=False, model_activation=False))
    (folder / 'results').mkdir()
    return folder


def preservation(folder):
    baseline = releases.read_json(folder / 'preservation-before.json')
    contract, after = snapshot()
    result = dict(original_models_unchanged=after == baseline['models'],
        source_controls_unchanged=all(releases.sha256(ROOT / n) == digest
                                    for n, digest in baseline['source'].items()),
        original_release_unchanged=all(releases.sha256(ROOT / 'inference_releases' / ORIGINAL_ID / n) == digest
                                    for n, digest in baseline['original_release'].items()),
        original_source_files_checked=len(after))
    write(folder / 'preservation-after.json', result)
    releases.require(all(result[k] for k in ('original_models_unchanged', 'source_controls_unchanged',
                                            'original_release_unchanged')), 'RELEASE_SOURCE_CHANGED')
    return result


def command(folder, label, args, timeout=120):
    print(label, flush=True)
    # Explicit local Desktop context defeats an inherited remote DOCKER_HOST/context.
    if args[0] == 'docker':
        args = ['docker', '--context', 'desktop-linux', *args[1:]]
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, cwd=ROOT)
        (folder / (label + '.log')).write_bytes(result.stdout)
    except subprocess.TimeoutExpired as error:
        (folder / (label + '.log')).write_bytes(error.stdout or b'')
        raise RuntimeError(label.upper() + '_TIMEOUT') from None
    if result.returncode:
        raise RuntimeError(label.upper() + '_FAILED')
    return result.stdout.decode('utf-8', errors='replace').strip()


def run(folder):
    settings = releases.read_json(folder / 'preparation.json')
    run_id = settings['run_id']; name = 'asuncion-linux-' + run_id.lower()
    image = 'asuncion-linux-validation:' + run_id.lower()
    state = dict(status='NOT_RUN', linux_executed=False, image=image, container=name,
                 hosted_verified=False, deployment_approved=False)
    created = False
    try:
        endpoint = command(folder, 'docker-local-context', ['docker', 'context', 'inspect',
            'desktop-linux', '--format', '{{.Endpoints.docker.Host}}'])
        if os.name != 'nt' or endpoint != 'npipe:////./pipe/dockerDesktopLinuxEngine':
            raise RuntimeError('LOCAL_DOCKER_DESKTOP_PIPE_REQUIRED')
        server = command(folder, 'docker-engine', ['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}'])
        if server not in ('linux/x86_64', 'linux/amd64'):
            raise RuntimeError('LINUX_AMD64_ENGINE_REQUIRED')
        command(folder, 'base-pull', ['docker', 'pull', '--platform', 'linux/amd64', BASE_IMAGE], 900)
        digest = command(folder, 'base-digest', ['docker', 'image', 'inspect', '--format',
                         '{{index .RepoDigests 0}}', BASE_IMAGE])
        if not re.fullmatch(r'python@sha256:[a-f0-9]{64}', digest):
            raise RuntimeError('BASE_DIGEST_REQUIRED')
        state['base_digest'] = digest
        command(folder, 'image-build', ['docker', 'build', '--platform', 'linux/amd64',
            '--network', 'default', '--build-arg', 'BASE_IMAGE=' + digest,
            '--label', 'asuncion.linux-validation.run=' + run_id, '-t', image, str(folder / 'context')], 2400)
        info = command(folder, 'image-inspect', ['docker', 'image', 'inspect', '--format',
                       '{{json .}}', image])
        image_info = json.loads(info)
        state.update(image_id=image_info['Id'], image_bytes=image_info['Size'],
                     model_mount_bytes=settings['model_bytes'], serving_image_bytes='NOT VERIFIED')
        # Only two mounts, both exact run-owned paths. No repository-wide mount.
        args = ['docker', 'create', '--platform', 'linux/amd64', '--name', name,
            '--label', 'asuncion.linux-validation.run=' + run_id,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--cpus', '2', '--memory', '4g',
            '--memory-swap', '4g', '--pids-limit', '256',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=536870912,mode=1777',
            '--mount', 'type=bind,source=' + str(folder / 'models') + ',target=/work/inference_releases,readonly',
            '--mount', 'type=bind,source=' + str(folder / 'results') + ',target=/results', image]
        write(folder / 'execution-plan.json', dict(argv=['docker', '--context', 'desktop-linux', *args[1:]], timeout_seconds=2400,
            effects='Creates only a new test container; retains it and its private local image. No ports/volumes/DB/credentials.',
            network='none', model_mount='read-only'))
        command(folder, 'container-create', args); created = True
        state['linux_executed'] = True
        started = time.monotonic()
        command(folder, 'inference-offline', ['docker', 'start', '--attach', name], 2400)
        summary = releases.read_json(folder / 'results/summary.json')
        releases.require(summary.get('linux_test_complete') is True
            and len(summary.get('phases', [])) == 9
            and all(p.get('passed') is True for p in summary['phases']), 'RELEASE_SYNTHETIC_PREDICTION')
        state.update(status='PROCESS_EXITED_ZERO', container_start_to_exit_seconds=time.monotonic() - started)
    except (Exception, KeyboardInterrupt) as error:
        state.update(status='BLOCKED_OR_FAILED', error_category=(str(error) if re.fullmatch(
            r'[A-Z][A-Z0-9_-]+', str(error)) else type(error).__name__))
        if created:
            # Address only this newly created, ownership-verified object, never an existing container.
            try:
                label = command(folder, 'ownership-check', ['docker', 'inspect', '--format',
                                '{{index .Config.Labels "asuncion.linux-validation.run"}}', name])
                if label == run_id:
                    command(folder, 'stop-owned-test', ['docker', 'stop', '--time', '5', name], 30)
                else:
                    state['stop_skipped'] = 'OWNERSHIP_NOT_VERIFIED'
            except Exception:
                state['stop_skipped'] = 'OWNERSHIP_OR_STOP_UNAVAILABLE'
    finally:
        if created:
            try:
                command(folder, 'container-inspect', ['docker', 'inspect', '--format', '{{json .}}', name])
            except Exception:
                state['container_inspect'] = 'NOT VERIFIED'
        state['preservation'] = preservation(folder)
        summary_path = folder / 'results/summary.json'
        phases = ({p['phase']: p for p in releases.read_json(summary_path).get('phases', [])}
                  if summary_path.is_file() else {})
        passed = lambda *names: all(phases.get(n, {}).get('passed') is True for n in names)
        state['verdicts'] = {
            'LINUX_ENVIRONMENT_READY': 'YES' if state['status'] == 'PROCESS_EXITED_ZERO' else 'NO',
            'TESSERACT_LINUX_TESTED': 'YES' if passed('native', 'formats') else 'NO',
            'FASTTEXT_LINUX_TESTED': 'YES' if passed('legacy') else 'NO',
            'RANDOM_FOREST_LINUX_TESTED': 'YES' if passed('rf') else 'NO',
            'SEMANTIC_MODEL_LINUX_TESTED': 'YES' if passed('semantic') else 'NO',
            'EXISTING_MODELS_UNCHANGED': 'YES' if state['preservation']['original_models_unchanged']
                                               and state['preservation']['original_release_unchanged'] else 'NO',
            'ONLINE_INFERENCE_DEPLOYMENT_READY': 'NO'}
        write(folder / 'execution.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run'))
    args = parser.parse_args()
    folder = prepare()
    print('Private evidence: ' + str(folder), flush=True)
    if args.action == 'prepare':
        print(json.dumps(preservation(folder), sort_keys=True))
        return 0
    result = run(folder)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'PROCESS_EXITED_ZERO' else 1


if __name__ == '__main__':
    raise SystemExit(main())
