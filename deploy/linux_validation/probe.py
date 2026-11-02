"""Offline Linux test appliance; original inference and extraction entry points.

Each phase has a fresh bounded process and a durable result even on failures.
Never imports the Flask app, DB layer, training code or private record sources.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import faulthandler
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import resource
import signal
import shutil
import subprocess
import sys
import tempfile
from threading import Lock, get_ident
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from deploy.validate_linux_runtime import pins, deny_network
import inference_releases as releases

RESULTS = Path('/results')
_CHECKPOINT_LOCK = Lock()
_STACK_STREAM = None


@contextmanager
def checkpoint(operation, worker=None):
    """Small, flushed records survive SIGKILL; never log input text or model data."""
    started = time.monotonic()
    def record(state, **detail):
        value = dict(operation=operation, state=state, worker=worker, thread=get_ident(),
                     monotonic_seconds=time.monotonic(), **detail)
        with _CHECKPOINT_LOCK, (RESULTS / 'concurrent-checkpoints.jsonl').open('a') as stream:
            stream.write(json.dumps(value, sort_keys=True) + '\n')
            stream.flush()
    record('started')
    try:
        yield
    except BaseException as error:
        record('failed', seconds=time.monotonic() - started, error_type=type(error).__name__)
        raise
    else:
        record('completed', seconds=time.monotonic() - started)

# Frozen Windows synthetic execution references from
# output/retrained_nlp_integration/test_results.json, NOT human-reviewed truth.
CASES = [
    ('quantity', 'SYNTHETIC paper; quantity 10 reams', 'SYNTHETIC paper; quantity 8 reams',
     ('Needs Recanvass', 'Requirement satisfied'), ('Needs Recanvass', 'Requirement satisfied')),
    ('unit', 'SYNTHETIC paper; quantity 10 reams', 'SYNTHETIC paper; quantity 10 boxes',
     ('Acceptable', 'Requirement satisfied'), ('Acceptable', 'Requirement satisfied')),
    ('minimum', 'SYNTHETIC computer; minimum 16 GB RAM', 'SYNTHETIC computer; 8 GB RAM',
     ('Needs Recanvass', 'Below minimum'), ('Needs Recanvass', 'Below minimum')),
    ('material', 'SYNTHETIC chair; material stainless steel', 'SYNTHETIC chair; material plastic',
     ('Reject', 'Different item'), ('Needs Recanvass', 'Missing item')),
    ('different_item', 'SYNTHETIC printer', 'SYNTHETIC desk',
     ('Reject', 'Different item'), ('Reject', 'Requirement satisfied')),
]


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def write(path, value):
    path.write_bytes(releases.canonical(value))


def guard(event, args):
    deny_network(event, args)
    if event == 'import' and args[0] in ('app', 'db'):
        raise PermissionError('LINUX_APPLICATION_DATABASE_IMPORT_DENIED')


def capture(*args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'LINUX_SUBPROCESS_FAILED')
    return result.stdout.strip()


def bounded_process(args, timeout):
    # Kill the entire owned process group, including OCR subprocesses, on timeout.
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return process.returncode, stdout + stderr
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            # communicate() after killing returns the complete buffered output.
            # Preserve it too, including diagnostics emitted at the deadline.
            stdout, stderr = process.communicate()
            error.output = stdout
            error.stderr = stderr
            raise


def source_integrity():
    inventory = releases.read_json(ROOT / 'validation-inputs.json')
    for name, record in inventory['source_files'].items():
        path = releases.safe_path(ROOT, name)
        require(path.stat().st_size == record['bytes'] and releases.sha256(path) == record['sha256'],
                'LINUX_SOURCE_CHECKSUM_MISMATCH')
    return inventory


def dependencies():
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'LINUX_AMD64_REQUIRED')
    require(platform.python_version() == '3.13.16', 'LINUX_PYTHON_VERSION_MISMATCH')
    expected = pins()
    versions = {p: importlib.metadata.version(p) for p in expected}
    require(versions == expected, 'LINUX_DEPENDENCY_CONTRACT_FAILED')
    capture(sys.executable, '-I', '-m', 'pip', 'check')
    import torch
    require(torch.__version__ == '2.14.1+cpu' and torch.version.cuda is None
            and torch.version.hip is None and not torch.cuda.is_available(), 'LINUX_CPU_TORCH_REQUIRED')
    distributions = {}
    for distribution in importlib.metadata.distributions():
        files = {Path(distribution.locate_file(f)) for f in distribution.files or []}
        distributions[distribution.metadata['Name']] = sum(p.stat().st_size for p in files if p.is_file())
    installed = Path('/opt/venv')
    return dict(os_release=Path('/etc/os-release').read_text(), architecture=platform.machine(),
        python=sys.version, libc=platform.libc_ver(), versions=versions,
        torch=dict(version=torch.__version__, cuda=torch.version.cuda, hip=torch.version.hip, cpu_only=True),
        pip_check='PASSED', distribution_bytes=distributions,
        venv_file_bytes=sum(p.stat().st_size for p in installed.rglob('*') if p.is_file()),
        native_packages=capture('dpkg-query', '-W', '-f=${Package}\t${Version}\n'),
        certificate_target_tls='NOT VERIFIED; no cloud TLS connection or CA approval in this task')


def native():
    import pytesseract
    from PIL import Image, ImageDraw, ImageFont
    executable = shutil.which('tesseract')
    require(executable is not None, 'LINUX_TESSERACT_MISSING')
    languages = capture(executable, '--list-langs')
    require('eng' in languages.splitlines(), 'LINUX_ENGLISH_MISSING')
    trained, = Path('/usr/share/tesseract-ocr').glob('*/tessdata/eng.traineddata')
    image = Image.new('RGB', (1500, 430), 'white')
    draw = ImageDraw.Draw(image); font = ImageFont.truetype('DejaVuSans.ttf', 36)
    for index, line in enumerate(('FICTIONAL TEST', 'A4 Copy Paper', 'Quantity 5 Ream',
                                  'Unit Cost 245.50', 'Total 1227.50')):
        draw.text((40, 25 + index * 75), line, font=font, fill='black')
    started = time.monotonic()
    text = pytesseract.image_to_string(image, lang='eng', config='--psm 6', timeout=30)
    require(all(token in text for token in ('A4 Copy Paper', '5 Ream', '245.50', '1227.50')),
            'LINUX_NATIVE_OCR_FIELD_MISMATCH')
    return dict(version=capture(executable, '--version'), languages=languages,
        executable_sha256=releases.sha256(executable), traineddata_sha256=releases.sha256(trained),
        traineddata_bytes=trained.stat().st_size, synthetic_text=text,
        ocr_seconds=time.monotonic() - started)


def model(identifier, engine):
    evidence = releases.verify(identifier)  # all model hashes + dependency contract before loading
    root = evidence['root']
    started = time.monotonic()
    if engine == 'legacy':
        import fasttext_nlp_runtime as runtime
        runtime.clear_cache(); assets = runtime.load_assets(str(root / 'legacy'))
        expected_dims = (659, 668); index = 3; model_root = str(root / 'legacy')
        heads = [type(assets.status_classifier).__name__, type(assets.issue_classifier).__name__]
    else:
        import retrained_nlp_runtime as runtime
        runtime.clear_cache(); assets = runtime.load_assets(root / 'rf')
        expected_dims = (559, 559); index = 4; model_root = root / 'rf'
        heads = [type(assets.status).__name__, type(assets.issue).__name__]
    initialization = time.monotonic() - started
    results = []
    for case in CASES:
        started = time.monotonic()
        result = runtime.predict_pair(model_root, case[1], case[2])
        require((result['feature_dimension'], result['status_feature_dimension']) == expected_dims,
                'LINUX_FEATURE_DIMENSIONS')
        require((result['status'], result['issue']) == case[index], 'LINUX_WINDOWS_PREDICTION_PARITY_FAILED')
        if engine == 'rf':
            require(result['model_manifest']['production_promotion_allowed'] is False
                and result['diagnostics']['automatic_approval'] is False, 'LINUX_RF_POLICY_CHANGED')
        results.append(dict(case=case[0], status=result['status'], issue=result['issue'],
                            warm_seconds=time.monotonic() - started))
    output = dict(initialization_seconds=initialization, predictions=results,
        feature_dimensions=expected_dims, classifier_types=heads, manifest_sha256=evidence['manifest_sha256'],
        labels=dict(status=releases.STATUS, issue=releases.ISSUES),
        windows_synthetic_parity='PASSED; execution equivalence only, not accuracy')
    if engine == 'rf':
        # load_assets also compares every frozen float32 feature and saved selected-head prediction.
        probes = releases.read_json(root / 'rf/reload_probes.json')
        output.update(frozen_reload_feature_prediction_parity='PASSED',
                      reload_pair_count=len(probes['text_pairs']), setting=7, seed=42,
                      automatic_approval=False, production_promotion_allowed=False)
    return output


def semantic(identifier):
    releases.verify(identifier)
    started = time.monotonic()
    import numpy as np
    import offline_ocr_mapper as mapper
    model = mapper.load_local_model()
    require(model is not None, mapper._MODEL_ERROR_CODE)
    initialization = time.monotonic() - started
    require(mapper._SCHEMA_EMBEDDINGS.shape == (12, 384), 'LINUX_SEMANTIC_DIMENSIONS')
    started = time.monotonic()
    vectors = model.encode(['SYNTHETIC item description', 'SYNTHETIC allocated budget'], normalize_embeddings=True)
    require(vectors.shape == (2, 384) and np.isfinite(vectors).all()
            and np.allclose(np.linalg.norm(vectors, axis=1), 1), 'LINUX_SEMANTIC_VECTORS')
    elapsed = time.monotonic() - started
    module = importlib.import_module('test_offline_ocr_mapper')
    functions = [getattr(module, n) for n in dir(module) if n.startswith('test_')]
    for function in functions:
        function()
    return dict(initialization_seconds=initialization, warm_encode_seconds=elapsed,
        shape=list(vectors.shape), schema_shape=list(mapper._SCHEMA_EMBEDDINGS.shape),
        mapping_expectations_passed=len(functions), selected_release=getattr(model, '_psms_release_id'),
        model_device=str(model.device), downloads=False)


def formats(identifier):
    releases.verify(identifier)
    import offline_ocr_mapper as mapper
    require(mapper.load_local_model() is not None, 'LINUX_SEMANTIC_REQUIRED')
    module = importlib.import_module('OCR_FILETYPE_REGRESSION_TEST')
    original = module.assert_mapping
    rows = []
    def measured(path, label, min_exact=10):
        started = time.monotonic()
        try:
            original(path, label, min_exact)
            rows.append(dict(format=label, result='PASSED', seconds=time.monotonic() - started))
        except Exception as error:
            rows.append(dict(format=label, result='FAILED', error_type=type(error).__name__,
                             detail=str(error), seconds=time.monotonic() - started))
    with patch.object(module, 'assert_mapping', side_effect=measured):
        module.main()
    require(len(rows) == 11, 'LINUX_FORMAT_CASE_COUNT')
    return dict(cases=rows, passed=all(r['result'] == 'PASSED' for r in rows),
        scope='Fictional source field mapping; actual original semantic model and Tesseract',
        native_exact_fields_required=12, scanned_exact_fields_required=9,
        missing_unit_price_must_stay_empty=True, human_approval=False)


def structures():
    modules = [importlib.import_module(n) for n in ('test_document_structure_ocr',
                'test_multi_item_extraction', 'test_aoq_ocr_tables_v0_1_0')]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in modules)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    # These are known fictional fixtures; details contain no operational records.
    return dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), passed=result.wasSuccessful() and not result.skipped,
        details=stream.getvalue(), document_metrics=modules[1].METRICS,
        semantic_mocked_for_parser_mechanisms=True, real_tesseract_for_scans=True,
        historical_private_pdf='EXCLUDED; not evaluated', browser_or_database_tested=False)


def negatives(identifier):
    import fasttext_nlp_runtime as legacy
    import retrained_nlp_runtime as rf
    import offline_ocr_mapper as mapper
    original_verify = releases.verify
    evidence = original_verify(identifier)
    results = []
    with tempfile.TemporaryDirectory(prefix='invalid-models-') as temporary:
        base = Path(temporary); target = base / 'inference_releases' / identifier
        shutil.copytree(evidence['root'], target)
        shutil.copyfile(ROOT / releases.REGISTRY, base / releases.REGISTRY)
        def relocated(value, **kwargs):
            return original_verify(value, **{**kwargs, 'base': base})
        with patch.object(releases, 'verify', side_effect=relocated):
            for engine, filename in [('legacy', 'legacy/fasttext_model.model.wv.vectors_ngrams.npy'),
                                      ('rf', 'rf/' + releases.HEAD + '/status.joblib'),
                                      ('semantic', 'semantic/tokenizer.json')]:
                for operation in ('missing', 'corrupt'):
                    path = target / filename; saved = path.read_bytes()
                    if operation == 'missing': path.unlink()
                    else: path.write_bytes(b'INVALID SYNTHETIC MODEL')
                    legacy.clear_cache(); rf.clear_cache()
                    mapper._MODEL = None; mapper._SCHEMA_EMBEDDINGS = None
                    with patch('joblib.load') as joblib_load, patch('gensim.models.FastText.load') as fast_load, \
                         patch('sentence_transformers.SentenceTransformer') as semantic_load:
                        if engine == 'semantic':
                            require(mapper.load_local_model() is None, 'LINUX_BAD_SEMANTIC_ACCEPTED')
                            diagnostic = mapper._MODEL_ERROR_CODE
                        else:
                            try:
                                if engine == 'legacy': legacy.load_assets(str(target / 'legacy'))
                                else: rf.load_assets(target / 'rf')
                            except RuntimeError as error:
                                diagnostic = str(error)
                            else:
                                raise RuntimeError('LINUX_BAD_MODEL_ACCEPTED')
                        joblib_load.assert_not_called(); fast_load.assert_not_called(); semantic_load.assert_not_called()
                    path.write_bytes(saved)
                    results.append(dict(engine=engine, mutation=operation, diagnostic_code=diagnostic,
                                        denied_before_deserialization=True))
    # Actual policy check, no classifier or application activation.
    try:
        original_verify(identifier, deployment=True)
    except releases.ReleaseError as error:
        require(str(error) == 'RELEASE_ACTIVATION_BLOCKED', 'LINUX_APPROVAL_GATE_CHANGED')
    else:
        raise RuntimeError('LINUX_UNAPPROVED_RELEASE_ACTIVATED')
    return dict(cases=results, release_activation_blocked=True, fallback_selected=False)


def concurrent(identifier):
    with checkpoint('release_verification'):
        releases.verify(identifier)
    with checkpoint('runtime_imports'):
        import fasttext_nlp_runtime as legacy
        import retrained_nlp_runtime as rf
        import offline_ocr_mapper as mapper
    root = ROOT / 'inference_releases' / identifier
    def task(index):
        case = CASES[index]
        with checkpoint('legacy_load', index):
            legacy.load_assets(str(root / 'legacy'))
        with checkpoint('legacy_prediction', index):
            a = legacy.predict_pair(str(root / 'legacy'), case[1], case[2])
        with checkpoint('rf_load', index):
            rf.load_assets(root / 'rf')
        with checkpoint('rf_prediction', index):
            b = rf.predict_pair(root / 'rf', case[1], case[2])
        require((a['status'], a['issue']) == case[3] and (b['status'], b['issue']) == case[4],
                'LINUX_CONCURRENT_PREDICTION_MISMATCH')
        with checkpoint('semantic_load_and_schema_encode', index):
            require(mapper.load_local_model() is not None, 'LINUX_CONCURRENT_SEMANTIC_FAILED')
        with checkpoint('fixture_import', index):
            import OCR_FILETYPE_REGRESSION_TEST as fixtures
        with tempfile.TemporaryDirectory(prefix='concurrent-ocr-') as temporary:
            path = Path(temporary) / 'fictional.png'
            with checkpoint('fixture_image', index):
                fixtures.make_image(path, 'PNG')
            with checkpoint('ocr_and_semantic_mapping', index):
                fixtures.assert_mapping(path, 'concurrent PNG', 9,
                                        observe=lambda operation: checkpoint(operation, index))
        with checkpoint('task_completed', index):
            pass
        return dict(case=case[0], passed=True)
    started = time.monotonic()
    with checkpoint('thread_pool_completion'):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(task, (0, 2)))
    # Real loader failure is safely surfaced, without a fallback prediction.
    with checkpoint('safe_loading_failure'):
        legacy.clear_cache()
        with patch('gensim.models.FastText.load', side_effect=ValueError('FICTIONAL LOAD FAILURE')):
            try: legacy.load_assets(str(root / 'legacy'))
            except RuntimeError as error: require(str(error) == 'FASTTEXT_MODEL_LOAD_FAILED', 'LINUX_LOAD_FAILURE_UNSAFE')
            else: raise RuntimeError('LINUX_LOAD_FAILURE_NOT_REJECTED')
    with checkpoint('subprocess_timeout_enforcement'):
        try:
            bounded_process([sys.executable, '-I', '-c', 'import time; time.sleep(10)'], 0.1)
        except subprocess.TimeoutExpired:
            timeout_verified = True
        else:
            raise RuntimeError('LINUX_TIMEOUT_NOT_ENFORCED')
    return dict(workers=2, tasks=results, seconds=time.monotonic() - started,
                actual_ocr=True, loading_race_tested=True, safe_loading_failure=True,
                synthetic_timeout_enforced=timeout_verified)


def worker(phase):
    observe = checkpoint if phase == 'concurrent' else lambda operation: nullcontext()
    if phase == 'concurrent':
        # Keep open through interpreter/thread shutdown, after the JSON result.
        # The unchanged 180-second outer bound limits dumps to at most four.
        global _STACK_STREAM
        _STACK_STREAM = (RESULTS / 'concurrent-stacks.log').open('w')
        faulthandler.enable(file=_STACK_STREAM, all_threads=True)
        faulthandler.dump_traceback_later(45, repeat=True, file=_STACK_STREAM)
    # Nothing inherited from the host except the image's explicit environment.
    require(not any(k.startswith(('SUPABASE_', 'PG', 'VERCEL', 'CAPSTONE_')) for k in os.environ),
            'LINUX_UNEXPECTED_HOST_CONFIGURATION')
    with observe('worker_source_integrity_before'):
        inventory = source_integrity()
    os.environ.update(PSMS_ENV='test', PSMS_INFERENCE_RELEASE_ID=inventory['release_id'],
                      HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1')
    sys.addaudithook(guard)
    with observe('worker_database_guard_import'):
        import psycopg
    with patch.object(psycopg, 'connect', side_effect=PermissionError('LINUX_DATABASE_DENIED')):
        identifier = inventory['release_id']
        functions = dict(dependencies=dependencies, native=native,
            legacy=lambda: model(identifier, 'legacy'), rf=lambda: model(identifier, 'rf'),
            semantic=lambda: semantic(identifier), formats=lambda: formats(identifier),
            structures=structures, negatives=lambda: negatives(identifier),
            concurrent=lambda: concurrent(identifier))
        started = time.monotonic()
        try:
            detail = functions[phase]()
            require(detail.get('passed', True), 'LINUX_PHASE_ASSERTIONS_FAILED')
            result = dict(phase=phase, passed=True, evidence=detail)
        except Exception as error:
            result = dict(phase=phase, passed=False, error_type=type(error).__name__,
                          error_category=str(error)[:500])
            # Preserve failed format/structure results, including exact extraction categories.
            if 'detail' in locals(): result['evidence'] = detail
        result.update(seconds=time.monotonic() - started,
            process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            child_peak_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024)
        with observe('worker_source_integrity_after'):
            result['source_integrity_after'] = source_integrity() == inventory
        if phase not in ('dependencies', 'native'):
            with observe('worker_release_integrity_after'):
                releases.verify(identifier)
        with observe('worker_result_write'):
            write(Path('/results') / (phase + '.json'), result)
        if phase == 'concurrent':
            with checkpoint('worker_result_saved'):
                pass
        return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--phase')
    args = parser.parse_args()
    if args.phase: return worker(args.phase)
    inventory = source_integrity()
    results = []
    for phase, timeout in [('dependencies', 120), ('native', 60), ('legacy', 120), ('rf', 120),
                           ('semantic', 180), ('formats', 420), ('structures', 480),
                           ('negatives', 180), ('concurrent', 180)]:
        started = time.monotonic()
        try:
            returncode, output = bounded_process([sys.executable, '-B', __file__, '--phase', phase], timeout)
            (Path('/results') / (phase + '.log')).write_bytes(output)
            path = Path('/results') / (phase + '.json')
            result = json.loads(path.read_text()) if path.is_file() else dict(phase=phase, passed=False,
                error_category='LINUX_CHILD_EXIT_WITHOUT_RESULT', returncode=returncode)
            result['process_exit_code'] = returncode
            if returncode: result['passed'] = False
        except subprocess.TimeoutExpired as error:
            (Path('/results') / (phase + '.log')).write_bytes((error.stdout or b'') + (error.stderr or b''))
            result = dict(phase=phase, passed=False, error_category='LINUX_PHASE_TIMEOUT', timeout_seconds=timeout)
        result['fresh_process_seconds'] = time.monotonic() - started
        results.append(result)
        write(Path('/results/summary.json'), dict(scope='ISOLATED LINUX; NOT HOSTED VERIFICATION',
            phases=results, source_integrity=inventory, linux_test_complete=len(results) == 9,
            deployment_approved=False, hosted_verified=False))
        print(json.dumps(dict(phase=phase, passed=result['passed'])), flush=True)
        if phase == 'dependencies' and not result['passed']: break
    for name in ('wheels.json', 'resolved-linux-amd64.lock.txt', 'installer.json'):
        shutil.copyfile(Path('/opt/evidence') / name, Path('/results') / name)
    shutil.copytree('/opt/native-evidence', '/results/native-build-evidence')
    try:
        peak = Path('/sys/fs/cgroup/memory.peak').read_text().strip()
    except OSError:
        peak = 'NOT VERIFIED'
    write(Path('/results/cgroup.json'), dict(memory_peak_bytes=peak,
        scope='Entire validation container including children, page cache and sequential probes; not a serving profile'))
    return 0 if len(results) == 9 and all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
