"""Synthetic packaging regressions. No network, database or model execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import inference_releases as releases
from deploy import embedded_model_delivery as delivery


class EmbeddedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'runtime').mkdir()
        self.entry = {'synthetic': True}
        self.descriptor = {'release_id': 'nlp-online-original-v2',
            'manifest_sha256': 'a' * 64, 'image': delivery.IMAGE_PREFIX + 'b' * 64}
        self.path = self.root / 'image.json'
        (self.root / 'runtime' / releases.REGISTRY).write_text(json.dumps({
            'deployment_release': self.descriptor['release_id'],
            'releases': {self.descriptor['release_id']: self.entry}}))
        self.original = ('FROM python:3.13.16-slim-bookworm\n'
            'ENV PSMS_INFERENCE_ENABLED=1 PSMS_NLP_RUNTIME=retrained_rf '
            'PSMS_INFERENCE_CACHE_ENABLED=1\nCOPY runtime/ /app/\nUSER psms\n'
            'RUN python -B deploy/verify_online_image.py\n')
        (self.root / 'Dockerfile.vercel').write_text(self.original)
        for manager in (patch.object(releases, 'metadata', return_value={
                'manifest_sha256': self.descriptor['manifest_sha256']}),
                patch.object(releases, 'entry_for', return_value=self.entry)):
            manager.start()
            self.addCleanup(manager.stop)

    def apply(self):
        self.path.write_text(json.dumps(self.descriptor))
        return delivery.apply_image(self.root, self.path)

    def test_bundles_before_nonroot_gate_and_disables_runtime_retrieval(self):
        report = self.apply()
        docker = (self.root / 'Dockerfile.vercel').read_text()
        self.assertLess(docker.index('COPY --from='), docker.index('USER psms'))
        self.assertIn('ENV PSMS_INFERENCE_CACHE_ENABLED=0', docker)
        self.assertIn('e=r.selected()', docker)
        self.assertIn('deploy/verify_online_image.py', docker)
        self.assertFalse(report['runtime_model_downloads'])

    def test_mutable_cross_project_or_injected_images_leave_context_untouched(self):
        for image in ('vcr.vercel.com/arman-a87f/asuncion/approved-models:latest',
                delivery.IMAGE_PREFIX.replace('/asuncion/', '/another/') + 'b' * 64,
                delivery.IMAGE_PREFIX + 'b' * 64 + '\nRUN false'):
            with self.subTest(image=image):
                self.descriptor['image'] = image
                with self.assertRaisesRegex(releases.ReleaseError, 'IMAGE_PIN_REQUIRED'):
                    self.apply()
                self.assertEqual((self.root / 'Dockerfile.vercel').read_text(), self.original)

    def test_wrong_manifest_and_context_release_are_rejected(self):
        self.descriptor['manifest_sha256'] = 'c' * 64
        with self.assertRaisesRegex(releases.ReleaseError, 'IMAGE_PIN_REQUIRED'):
            self.apply()
        self.descriptor['manifest_sha256'] = 'a' * 64
        (self.root / 'runtime' / releases.REGISTRY).write_text('{}')
        with self.assertRaisesRegex(releases.ReleaseError, 'CONTEXT_RELEASE_MISMATCH'):
            self.apply()

    def test_repeat_application_fails_without_another_copy(self):
        self.apply()
        before = (self.root / 'Dockerfile.vercel').read_bytes()
        with self.assertRaisesRegex(releases.ReleaseError, 'CONTEXT_INVALID'):
            self.apply()
        self.assertEqual((self.root / 'Dockerfile.vercel').read_bytes(), before)

    def test_supported_containerfile_preserves_docker_instructions_and_runtime(self):
        self.apply()
        before = (self.root / 'Dockerfile.vercel').read_bytes()
        for name in ('.dockerignore', '.vercelignore'):
            (self.root / name).write_text('**\n!runtime/\n!runtime/**\n')
        registry = (self.root / 'runtime' / releases.REGISTRY).read_bytes()
        delivery.use_containerfile(self.root)
        self.assertFalse((self.root / 'Dockerfile.vercel').exists())
        self.assertEqual((self.root / 'Containerfile.vercel').read_bytes(), before)
        self.assertEqual((self.root / 'runtime' / releases.REGISTRY).read_bytes(), registry)
        for name in ('.dockerignore', '.vercelignore'):
            self.assertIn('!Containerfile.vercel', (self.root / name).read_text())
