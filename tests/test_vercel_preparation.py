"""Offline asset/build and actual WSGI checks. No database, models or listener.

All environment settings and credentials below are synthetic process-local
fixtures. Build writes are confined to owned temporary directories.
"""
import ast
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.build_vercel_assets import ASSETS, build_assets


class AssetBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='.vercel-assets-test-', dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ASSETS:
            source = self.root / 'static' / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(('SYNTHETIC ' + name).encode())

    def test_reviewed_assets_copy_exactly_and_build_is_idempotent(self):
        report = build_assets(self.root)
        self.assertEqual(report['assets'], len(ASSETS))
        for name in ASSETS:
            self.assertEqual((self.root / 'static' / name).read_bytes(),
                             (self.root / 'public/static' / name).read_bytes())
        self.assertEqual(build_assets(self.root), report)

    def test_check_does_not_create_public_directory(self):
        self.assertTrue(build_assets(self.root, check=True)['check_only'])
        self.assertFalse((self.root / 'public').exists())

    def test_missing_input_refuses_before_any_output(self):
        (self.root / 'static' / ASSETS[-1]).unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            build_assets(self.root)
        self.assertFalse((self.root / 'public').exists())

    def test_conflicting_destination_is_preserved(self):
        target = self.root / 'public/static' / ASSETS[0]
        target.parent.mkdir(parents=True)
        target.write_bytes(b'UNRELATED EXISTING FILE')
        with self.assertRaisesRegex(RuntimeError, 'conflicts'):
            build_assets(self.root)
        self.assertEqual(target.read_bytes(), b'UNRELATED EXISTING FILE')
        self.assertFalse((self.root / 'public/static' / ASSETS[1]).exists())

    def test_unreviewed_public_file_refuses_build(self):
        path = self.root / 'public/private-document.txt'
        path.parent.mkdir()
        path.write_text('SYNTHETIC PRIVATE FIXTURE')
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
            build_assets(self.root)
        self.assertFalse((self.root / 'public/static').exists())

    def test_private_source_files_are_never_copied(self):
        private = self.root / 'static/private-signature.png'
        private.write_bytes(b'SYNTHETIC PRIVATE FIXTURE')
        build_assets(self.root)
        actual = {p.relative_to(self.root / 'public').as_posix()
                  for p in (self.root / 'public').rglob('*') if p.is_file()}
        self.assertEqual(actual, {'static/' + name for name in ASSETS})

    def test_explicit_generated_refresh_preserves_old_bytes_before_replacement(self):
        build_assets(self.root)
        target = self.root / 'public/static' / ASSETS[0]
        target.write_bytes(b'SYNTHETIC OLD GENERATED COPY')
        result = build_assets(self.root, refresh_generated=True)
        self.assertEqual(result['refreshed'], 1)
        backup = self.root / 'output/vercel_asset_backups' / result['backup_id'] / 'static' / ASSETS[0]
        self.assertEqual(backup.read_bytes(), b'SYNTHETIC OLD GENERATED COPY')
        self.assertEqual(target.read_bytes(), (self.root / 'static' / ASSETS[0]).read_bytes())
        self.assertEqual(build_assets(self.root, refresh_generated=True)['refreshed'], 0)

    def test_generated_refresh_still_refuses_unreviewed_public_files(self):
        build_assets(self.root)
        (self.root / 'public/private-document.txt').write_bytes(b'SYNTHETIC')
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
            build_assets(self.root, refresh_generated=True)
        self.assertFalse((self.root / 'output').exists())

    def test_original_assets_and_template_references_are_covered(self):
        # A clean Git deployment has no ignored public/static cache. Validate
        # original source bytes in an owned clean build root; leave any stale
        # generated copies in the developer's workspace untouched.
        with tempfile.TemporaryDirectory(prefix='.vercel-original-assets-', dir=ROOT) as folder:
            clean = Path(folder)
            for name in ASSETS:
                target = clean / 'static' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / 'static' / name).read_bytes())
            report = build_assets(clean, check=True)
        self.assertEqual(report['assets'], 18)
        for template in (ROOT / 'templates').glob('*.html'):
            text = template.read_text(encoding='utf-8-sig')
            for name in re.findall(r"url_for\('static',\s*filename='([^']+)'\)", text):
                self.assertIn(name, ASSETS, (template.name, name))

    def test_configuration_keeps_real_entrypoint_and_cloud_fence(self):
        config = json.loads((ROOT / 'vercel.json').read_text())
        self.assertEqual(config['framework'], 'container')
        self.assertTrue(config['fluid'])
        self.assertFalse(config['git']['deploymentEnabled'])
        self.assertIn('python", "serve.py', (ROOT/'Dockerfile.vercel').read_text())
        # Production secrets/profile belong to the existing project's scoped env.
        self.assertNotIn('env', config)
        self.assertNotIn('functions', config)
        self.assertNotIn('buildCommand', config)
        self.assertNotIn('rewrites', config)
        self.assertNotIn('builds', config)
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8-sig'))
        self.assertTrue(any(isinstance(n, ast.Assign) and
            any(isinstance(t, ast.Name) and t.id == 'app' for t in n.targets)
            and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
            and n.value.func.id == 'Flask' for n in tree.body))


