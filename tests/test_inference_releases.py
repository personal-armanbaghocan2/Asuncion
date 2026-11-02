"""Synthetic, isolated release fixtures. Never load fixture bytes with pickle."""
import copy
import hashlib
import json
import os
import platform
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import inference_releases as r
from deploy import inference_release as tool
from deploy.build_vercel import build

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='.inference-release-test-', dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.identifier = 'synthetic-v1'
        artifacts = {}
        for target, source in r.SOURCES.items():
            path = self.base / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('SYNTHETIC NON-MODEL ' + target).encode())
            artifacts[target] = {'source': source, 'sha256': r.sha256(path), 'bytes': path.stat().st_size}
        self.contract = {'schema': r.SCHEMA, 'release_id': self.identifier, 'model_version': 'synthetic-1',
            'profiles': r.PROFILES, 'feature_contract': copy.deepcopy(r.CONTRACTS),
            'runtime': {'python_implementation': 'CPython', 'python_major': 3,
                        'locally_verified_python': platform.python_version(), 'linux_verified': True,
                        'packages': {p: 'synthetic-version' for p in r.PACKAGES}},
            'approval': {'status': 'local_validation_only', 'approved_profiles': [],
                         'quality_review': None, 'deployment_review': None}, 'artifacts': artifacts}
        self.write_contract()
        tool.export(self.base)
        self.root = self.base / 'inference_releases' / self.identifier

    def write_contract(self):
        (self.base / r.SOURCE_CONTRACT).write_bytes(r.canonical(self.contract))
        self.manifest = tool.manifest_from_contract(self.contract)
        self.entry = {'directory': 'inference_releases/' + self.identifier,
            'model_version': self.contract['model_version'],
            'manifest_sha256': hashlib.sha256(r.canonical(self.manifest)).hexdigest(),
            'approval': self.contract['approval']}
        self.write_registry()

    def write_registry(self):
        (self.base / r.REGISTRY).write_bytes(r.canonical(
            {'schema': r.SCHEMA, 'releases': {self.identifier: self.entry}}))

    def verify(self, **kwargs):
        return r.verify(self.identifier, base=self.base, dependencies=False, **kwargs)

    def pin_modified_manifest(self):
        (self.root / r.MANIFEST).write_bytes(r.canonical(self.manifest))
        self.entry['manifest_sha256'] = r.sha256(self.root / r.MANIFEST)
        self.write_registry()

    def test_export_contains_only_allowlisted_inference_files_and_sidecar(self):
        report = self.verify()
        self.assertEqual(report['files_verified'], 28)
        self.assertTrue((self.root / 'legacy/fasttext_model.model.wv.vectors_ngrams.npy').is_file())
        self.assertEqual(len(list((self.root / 'rf').rglob('*.joblib'))), 2)
        self.assertFalse((self.root / 'rf/artifact_manifest.json').exists())

    def test_export_preserves_originals_and_refuses_overwrite(self):
        before = {p: r.sha256(self.base / p) for p in r.SOURCES.values()}
        with self.assertRaisesRegex(r.ReleaseError, 'ALREADY_EXISTS'):
            tool.export(self.base)
        self.assertEqual(before, {p: r.sha256(self.base / p) for p in before})

    def test_export_rejects_a_conflicting_requested_release(self):
        with self.assertRaisesRegex(r.ReleaseError, 'EXPORT_SELECTION'):
            tool.export(self.base, expected_id='different-requested-id')

    def test_missing_artifact(self):
        (self.root / 'legacy/fasttext_model.model.wv.vectors_ngrams.npy').unlink()
        with self.assertRaisesRegex(r.ReleaseError, 'UNEXPECTED_FILE|MISSING'):
            self.verify()

    def test_corrupt_same_size_artifact(self):
        path = self.root / 'rf' / r.HEAD / 'status.joblib'
        path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(r.ReleaseError, 'CHECKSUM'):
            self.verify()

    def test_manifest_cannot_approve_itself(self):
        self.manifest['approval']['status'] = 'approved'
        (self.root / r.MANIFEST).write_bytes(r.canonical(self.manifest))
        with self.assertRaisesRegex(r.ReleaseError, 'MANIFEST_NOT_PINNED'):
            self.verify()

    def test_wrong_feature_contract_even_if_manifest_pinned(self):
        self.manifest['feature_contract']['retrained_rf']['issue_features'] = 560
        self.pin_modified_manifest()
        with self.assertRaisesRegex(r.ReleaseError, 'FEATURE_CONTRACT'):
            self.verify()

    def test_wrong_model_version(self):
        self.manifest['model_version'] = 'unapproved-new-model'
        self.pin_modified_manifest()
        with self.assertRaisesRegex(r.ReleaseError, 'MODEL_VERSION'):
            self.verify()

    def test_wrong_release_id(self):
        self.manifest['release_id'] = 'other-v1'
        self.pin_modified_manifest()
        with self.assertRaisesRegex(r.ReleaseError, 'VERSION_MISMATCH'):
            self.verify()

    def test_incompatible_dependencies(self):
        with patch.object(r.importlib.metadata, 'version', return_value='different'):
            with self.assertRaisesRegex(r.ReleaseError, 'DEPENDENCY_VERSION'):
                r.verify(self.identifier, base=self.base)

    def test_missing_package_is_safe_failure(self):
        with patch.object(r.importlib.metadata, 'version', side_effect=LookupError('PRIVATE DETAIL')):
            with self.assertRaisesRegex(r.ReleaseError, '^RELEASE_VERIFICATION_FAILED$'):
                r.verify(self.identifier, base=self.base)

    def test_incompatible_python_is_rejected_before_loading(self):
        with patch.object(r.platform, 'python_implementation', return_value='PyPy'):
            with self.assertRaisesRegex(r.ReleaseError, 'PYTHON_VERSION'):
                r.verify(self.identifier, base=self.base)

    def test_prepare_creates_only_unapproved_review_proposals(self):
        before = (self.base / r.REGISTRY).read_bytes()
        report = tool.prepare('future-v2', 'future-synthetic-version', base=self.base)
        proposal = r.read_json(self.base / 'output/inference_releases/future-v2/sources.PROPOSED.json')
        self.assertTrue(report['proposed'])
        self.assertEqual(proposal['approval']['approved_profiles'], [])
        self.assertFalse(proposal['runtime']['linux_verified'])
        self.assertEqual(before, (self.base / r.REGISTRY).read_bytes())

    def test_unexpected_file_rejected(self):
        (self.root / 'private-training.csv').write_bytes(b'SYNTHETIC')
        with self.assertRaisesRegex(r.ReleaseError, 'UNEXPECTED_FILE'):
            self.verify()

    def test_unsafe_paths(self):
        for name in ('../private', '/private', 'C:/private', 'a\\b', './a', 'a//b'):
            with self.subTest(name=name), self.assertRaises(r.ReleaseError):
                r.safe_path(self.base, name, exists=False)

    def test_symlink_is_rejected_without_reading_target(self):
        target = self.root / 'legacy/labels.json'
        with patch.object(Path, 'is_symlink', lambda p: p == target):
            with self.assertRaisesRegex(r.ReleaseError, 'LINK_FORBIDDEN'):
                r.safe_path(self.root, 'legacy/labels.json')

    def test_unknown_release_and_bad_identifier(self):
        for identifier in ('not-reviewed', '../escape'):
            with self.subTest(identifier=identifier), self.assertRaises(r.ReleaseError):
                r.verify(identifier, base=self.base)

    def test_local_candidate_cannot_activate_online(self):
        with self.assertRaisesRegex(r.ReleaseError, 'ACTIVATION_BLOCKED'):
            self.verify(deployment=True)

    def test_approval_is_profile_specific(self):
        self.manifest['approval'] = {'status': 'approved', 'approved_profiles': ['legacy', 'semantic_ocr'],
            'quality_review': 'SYNTHETIC fixture review', 'deployment_review': 'SYNTHETIC fixture review'}
        self.entry['approval'] = copy.deepcopy(self.manifest['approval'])
        self.pin_modified_manifest()
        self.verify(deployment=True)
        with self.assertRaisesRegex(r.ReleaseError, 'ACTIVATION_BLOCKED'):
            self.verify(deployment=True, profiles=('retrained_rf', 'semantic_ocr'))

    def test_source_hash_change_refuses_before_output(self):
        # A second version remains isolated; source trust is not generated by export.
        self.contract['release_id'] = self.identifier = 'synthetic-v2'
        self.write_contract()
        (self.base / next(iter(r.SOURCES.values()))).write_bytes(b'changed source')
        with self.assertRaisesRegex(r.ReleaseError, 'SOURCE_CHANGED'):
            tool.export(self.base)
        self.assertFalse((self.base / 'inference_releases/synthetic-v2').exists())

    def test_copy_failure_rolls_back_staging(self):
        self.contract['release_id'] = self.identifier = 'synthetic-v2'
        self.write_contract()
        with patch.object(tool.shutil, 'copyfile', side_effect=OSError('synthetic copy failure')):
            with self.assertRaises(OSError):
                tool.export(self.base)
        self.assertEqual([p.name for p in (self.base / 'inference_releases').iterdir()], ['synthetic-v1'])

    def test_deterministic_archive_and_repeat_verification(self):
        self.assertEqual(tool.archive_bytes(self.root), tool.archive_bytes(self.root))
        self.assertEqual(self.verify(), self.verify())

    def test_publication_failure_rolls_back_owned_output(self):
        self.contract['release_id'] = self.identifier = 'synthetic-v2'
        self.write_contract()
        real = tool._rename_owned
        calls = []
        def fail_second(source, destination):
            calls.append(source)
            if len(calls) == 2:
                raise OSError('SYNTHETIC interrupted publication')
            real(source, destination)
        with patch.object(tool, '_rename_owned', side_effect=fail_second):
            with self.assertRaises(OSError):
                tool.export(self.base)
        self.assertEqual([p.name for p in (self.base / 'inference_releases').iterdir()], ['synthetic-v1'])

    def test_registry_and_manifest_approval_must_match(self):
        self.entry['approval'] = {'status': 'approved'}
        self.write_registry()
        with self.assertRaisesRegex(r.ReleaseError, 'APPROVAL_MISMATCH'):
            self.verify()

    def test_pack_never_changes_trust_or_approval(self):
        before = (self.base / r.REGISTRY).read_bytes()
        report = tool.pack(self.identifier, self.base)
        self.assertFalse(report['published'])
        self.assertEqual(before, (self.base / r.REGISTRY).read_bytes())
        with self.assertRaises(FileExistsError):
            tool.pack(self.identifier, self.base)

    def test_retrieval_requires_approval_before_network(self):
        with patch.object(tool.urllib.request, 'build_opener') as network:
            with self.assertRaisesRegex(r.ReleaseError, 'ACTIVATION_BLOCKED'):
                tool.retrieve(self.identifier, base=self.base)
        network.assert_not_called()

    def test_archive_hash_failure_preserves_existing_release(self):
        before = r.sha256(self.root / r.MANIFEST)
        with self.assertRaisesRegex(r.ReleaseError, 'ARCHIVE_CHECKSUM'):
            tool.install_archive(b'not trusted', self.identifier, base=self.base, deployment=False)
        self.assertEqual(before, r.sha256(self.root / r.MANIFEST))

    def test_valid_archive_installs_and_preserves_sources(self):
        payload = tool.archive_bytes(self.root)
        self.entry['delivery'] = {'archive_sha256': hashlib.sha256(payload).hexdigest(),
                                  'archive_bytes': len(payload)}
        self.write_registry()
        # Remove only this test's synthetic output, never project models.
        tool.shutil.rmtree(self.root)
        report = tool.install_archive(payload, self.identifier, base=self.base, deployment=False)
        self.assertEqual(report['files_verified'], 28)
        self.verify()

    def test_build_requires_explicit_release_before_assets(self):
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': ''}):
            with self.assertRaisesRegex(r.ReleaseError, 'SELECTION_REQUIRED'):
                build(self.base)
        self.assertFalse((self.base / 'public').exists())

    def test_build_blocks_candidate_before_assets(self):
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': self.identifier}):
            with self.assertRaisesRegex(r.ReleaseError, 'ACTIVATION_BLOCKED'):
                build(self.base)
        self.assertFalse((self.base / 'public').exists())

    def test_explicit_invalid_release_does_not_fall_back(self):
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': 'missing-v1', 'PSMS_ENV': 'test'}):
            with self.assertRaisesRegex(r.ReleaseError, 'NOT_TRUSTED'):
                r.selected(base=self.base)

    def test_git_deployment_pointer_is_inactive_for_local_development(self):
        registry = r.read_json(self.base / r.REGISTRY)
        registry['deployment_release'] = self.identifier
        (self.base / r.REGISTRY).write_bytes(r.canonical(registry))
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': '', 'PSMS_ENV': 'local', 'VERCEL': ''}):
            self.assertIsNone(r.configured_identifier(base=self.base))
            self.assertEqual(r.configured_identifier(base=self.base, deployment=True), self.identifier)

    def test_unapproved_git_pointer_cannot_activate(self):
        registry = r.read_json(self.base / r.REGISTRY)
        registry['deployment_release'] = self.identifier
        (self.base / r.REGISTRY).write_bytes(r.canonical(registry))
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': '', 'PSMS_ENV': 'staging'}):
            with self.assertRaisesRegex(r.ReleaseError, 'ACTIVATION_BLOCKED'):
                r.selected(base=self.base)

    def test_selection_default_preserves_local_workflow(self):
        from nlp_runtime_selection import inspect_selection, model_root
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': '', 'PSMS_NLP_RUNTIME': 'legacy'}):
            self.assertEqual(inspect_selection({'PSMS_ENV': 'test'}, self.base)['runtime'], 'legacy')
            self.assertEqual(model_root({}, 'original-local-directory'), 'original-local-directory')

    def test_changed_release_requires_application_restart(self):
        from nlp_runtime_selection import runtime
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': 'different-v2'}):
            with self.assertRaisesRegex(RuntimeError, 'selection changed'):
                runtime({'PSMS_NLP_RUNTIME': 'legacy', '_PSMS_INFERENCE_RELEASE_ID': self.identifier})

    def test_failed_release_prevents_legacy_deserialization(self):
        import fasttext_nlp_runtime as legacy
        legacy.clear_cache()
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': 'not-approved'}):
            with patch('joblib.load') as loader:
                with self.assertRaisesRegex(RuntimeError, 'failed verification'):
                    legacy.load_assets(str(self.root / 'legacy'))
        loader.assert_not_called()

    def test_failed_release_prevents_rf_deserialization(self):
        import retrained_nlp_runtime as rf
        rf.clear_cache()
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': 'not-approved'}):
            with patch('joblib.load') as loader:
                with self.assertRaises(rf.RetrainedModelError):
                    rf.load_assets(self.root / 'rf')
        loader.assert_not_called()

    def test_failed_release_prevents_semantic_deserialization(self):
        import offline_ocr_mapper as mapper
        with patch.dict(os.environ, {'PSMS_INFERENCE_RELEASE_ID': 'not-approved'}):
            with patch.object(mapper, '_MODEL', None), patch('sentence_transformers.SentenceTransformer') as loader:
                self.assertIsNone(mapper.load_local_model())
        loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
