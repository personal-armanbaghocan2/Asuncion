"""Trusted export and actual Flask routes; synthetic SQL doubles, no live DB."""
from copy import deepcopy
import importlib.metadata
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from test_nlp_comparison_routes import ActualApplication, SyntheticDatabase
import nlp_runtime_selection as selection
import retrained_nlp_runtime as rf

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'Retrained_Colab_Models/retrained_nlp'
EVIDENCE = {}


class RetrainedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        started = time.perf_counter()
        cls.assets = rf.load_assets(ARTIFACTS)
        EVIDENCE['load_seconds'] = round(time.perf_counter() - started, 4)

    def test_all_checksums_and_head_counts(self):
        report = rf.verify_artifacts(ARTIFACTS)
        self.assertEqual((report['files_verified'], report['heads_verified']), (144, 48))
        EVIDENCE['artifact_verification'] = {k: report[k] for k in
            ('files_verified', 'heads_verified', 'manifest_sha256', 'python_version', 'training_environment')}

    def test_untrusted_manifest_never_deserializes(self):
        with tempfile.TemporaryDirectory() as folder, patch('joblib.load') as load:
            Path(folder, 'artifact_manifest.json').write_text('{"files":{}}')
            with self.assertRaisesRegex(rf.RetrainedModelError, 'MANIFEST_NOT_PINNED'):
                rf.load_assets(folder)
            load.assert_not_called()

    def test_tampered_file_never_deserializes(self):
        original = rf._sha
        def changed(path):
            return '0' * 64 if path.name == 'status.joblib' else original(path)
        with patch.object(rf, '_sha', side_effect=changed), patch('joblib.load') as load:
            with self.assertRaisesRegex(rf.RetrainedModelError, 'CHECKSUM_MISMATCH'):
                rf.verify_artifacts(ARTIFACTS)
            load.assert_not_called()

    def test_missing_artifacts_are_sanitized(self):
        with tempfile.TemporaryDirectory(prefix='SENSITIVE-SYNTHETIC-') as folder:
            with self.assertRaisesRegex(rf.RetrainedModelError, '^RETRAINED_ARTIFACTS_UNAVAILABLE$'):
                rf.verify_artifacts(folder)

    def test_dependency_mismatch_stops_loading(self):
        with patch.object(importlib.metadata, 'version', return_value='0.0.0'):
            with self.assertRaisesRegex(rf.RetrainedModelError, 'DEPENDENCY_VERSION_MISMATCH'):
                rf.verify_artifacts(ARTIFACTS)

    def test_feature_order_dimensions_and_dtype(self):
        a = self.assets
        pr, po = 'SYNTHETIC computer minimum 16 GB RAM', 'SYNTHETIC computer 8 GB RAM'
        x = rf.build_features(a, pr, po)[0]
        p, q = (a.features.sentence_vector(a.fasttext, text) for text in (pr, po))
        expected = np.concatenate([p, q, q-p, abs(p-q), p*q,
                                   a.features.numeric_relation_features(pr, po)]).astype(np.float32)
        np.testing.assert_array_equal(x, expected)
        self.assertEqual((x.size, x.dtype, a.fasttext.vector_size), (559, np.float32, 100))

    def test_tokenizer_matches_training_with_decimal_and_punctuation(self):
        self.assertEqual(self.assets.features.tokenize("SSD 2.5-TB; RAM 16GB, Wi-Fi / user's!"),
                         ['ssd', '2.5', 'tb', 'ram', '16', 'gb', 'wi-fi', "user's"])

    def test_saved_probe_feature_and_prediction_parity_all_48_heads(self):
        import joblib
        probes = json.loads((ARTIFACTS / 'reload_probes.json').read_text())
        matrix = np.vstack([rf.build_features(self.assets, *pair) for pair in probes['text_pairs']])
        np.testing.assert_array_equal(matrix, np.load(ARTIFACTS/'reload_probe_features.npy', allow_pickle=False))
        # All files were verified before any deserialization. Comparison heads
        # stay local to this test and never enter the application's selection.
        for path, expected in probes['predictions'].items():
            with self.subTest(head=path):
                head = joblib.load(ARTIFACTS / path)
                self.assertEqual(head.n_features_in_, 559)
                self.assertEqual(head.predict(matrix).tolist(), expected)
        self.assertEqual(len(probes['predictions']), 48)

    def test_string_classes_not_integer_mappings(self):
        self.assertEqual(set(self.assets.status.classes_), set(rf.STATUS_LABELS))
        self.assertEqual(set(self.assets.issue.classes_), set(rf.ISSUE_LABELS))
        self.assertEqual(self.assets.status.min_samples_leaf, 2)
        self.assertEqual(self.assets.status.n_estimators, 200)
        self.assertFalse(hasattr(self.assets.status, 'steps'))

    def test_invalid_empty_and_oversized_text_stops(self):
        for text in (None, [], {}, 17, '', '  ', '!!!', 'x' * (rf.MAX_TEXT + 1)):
            with self.subTest(kind=type(text).__name__):
                with self.assertRaises(rf.RetrainedModelError):
                    rf.predict_pair(ARTIFACTS, text, 'SYNTHETIC paper')

    def test_nonfinite_features_stops_inference(self):
        with patch.object(self.assets.features, 'build_pair_features', return_value=np.full(559, np.nan, dtype=np.float32)):
            with self.assertRaisesRegex(rf.RetrainedModelError, 'INVALID_FEATURES'):
                rf.predict_pair(ARTIFACTS, 'SYNTHETIC paper', 'SYNTHETIC paper')

    def test_inference_exception_never_returns_acceptable_or_payload(self):
        with patch.object(self.assets.status, 'predict', side_effect=RuntimeError('SENSITIVE-SYNTHETIC-TEXT')):
            with self.assertRaisesRegex(rf.RetrainedModelError, '^RETRAINED_INFERENCE_FAILED$'):
                rf.predict_pair(ARTIFACTS, 'SYNTHETIC paper', 'SYNTHETIC paper')

    def test_probability_is_actual_uncalibrated_rf_output(self):
        pair = ('SYNTHETIC computer; minimum 16 GB RAM', 'SYNTHETIC computer; 8 GB RAM')
        result = rf.predict_pair(ARTIFACTS, *pair)
        matrix = rf.build_features(self.assets, *pair)
        raw = dict(zip(self.assets.status.classes_, self.assets.status.predict_proba(matrix)[0] * 100))
        self.assertEqual(result['status_probabilities'], raw)
        self.assertEqual(result['status_confidence'], raw[result['status']])
        self.assertFalse(result['diagnostics']['probability_calibrated'])
        self.assertFalse(result['diagnostics']['automatic_approval'])

    def test_confusion_matrices_reproduce_false_acceptable_counts(self):
        evidence = {}
        for family, setting, count in [('Logistic_Regression', 0, 9), ('Linear_SVM', 3, 8), ('Random_Forest', 7, 15)]:
            data = json.loads((ARTIFACTS/f'models/{family}/setting_{setting}/seed_42/evaluation.json').read_text())
            false, denominator = 0, 0
            for panel in ('dev_unseen_name', 'dev_unseen_family'):
                metrics = data['metrics'][panel]
                matrix = np.array(metrics['status']['confusion_matrix'])
                self.assertEqual(metrics['status']['label_order'], rf.STATUS_LABELS)
                self.assertEqual(int(matrix[1:, 0].sum()), metrics['false_acceptable_count'])
                false += int(matrix[1:, 0].sum()); denominator += int(matrix[1:, :].sum())
            self.assertEqual((false, denominator), (count, 296))
            evidence[family] = dict(false_acceptable=false, reference_non_acceptable=denominator)
        EVIDENCE['false_acceptable_against_ai_proposals'] = evidence

    def test_same_synthetic_pairs_legacy_and_retrained_separately(self):
        from fasttext_nlp_runtime import predict_pair
        cases = [
            ('quantity', 'SYNTHETIC paper; quantity 10 reams', 'SYNTHETIC paper; quantity 8 reams'),
            ('unit', 'SYNTHETIC paper; quantity 10 reams', 'SYNTHETIC paper; quantity 10 boxes'),
            ('minimum', 'SYNTHETIC computer; minimum 16 GB RAM', 'SYNTHETIC computer; 8 GB RAM'),
            ('material', 'SYNTHETIC chair; material stainless steel', 'SYNTHETIC chair; material plastic'),
            ('different_item', 'SYNTHETIC printer', 'SYNTHETIC desk'),
        ]
        results = []
        for name, pr, po in cases:
            row = dict(case=name, scope='synthetic probes; no independent human-reviewed targets')
            for label, function, folder in [('legacy', predict_pair, ROOT/'model/fasttext_nlp'),
                                            ('retrained_rf', rf.predict_pair, ARTIFACTS)]:
                result = function(str(folder), pr, po)
                row[label] = {k: result[k] for k in ('status', 'issue', 'feature_dimension')}
            results.append(row)
        EVIDENCE['synthetic_comparison'] = results


