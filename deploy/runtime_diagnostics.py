"""Offline, non-sensitive OCR/model diagnostics; never imports Flask app or SQL.

Metadata is the default. --native explicitly probes the local Tesseract binary;
--load-semantic explicitly loads only the configured local/release model. Neither
option grants model approval or establishes hosted readiness.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inference_releases as releases


def native_probe():
    import aoq_flexible
    import pytesseract
    aoq_flexible._configure_tesseract()
    command = pytesseract.pytesseract.tesseract_cmd
    if not (shutil.which(command) or Path(command).is_file()):
        return {'code': 'TESSERACT_MISSING', 'english_available': False}
    try:
        result = subprocess.run([command, '--list-langs'], capture_output=True, text=True, timeout=10,
                                creationflags=0x08000000 if os.name == 'nt' else 0)
        english = 'eng' in result.stdout.splitlines()
        return {'code': 'READY' if result.returncode == 0 and english else 'TESSERACT_LANGUAGE_UNAVAILABLE',
                'english_available': english}
    except Exception:
        return {'code': 'TESSERACT_EXECUTION_FAILED', 'english_available': False}


def check(*, native=False, load_semantic=False):
    from runtime_configuration import environment_mode
    from transfer_limits import on_vercel
    from durable_workbooks import enabled, Unavailable
    mode = environment_mode()
    result = {'scope': 'LOCAL DIAGNOSTICS ONLY', 'database_contacted': False, 'hosted_verified': False,
              'model_loading_tested': False, 'native_execution_tested': native}
    try:
        result['workbooks'] = {'code': 'PERSISTENT_SELECTED_SCHEMA_UNVERIFIED' if enabled() else 'LOCAL_EXCEL_SELECTED'}
    except Unavailable:
        result['workbooks'] = {'code': 'PERSISTENT_ACTIVATION_BLOCKED'}
    missing = []
    for name in releases.PACKAGES:
        try: importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: missing.append(name)
    result['dependencies'] = {'code': 'DEPENDENCIES_MISSING' if missing else 'PRESENT_NOT_LINUX_PROOF',
                              'missing_packages': missing}
    try:
        selected = releases.selected()
        if selected is not None:
            result['release'] = {'code': 'VERIFIED', 'artifacts': selected['files_verified']}
            semantic_root = selected['root'] / 'semantic'
        elif mode in ('staging', 'production') or on_vercel():
            result['release'] = {'code': 'RELEASE_SELECTION_REQUIRED'}
            semantic_root = None
        else:
            result['release'] = {'code': 'LOCAL_ORIGINAL_PATHS_SELECTED'}
            semantic_root = Path(os.environ.get('OCR_SEMANTIC_MODEL_PATH', ROOT / 'models/ocr_mapper/all-MiniLM-L6-v2'))
        missing_files = ([p for p in releases.SEMANTIC if p != 'README.md' and not (semantic_root / p).is_file()]
                         if semantic_root is not None else [])
        result['semantic'] = {'code': ('SEMANTIC_ARTIFACTS_MISSING' if missing_files else
                                      'LOCAL_FILES_PRESENT_NOT_LOADED') if semantic_root is not None else
                                      'SEMANTIC_RELEASE_REQUIRED', 'missing_artifacts': missing_files}
    except releases.ReleaseError as error:
        # ReleaseError values originate only from fixed application diagnostics.
        result['release'] = {'code': str(error) if re.fullmatch(r'RELEASE_[A-Z_]+', str(error)) else 'RELEASE_INVALID'}
        result['semantic'] = {'code': 'SEMANTIC_RELEASE_BLOCKED'}
    approved_load = not (mode in ('staging', 'production') or on_vercel()) or result['release']['code'] == 'VERIFIED'
    if load_semantic and approved_load:
        import offline_ocr_mapper
        status = offline_ocr_mapper.model_status()
        result['semantic'] = {'code': status['diagnostic_code'], 'active': status['active']}
        result['model_loading_tested'] = True
    elif load_semantic:
        result['semantic'] = {'code': 'SEMANTIC_RELEASE_BLOCKED', 'active': False}
    result['native'] = native_probe() if native else {'code': 'NOT_PROBED'}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--load-semantic', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(check(native=args.native, load_semantic=args.load_semantic), sort_keys=True))
    except Exception:
        print(json.dumps({'code': 'RUNTIME_DIAGNOSTICS_UNAVAILABLE', 'database_contacted': False}))
        raise SystemExit(1) from None
