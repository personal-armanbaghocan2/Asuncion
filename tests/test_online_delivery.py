"""Essential delivery/approval checks only; no SQL, Linux build or real models."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import inference_releases as releases
import nlp_runtime_selection as selection
from deploy import online_delivery as delivery
from test_inference_releases import ReleaseTests


class ContextTests(unittest.TestCase):
    # Reuse only the synthetic-byte fixture setup, not the earlier test campaign.
    setUp=ReleaseTests.setUp
    write_contract=ReleaseTests.write_contract
    write_registry=ReleaseTests.write_registry
    pin_modified_manifest=ReleaseTests.pin_modified_manifest

    def test_unapproved_models_cannot_create_a_delivery_context(self):
        target=self.base/'output/new-context'
        with self.assertRaisesRegex(releases.ReleaseError,'ACTIVATION_BLOCKED'):
            delivery.context(target,self.identifier,base=self.base)
        self.assertFalse(target.exists())

    def test_approved_context_contains_source_and_pinned_metadata_without_weights(self):
        approval=dict(status='approved',approved_profiles=['legacy','semantic_ocr'],
                      quality_review='SYNTHETIC-QUALITY',deployment_review='SYNTHETIC-DEPLOYMENT')
        self.manifest['approval']=self.entry['approval']=approval
        self.entry['manifest']=self.manifest
        self.entry['delivery']=dict(url='https://synthetic.invalid/immutable.zip',
            archive_bytes=20000,archive_sha256='0'*64)
        self.pin_modified_manifest()
        (self.base/'app.py').write_text('SYNTHETIC APPLICATION SOURCE')
        (self.base/'Dockerfile.vercel').write_text('ENV PSMS_INFERENCE_ENABLED=0\nCOPY runtime/ /app/\n')
        (self.base/'vercel.json').write_text('{}')
        (self.base/'.vercelignore').write_text('**\n!runtime/\n!runtime/**\n')
        (self.base/'UNREVIEWED-CANARY.txt').write_text('SYNTHETIC-PRIVATE-CANARY')
        (self.base/'unreviewed-files').mkdir()
        (self.base/'unreviewed-files/private.txt').write_text('SYNTHETIC-PRIVATE-CANARY')
        before=(self.base/releases.REGISTRY).read_bytes()
        target=self.base/'output/approved-context'
        with patch.object(delivery,'source_files',return_value={'app.py',releases.REGISTRY,releases.SOURCE_CONTRACT}):
            report=delivery.context(target,self.identifier,base=self.base)
        self.assertFalse(report['published'])
        self.assertEqual((self.base/releases.REGISTRY).read_bytes(),before)
        self.assertFalse((target/'runtime/.env').exists())
        self.assertFalse((target/'runtime/uploads').exists())
        self.assertEqual(releases.read_json(target/'runtime'/releases.REGISTRY)['deployment_release'],self.identifier)
        evidence=releases.metadata(self.identifier,base=target/'runtime',dependencies=False)
        self.assertEqual(len(evidence['manifest']['artifacts']),28)
        self.assertFalse((target/'runtime/inference_releases').exists())
        self.assertEqual(report['model_bytes_uploaded'],0)
        self.assertLess(report['source_bytes'],delivery.SOURCE_UPLOAD_LIMIT)
        self.assertNotIn('SYNTHETIC-PRIVATE-CANARY',''.join(p.read_text(errors='ignore')
            for p in target.rglob('*') if p.is_file()))

    def test_archive_and_context_cannot_overwrite_or_escape_private_output(self):
        for target in (self.base/'app.py', self.base.parent/'unowned'):
            with self.assertRaisesRegex(releases.ReleaseError,'NEW_PRIVATE_OUTPUT'):
                delivery.destination(target,base=self.base)


class ProductionRFTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'output')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.environment=dict(PSMS_ENV='production',PSMS_NLP_RUNTIME='retrained_rf',
            PSMS_PRODUCTION_DB_ENABLED='1',PSMS_SECRET_KEY='SYNTHETIC-SESSION-KEY',
            PSMS_INFERENCE_RELEASE_ID='synthetic-approved')
        env=patch.dict(os.environ,self.environment,clear=True)
        env.start();self.addCleanup(env.stop)

    def test_unapproved_release_cannot_reach_production_rf_or_staging_database(self):
        with patch.object(releases,'selected',side_effect=releases.ReleaseError('RELEASE_ACTIVATION_BLOCKED')), \
             patch.object(selection,'verify_staging_database') as database:
            with self.assertRaisesRegex(releases.ReleaseError,'ACTIVATION_BLOCKED'):
                selection.inspect_selection({'PSMS_ENV':'production'},self.root)
        database.assert_not_called()

    def test_verified_production_release_uses_its_rf_directory_without_synthetic_database(self):
        verified=dict(root=self.root,manifest={'release_id':'synthetic-approved'})
        with patch.object(releases,'selected',return_value=verified), \
             patch.object(selection,'_staging_settings',side_effect=AssertionError('NO STAGING')):
            result=selection.inspect_selection({'PSMS_ENV':'production'},self.root)
        self.assertTrue(result['production_release'])
        self.assertIsNone(result['stage'])
        self.assertEqual(result['root'],str(self.root/'rf'))

    def test_production_rf_still_requires_database_activation_and_session_csrf_guards(self):
        verified=dict(root=self.root,manifest={'release_id':'synthetic-approved'})
        with patch.object(releases,'selected',return_value=verified), \
             patch.dict(os.environ,{'PSMS_PRODUCTION_DB_ENABLED':'0'}):
            with self.assertRaisesRegex(RuntimeError,'production database activation'):
                selection.inspect_selection({'PSMS_ENV':'production'},self.root)
        configured=dict(PSMS_ENV='production',PSMS_NLP_RUNTIME='retrained_rf',
            _PSMS_INFERENCE_RELEASE_ID='synthetic-approved',_PSMS_RF_PRODUCTION_RELEASE='synthetic-approved',
            _PSMS_NLP_BASE_DIR=str(self.root),PSMS_RETRAINED_NLP_DIR=str(self.root/'rf'))
        with patch.object(releases,'selected',return_value=verified):
            with self.assertRaisesRegex(RuntimeError,'session and CSRF protections'):
                selection.runtime(configured)

    def test_approved_rf_returns_original_classifier_runtime_with_secure_configuration(self):
        verified=dict(root=self.root,manifest={'release_id':'synthetic-approved'})
        configured=dict(PSMS_ENV='production',PSMS_NLP_RUNTIME='retrained_rf',
            _PSMS_INFERENCE_RELEASE_ID='synthetic-approved',_PSMS_RF_PRODUCTION_RELEASE='synthetic-approved',
            _PSMS_NLP_BASE_DIR=str(self.root),PSMS_RETRAINED_NLP_DIR=str(self.root/'rf'),
            WTF_CSRF_ENABLED=True,SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax',DEBUG=False,TESTING=False,TRUSTED_HOSTS=['synthetic.invalid'],
            SECRET_KEY=os.environ['PSMS_SECRET_KEY'])
        with patch.object(releases,'selected',return_value=verified):
            self.assertEqual(selection.runtime(configured).__name__,'retrained_nlp_runtime')
            configured['WTF_CSRF_ENABLED']=False
            with self.assertRaisesRegex(RuntimeError,'session and CSRF protections'):
                selection.runtime(configured)


class ContainerContractTests(unittest.TestCase):
    def test_native_runtime_build_keeps_models_offline_and_secrets_out(self):
        text=(ROOT/'Dockerfile.vercel').read_text()
        self.assertIn('tesseract-ocr tesseract-ocr-eng',text)
        self.assertIn('COPY runtime/ /app/',text)
        self.assertIn('RUN python -B deploy/verify_online_image.py',text)
        self.assertIn('HF_HUB_OFFLINE=1',text)
        self.assertNotIn('COPY . .',text)
        self.assertNotIn('PSMS_SECRET_KEY=',text)
        self.assertNotIn('SUPABASE_DB_URL=',text)
        self.assertEqual(json.loads((ROOT/'vercel.json').read_text())['framework'],'container')


if __name__=='__main__':
    unittest.main(verbosity=2)