class SelectionTests(unittest.TestCase):
    def test_default_is_legacy(self):
        with patch.dict(os.environ, {}, clear=True):
            app = SimpleNamespace(config={'PSMS_ENV': 'test'})
            selection.configure(app, ROOT)
            self.assertEqual(app.config['PSMS_NLP_RUNTIME'], 'legacy')
            self.assertEqual(selection.runtime(app.config).__name__, 'fasttext_nlp_runtime')

    def test_unknown_selection_rejected(self):
        with patch.dict(os.environ, PSMS_NLP_RUNTIME='unknown'):
            with self.assertRaises(RuntimeError):
                selection.configure(SimpleNamespace(config={'PSMS_ENV': 'test'}), ROOT)

    def test_experimental_activation_requires_test_mode(self):
        for mode in ('local', 'production'):
            with self.subTest(mode=mode), patch.dict(os.environ, PSMS_NLP_RUNTIME='retrained_rf'):
                with self.assertRaises(RuntimeError):
                    selection.configure(SimpleNamespace(config={'PSMS_ENV': mode}), ROOT)
                with self.assertRaises(RuntimeError):
                    selection.runtime({'PSMS_ENV': mode, 'PSMS_NLP_RUNTIME': 'retrained_rf'})

    def test_experimental_selection_never_uses_default_database_or_data(self):
        with patch.dict(os.environ, {'PSMS_NLP_RUNTIME': 'retrained_rf'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'explicit synthetic database'):
                selection.configure(SimpleNamespace(config={'PSMS_ENV': 'test'}), ROOT)


class RetrainedRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection_patch = patch.dict(os.environ, PSMS_NLP_RUNTIME='retrained_rf')
        cls.selection_patch.start()
        cls.runtime = ActualApplication().__enter__()
        cls.module = cls.runtime.module

    @classmethod
    def tearDownClass(cls):
        cls.runtime.__exit__(None, None, None)
        cls.selection_patch.stop()

    def setUp(self):
        self.db = SyntheticDatabase()
        self.runtime.bind(self.db)
        self.client = self.runtime.client(self.db)
        self.module.app.config['PSMS_NLP_RUNTIME'] = 'retrained_rf'
        self.module.app.config['PR_PO_ITEM_COMPARISON_ENABLED'] = True

    def csrf(self):
        import re
        html = self.client.get('/nlp').get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html)[1]

    def post(self, path='/nlp', **values):
        return self.client.post(path, data=dict(csrf_token=self.csrf(), **values))

    def test_actual_post_records_rf_evidence_and_audit_for_review(self):
        with patch.object(self.module, 'next_document_no', return_value='SYNTHETIC-NLP-21'):
            response = self.post(po_id='10')
        self.assertEqual(response.status_code, 302)
        record = self.db.nlps[21]
        self.assertEqual(record['status'], 'For Review')
        detail = json.loads(record['details'])
        self.assertEqual((detail['runtime'], detail['feature_dimension']), ('retrained_rf', 559))
        self.assertTrue(detail['deterministic_comparison']['rows'])
        self.assertFalse(detail['automatic_approval'])
        self.assertTrue(detail['human_confirmation_required'])
        self.assertIn('AI_PROPOSED_UNVERIFIED', record['remarks'])
        self.assertTrue(any(sql.startswith('insert into audit_trail') for sql, _ in self.db.writes))
        self.runtime.firewall.assert_not_called()

    def test_future_procurement_descriptions_use_real_rf_and_persist_for_human_review(self):
        items=[{'Item Name':'SYNTHETIC solar-powered water-level telemetry module',
                'Technical Specification':'IP68 enclosure; minimum 30 day battery reserve; LoRaWAN',
                'Category':'Climate adaptation instrumentation','Quantity':2,'Unit':'units'}]
        self.db.prs[10]['items']=json.dumps(items)
        self.db.pos[10]['items']=json.dumps(items)
        self.test_actual_post_records_rf_evidence_and_audit_for_review()
        details=json.loads(self.db.nlps[21]['details'])
        self.assertEqual(details['model'],'FastText + Random Forest')
        self.assertIn('water-level telemetry module',details['pr_text'])
        self.assertIn('LoRaWAN',details['po_text'])
        self.assertIn('Climate adaptation instrumentation',details['pr_text'])
        self.assertIn(self.db.nlps[21]['result'],rf.STATUS_LABELS)
        self.assertTrue(0<=self.db.nlps[21]['confidence']<=100)

    def test_review_presents_probabilities_warnings_and_confirmation(self):
        self.test_actual_post_records_rf_evidence_and_audit_for_review()
        html = self.client.get('/nlp/21/view').get_data(as_text=True)
        for text in ('AI_PROPOSED_UNVERIFIED', 'uncalibrated', 'review_confirm',
                     'deterministic_comparison', 'numeric_features', 'id="prPoItemComparison"'):
            self.assertIn(text, html)
        self.assertIn('Experimental Random Forest', self.client.get('/nlp').get_data(as_text=True))

    def test_missing_artifact_stops_without_insert_or_legacy_fallback(self):
        with patch.dict(self.module.app.config, PSMS_RETRAINED_NLP_DIR=str(ROOT/'output/nonexistent-retrained-folder')):
            with patch('fasttext_nlp_runtime.predict_pair', side_effect=AssertionError('Fallback forbidden')):
                self.assertEqual(self.post(po_id='10').status_code, 302)
        self.assertNotIn(21, self.db.nlps)
        self.assertFalse(any(sql.startswith('insert into nlp') for sql, _ in self.db.writes))
        self.assertTrue(any(sql.startswith('insert into audit_trail') for sql, _ in self.db.writes))

    def test_empty_items_stop_before_model(self):
        self.db.pos[10]['items'] = '[]'
        with patch.object(self.module, 'run_trained_nlp_prediction') as inference:
            self.assertEqual(self.post(po_id='10').status_code, 302)
            inference.assert_not_called()
        self.assertNotIn(21, self.db.nlps)

    def test_deterministic_quantity_unit_numeric_and_material_differences(self):
        cases = [('quantity', dict(qty=9)), ('unit', dict(unit='box')),
                 ('numeric', dict(description='RAM 8 GB')), ('material', dict(description='material plastic'))]
        results = []
        for label, difference in cases:
            pr = dict(item='SYNTHETIC Device', description='RAM minimum 16 GB; material stainless steel', qty=10, unit='pieces')
            po = dict(pr, **difference)
            _, _, details = self.module.nlp_predict([pr], [po])
            checks = details['deterministic_comparison']['rows'][0]['checks']
            attribute = {'quantity': 'quantity', 'unit': 'quantity', 'numeric': 'memory', 'material': 'material'}[label]
            states = ('UNKNOWN',) if label == 'unit' else ('WORDING_DIFFERENCE',) if label == 'material' else ('VIOLATED',)
            self.assertTrue(any(c['attribute'] == attribute and c['state'] in states
                                for c in checks), (label, checks))
            results.append(dict(case=label, status=details['trained_status_label'],
                                checks=checks, automatic_approval=details['automatic_approval']))
        EVIDENCE['synthetic_deterministic_safety'] = results

    def test_deterministic_evidence_survives_optional_panel_switch(self):
        self.module.app.config['PR_PO_ITEM_COMPARISON_ENABLED'] = False
        pr = [dict(item='SYNTHETIC Paper', qty=10, unit='ream')]
        po = [dict(item='SYNTHETIC Paper', qty=8, unit='ream')]
        result = self.module.nlp_predict(pr, po)
        self.assertTrue(result[2]['deterministic_comparison']['rows'])

    def test_false_acceptable_stub_cannot_hide_differences_or_approve(self):
        actual = rf.predict_pair(ARTIFACTS, 'SYNTHETIC computer minimum 16 GB RAM', 'SYNTHETIC computer 8 GB RAM')
        actual.update(status='Acceptable', status_confidence=12.0)
        with patch.object(self.module, 'run_trained_nlp_prediction', return_value=actual), patch.object(
                self.module, 'next_document_no', return_value='SYNTHETIC-NLP-21'):
            self.post(po_id='10')
        record = self.db.nlps[21]
        self.assertEqual((record['status'], record['result']), ('For Review', 'Acceptable'))
        self.assertTrue(json.loads(record['details'])['deterministic_comparison']['rows'])
        self.assertEqual(self.post('/nlp/21/approve').status_code, 302)
        self.assertEqual(record['status'], 'For Review')

    def test_csrf_and_human_confirmation_and_repeated_approval(self):
        self.assertEqual(self.client.post('/nlp/20/approve', data={'review_confirm': 'yes'}).status_code, 400)
        self.post('/nlp/20/approve')
        self.assertEqual(self.db.nlps[20]['status'], 'For Review')
        self.post('/nlp/20/approve', review_confirm='yes')
        self.assertEqual(self.db.nlps[20]['status'], 'Approved')
        self.assertEqual(self.db.nlps[20]['staff_decision'], 'Needs Recanvass')
        writes = len(self.db.writes)
        self.post('/nlp/20/approve', review_confirm='yes')
        self.assertEqual(len(self.db.writes), writes)

    def test_ordinary_post_cannot_select_runtime(self):
        with patch.object(self.module, 'next_document_no', return_value='SYNTHETIC-NLP-21'):
            self.post(po_id='10', PSMS_NLP_RUNTIME='legacy')
        self.assertEqual(json.loads(self.db.nlps[21]['details'])['runtime'], 'retrained_rf')

    def test_revoked_session_and_anonymous_cannot_verify(self):
        self.assertEqual(self.module.app.test_client().post('/nlp', data={'po_id': '10'}).status_code, 302)
        token = self.csrf()
        self.db.revoked.add(2)
        self.assertEqual(self.client.post('/nlp', data={'csrf_token': token, 'po_id': '10'}).status_code, 302)
        self.assertFalse(self.db.writes)

    def test_legacy_rollback_uses_unchanged_original_predictions(self):
        from fasttext_nlp_runtime import predict_pair
        pr = [dict(item='SYNTHETIC paper', qty=10, unit='ream')]
        with patch.dict(self.module.app.config, PSMS_NLP_RUNTIME='legacy'):
            result = self.module.nlp_predict(pr, pr)
            expected = predict_pair(self.module.NLP_FASTTEXT_DIR,
                self.module.build_pr_text_for_trained_nlp(pr), self.module.build_po_text_for_trained_nlp(pr))
        self.assertEqual(result[:2], (expected['status'], expected['status_confidence']))
        self.assertEqual(result[2]['trained_primary_issue'], expected['issue'])
        self.assertNotIn('deterministic_comparison', result[2])