class InitialProductionTests(unittest.TestCase):
    """Only initial website boot/off-switch/context; no live SQL/models/network."""
    @classmethod
    def setUpClass(cls):
        from test_database_configuration_isolation import cloud_environment
        import inference_releases
        cls.stack=ExitStack();cls.addClassCleanup(cls.stack.close)
        root=Path(cls.stack.enter_context(tempfile.TemporaryDirectory(prefix='.first-online-',dir=ROOT)))
        certificate=root/'synthetic-ca.crt';certificate.write_text('SYNTHETIC CONFIG ONLY')
        environment=cloud_environment(certificate,mode='production')
        environment.update(PSMS_SECRET_KEY='synthetic-first-production-session-key-not-live',
            VERCEL='1',PSMS_PRODUCTION_DB_ENABLED='1',PSMS_DURABLE_WORKBOOKS_ENABLED='1',
            PSMS_INFERENCE_ENABLED='0',PSMS_INFERENCE_CACHE_ENABLED='1',PSMS_DATA_DIR=str(root))
        cls.stack.enter_context(patch.dict(os.environ,environment,clear=True))
        cls.driver=cls.stack.enter_context(patch('psycopg.connect',side_effect=AssertionError('NO LIVE SQL')))
        cls.stack.enter_context(patch('db.apply_schema',side_effect=AssertionError('NO INITIALIZATION')))
        cls.metadata=cls.stack.enter_context(patch.object(inference_releases,'metadata',
            side_effect=AssertionError('DISABLED MODE MUST NOT INSPECT OR DOWNLOAD MODELS')))
        spec=importlib.util.spec_from_file_location('_initial_production_app',ROOT/'app.py')
        module=importlib.util.module_from_spec(spec)
        cls.stack.enter_context(patch.dict(sys.modules,{spec.name:module,'app':module}))
        spec.loader.exec_module(module)
        cls.module=module;cls.app=module.app

    def tearDown(self):
        self.driver.assert_not_called()

    def test_real_startup_login_assets_auth_and_durable_configuration(self):
        import durable_workbooks
        client=self.app.test_client()
        self.assertFalse(self.app.config['PSMS_INFERENCE_ENABLED'])
        self.assertTrue(durable_workbooks.enabled())
        self.assertEqual(client.get('/health/live',base_url='https://approved.example').json,{'status':'alive'})
        response=client.get('/login',base_url='https://approved.example')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'login-split',response.data);self.assertIn(b'csrf_token',response.data)
        for path in set(re.findall(r'(?:href|src)="(/static/[^"]+)"',response.get_data(as_text=True))):
            asset=client.get(path,base_url='https://approved.example')
            self.assertEqual(asset.status_code,200,path);asset.close()
        for path in ('/','/ppmp','/pr','/aoq','/po','/nlp','/iar','/inventory','/reports','/users'):
            self.assertEqual(client.get(path,base_url='https://approved.example').status_code,302,path)
        self.assertTrue(self.app.config['SESSION_COOKIE_SECURE'])
        self.assertTrue(self.app.config.get('WTF_CSRF_ENABLED',True))

    def test_disabled_processing_rejects_before_models_upload_reads_or_writes(self):
        import inference_releases as releases
        import fasttext_nlp_runtime as legacy
        import retrained_nlp_runtime as rf
        import offline_ocr_mapper as mapper
        import durable_ocr_v0_1_0 as ocr
        from werkzeug.exceptions import ServiceUnavailable
        for loader in (legacy.load_assets,rf.load_assets):
            with self.assertRaisesRegex(releases.ReleaseError,'INFERENCE_DISABLED'):
                loader('NONEXISTENT')
        self.assertIsNone(mapper.load_local_model())
        self.assertFalse(self.module.trained_nlp_model_status()['active'])
        with self.app.test_request_context('/ppmp/DBM/upload_ocr',method='POST'):
            for operation in (lambda:ocr.ppmp_upload({'app':self.app},'DBM'),
                              lambda:ocr.aoq_upload({'app':self.app}),self.module.nlp):
                with self.assertRaises(ServiceUnavailable):operation()
        self.metadata.assert_not_called()

    def test_disabled_source_context_needs_no_private_models_or_release(self):
        from deploy import online_delivery as delivery
        import inference_releases as releases
        self.assertIn('serve.py',delivery.source_files())
        with tempfile.TemporaryDirectory(prefix='.initial-context-',dir=ROOT) as temporary:
            base=Path(temporary)
            (base/'app.py').write_text('SYNTHETIC SOURCE')
            (base/releases.REGISTRY).write_text('{}')
            (base/'Dockerfile.vercel').write_text('ENV PSMS_INFERENCE_ENABLED=0\nCOPY runtime/ /app/\n')
            (base/'vercel.json').write_text('{}')
            (base/'.vercelignore').write_text('**\n!runtime/\n!runtime/**\n')
            with patch.object(delivery,'source_files',return_value={'app.py',releases.REGISTRY}):
                result=delivery.context(base/'output/context',None,base=base,inference_mode='disabled')
            self.assertEqual(result['inference_mode'],'disabled')
            self.assertEqual(result['model_bytes_uploaded'],0)
            registry=releases.read_json(base/'output/context/runtime'/releases.REGISTRY)
            self.assertIsNone(registry['deployment_release']);self.assertEqual(registry['releases'],{})
            self.assertFalse((base/'output/context/runtime/inference_releases').exists())

    def test_enabling_inference_preserves_release_approval_and_restart_guards(self):
        import inference_releases as releases
        import nlp_runtime_selection as selection
        from werkzeug.exceptions import ServiceUnavailable
        with patch.dict(os.environ,{'PSMS_INFERENCE_ENABLED':'1',
                'PSMS_INFERENCE_RELEASE_ID':'nlp-20261101-local-v1'}), \
             patch.object(releases,'metadata',side_effect=releases.ReleaseError('RELEASE_ACTIVATION_BLOCKED')) as check:
            with self.assertRaisesRegex(releases.ReleaseError,'ACTIVATION_BLOCKED'):
                selection.inspect_selection(self.app.config,ROOT)
            check.assert_called_once()
            with self.assertRaises(ServiceUnavailable):selection.runtime(self.app.config)


class ActualWSGITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_database_configuration_isolation import cloud_environment
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        root = Path(cls.stack.enter_context(tempfile.TemporaryDirectory(
            prefix='.vercel-wsgi-test-', dir=ROOT)))
        certificate = root / 'synthetic-ca.crt'
        certificate.write_text('SYNTHETIC CONFIGURATION ONLY; NOT A TRUSTED CA')
        environment = cloud_environment(certificate, mode='staging')
        environment.update(PSMS_SECRET_KEY='synthetic-vercel-test-secret-not-an-actual-key',
                           PSMS_DATA_DIR=str(root), HF_HUB_OFFLINE='1',
                           TRANSFORMERS_OFFLINE='1', HF_HOME=str(root / 'cache'),
                           TORCH_HOME=str(root / 'cache'))
        cls.stack.enter_context(patch.dict(os.environ, environment, clear=True))
        cls.driver = cls.stack.enter_context(patch('psycopg.connect',
            side_effect=AssertionError('DATABASE CONNECTION DENIED')))
        cls.schema = cls.stack.enter_context(patch('db.apply_schema',
            side_effect=AssertionError('INITIALIZATION DENIED')))
        cls.stack.enter_context(patch('socket.socket.connect',
            side_effect=AssertionError('NETWORK DENIED')))
        spec = importlib.util.spec_from_file_location('_vercel_actual_application', ROOT / 'app.py')
        module = importlib.util.module_from_spec(spec)
        cls.stack.enter_context(patch.dict(sys.modules, {spec.name: module, 'app': module}))
        spec.loader.exec_module(module)
        spec = importlib.util.spec_from_file_location('_vercel_actual_wsgi', ROOT / 'wsgi.py')
        wsgi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wsgi)
        cls.application = wsgi.application
        cls.application.config['TESTING'] = True

    def setUp(self):
        self.client = self.application.test_client()

    def tearDown(self):
        self.driver.assert_not_called()
        self.schema.assert_not_called()

    def get(self, path):
        return self.client.get(path, base_url='https://approved.example')

    def test_actual_wsgi_import_and_liveness_without_database(self):
        self.assertEqual(self.get('/health/live').json, {'status': 'alive'})
        self.assertEqual(self.application.config['PSMS_NLP_RUNTIME'], 'legacy')

    def test_original_login_template_and_every_referenced_asset_load(self):
        response = self.get('/login')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'login-split', response.data)
        self.assertIn(b'Municipality of', response.data)
        for path in set(re.findall(r'(?:href|src)="(/static/[^"]+)"', response.get_data(as_text=True))):
            asset = self.get(path)
            self.assertEqual(asset.status_code, 200, path)
            self.assertEqual(asset.data, (ROOT / path.lstrip('/')).read_bytes())
            asset.close()

    def test_anonymous_functional_routes_remain_protected(self):
        for path in ('/', '/ppmp', '/app', '/pr', '/aoq', '/po', '/nlp',
                     '/iar', '/ris-ics', '/inventory', '/reports', '/users', '/audit'):
            response = self.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertTrue(response.location.endswith('/login'), path)
        response = self.client.post('/po/1/approve', base_url='https://approved.example')
        self.assertEqual(response.status_code, 302)

    def test_cloud_database_connection_still_refused(self):
        import db
        with self.assertRaisesRegex(RuntimeError, 'Staging database connections require explicit activation'):
            db.connect_db()

    def test_untrusted_host_rejected(self):
        # Rejected hosts stop before account lookup or redirect URL generation.
        with patch.dict(self.application.config, {'PROPAGATE_EXCEPTIONS': False}), \
                patch.object(self.application.logger, 'disabled', True):
            response = self.client.get('/health/live', base_url='https://unreviewed.example')
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
