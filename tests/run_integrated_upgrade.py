"""Essential integrated-upgrade regressions; synthetic inputs and SQL doubles only.

No cloud, real database, installed-model verification, migration or deployment.
"""
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT)]
sys.dont_write_bytecode = True

MODULES = (
    'test_integrated_upgrade',
    'test_aoq_batch3_supplier_review',
    'test_multi_item_review_routes',
    'test_nlp_comparison_routes',
    'test_f03_persistent_workbooks',
    'test_aoq_missing_review_contract',
    'test_aoq_excel_review_mapping',
    'test_iar_atomic_approval',
    'test_ris_ics_atomic_approval',
)
EXCLUDED = {'test_installed_fasttext_inference_and_real_verification_post_are_unchanged'}


def selected(suite):
    for test in suite:
        if isinstance(test,unittest.TestSuite):
            yield from selected(test)
        elif test._testMethodName not in EXCLUDED:
            yield test


def main():
    from run_online_readiness import guard
    sys.addaudithook(guard)
    import psycopg
    environment = {key: os.environ[key] for key in
                   ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    environment.update(PSMS_ENV='test', PSMS_DURABLE_WORKBOOKS_ENABLED='0',
                       HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    with patch.dict(os.environ, environment, clear=True), patch.object(
            psycopg, 'connect', side_effect=AssertionError('REAL DATABASE FORBIDDEN')):
        suite = unittest.TestSuite()
        for name in MODULES:
            print('Loading ' + name, flush=True)
            suite.addTests(selected(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name))))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    report = dict(tests_run=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped),
                  scope='Synthetic actual Flask routes, templates and SQL doubles',
                  network=False, real_database=False, models_reverified=False, deployed=False,
                  excluded_installed_model_checks=sorted(EXCLUDED))
    directory = ROOT / 'output' / 'integrated_upgrade_20261103'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report), flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
