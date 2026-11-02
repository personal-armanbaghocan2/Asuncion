"""Offline runtime/security checks with explicit synthetic startup settings."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT)]
sys.dont_write_bytecode = True


def guard(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'}:
        raise PermissionError('NETWORK_DENIED')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace('\\', '/').lower()
        private_data_root = str(ROOT/'data').replace('\\', '/').lower()+'/'
        if path.startswith(private_data_root) or any(name in path for name in ('/training_data/', '/legacy_training/', '/sample_files/',
                                          'human_review', 'labeling_pilot', 'legacy_seed.sql')) or path.endswith('/.env'):
            raise PermissionError('PRIVATE_DATA_DENIED')


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--report'); args = parser.parse_args()
    destination = (ROOT/args.report).resolve() if args.report else None
    if destination and (not destination.is_relative_to(ROOT) or destination.exists()): raise ValueError('New repository-local report required.')
    sys.addaudithook(guard)
    names = ('test_online_runtime_readiness', 'test_csrf_security', 'test_session_secret_configuration',
             'test_security_phase4', 'test_isolation_harness_contract', 'test_publication_packaging')
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)) for name in names)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    evidence = dict(scope='OFFLINE SYNTHETIC RUNTIME/SECURITY', tests_run=result.testsRun,
                    failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                    production_database_access=False, external_network=False, deployed=False)
    if destination:
        with destination.open('x', encoding='utf-8') as stream: json.dump(evidence, stream, indent=2); stream.write('\n')
    print('ONLINE_READINESS_TESTS '+json.dumps(evidence))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__': raise SystemExit(main())
