"""Focused implementation checks; no build, real model inference or database."""
import importlib
import argparse
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initial-only',action='store_true')
    args=parser.parse_args()
    from run_online_readiness import guard
    sys.addaudithook(guard)
    import psycopg
    environment={k:os.environ[k] for k in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP') if k in os.environ}
    environment.update(PSMS_ENV='test',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    with patch.dict(os.environ,environment,clear=True), \
         patch.object(psycopg,'connect',side_effect=AssertionError('REAL DATABASE FORBIDDEN')):
        names=([
            'test_vercel_preparation.InitialProductionTests',
            'test_f03_persistent_workbooks.PersistentWorkbookTests.test_production_route_upload_uses_database_and_refreshes_without_local_write']
            if args.initial_only else [
            'test_online_delivery.ContextTests','test_online_delivery.ProductionRFTests',
            'test_online_delivery.ContainerContractTests',
            'test_vercel_preparation.AssetBuildTests.test_configuration_keeps_real_entrypoint_and_cloud_fence',
            'test_f03_persistent_workbooks.PersistentWorkbookTests.test_production_route_upload_uses_database_and_refreshes_without_local_write'])
        suite=unittest.defaultTestLoader.loadTestsFromNames(names)
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    report=dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        database=False,network=False,linux_or_docker_run=False,deployed=False)
    folder=ROOT/('output/first_production_checks' if args.initial_only else 'output/online_delivery')
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'checks.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__=='__main__':
    raise SystemExit(main())
