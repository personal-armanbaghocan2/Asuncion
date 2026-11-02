"""Verified, frozen 559-feature Colab inference. Never trains or approves records.

Trust is anchored to the user-supplied export reviewed for this integration, not
to a manifest supplied by an arbitrary directory. Checksums establish identity,
not a publisher signature. Only the two pinned RF heads are deserialized.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import platform
from threading import RLock
from typing import Any
import warnings
from inference_releases import configured_identifier

MANIFEST_SHA256 = '76937d298701ba832ec004ab694c69ef8b60687b9f4d8fe2d335f4a2dc75c61b'
FEATURE_SHA256 = '41be9ed2b4149f917d1b4becbd1a983f9bbd60c7caa133625056f5eaaf9746e5'
SCHEMA = 'FASTTEXT_NUMERIC_RELATION_V2'
HEAD_ROOT = 'models/Random_Forest/setting_7/seed_42'
STATUS_LABELS = ['Acceptable', 'Needs Review', 'Needs Recanvass', 'Reject']
ISSUE_LABELS = ['Requirement satisfied', 'Missing or ambiguous information',
                'Below minimum', 'Above maximum', 'Exact or range mismatch',
                'Quantity or unit mismatch', 'Specification mismatch',
                'Different item', 'Missing item']
PACKAGES = ('numpy', 'scipy', 'gensim', 'scikit-learn', 'joblib', 'threadpoolctl')
BLOCKS = ['PR mean[100]', 'PO mean[100]', 'PO-PR[100]', 'abs(PO-PR)[100]',
          'PR*PO[100]', 'numeric[59]']
MAX_TEXT = 100_000
_LOCK = RLock()
_CACHE: dict[str, Any] = {}


class RetrainedModelError(RuntimeError):
    """Safe diagnostic code without source text, paths or serialized exceptions."""


def _require(condition, code):
    if not condition:
        raise RetrainedModelError(code)


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _json(root, name):
    return json.loads((root / name).read_text(encoding='utf-8'))


def verify_artifacts(model_root):
    """Verify every declared file before executing feature code or loading pickle."""
    try:
        root = Path(model_root).resolve(strict=True)
        from inference_releases import profile_evidence
        release = profile_evidence(root, 'retrained_rf')
        if release is not None:
            # The application-pinned serving manifest verifies all release files;
            # historical classifiers and research charts are not serving inputs.
            manifest = {'files': {name[3:]: record['sha256'] for name, record in
                        release['manifest']['artifacts'].items() if name.startswith('rf/')}}
        else:
            _require(_sha(root / 'artifact_manifest.json') == MANIFEST_SHA256,
                 'RETRAINED_MANIFEST_NOT_PINNED')
            manifest = _json(root, 'artifact_manifest.json')
            _require(manifest['schema'] == 'SHARED_FASTTEXT_THREE_CLASSIFIERS_EXPERIMENT_V1'
                 and manifest['smoke_only'] is False and manifest['fasttext_models'] == 1,
                 'RETRAINED_ARTIFACT_VERSION')
        for name, digest in manifest['files'].items():
            relative = PurePosixPath(name)
            _require(not relative.is_absolute() and '..' not in relative.parts,
                     'RETRAINED_UNSAFE_ARTIFACT_PATH')
            path = (root / name).resolve(strict=True)
            _require(path.is_relative_to(root) and _sha(path) == digest,
                     'RETRAINED_CHECKSUM_MISMATCH')
        heads = [p for p in manifest['files'] if p.endswith('.joblib')]
        _require(len(heads) == (2 if release else 48), 'RETRAINED_HEAD_COUNT')
        contract = _json(root, 'feature_contract.json')
        _require(contract['schema'] == SCHEMA and contract['embedding_size'] == 100
                 and contract['total_features'] == 559 and contract['dtype'] == 'float32'
                 and contract['ordered_blocks'] == BLOCKS
                 and len(contract['numeric_feature_names']) == 59
                 and contract['feature_source_sha256'] == FEATURE_SHA256,
                 'RETRAINED_FEATURE_CONTRACT')
        labels = _json(root, 'labels.json')
        _require(labels['status_labels'] == STATUS_LABELS and labels['issue_labels'] == ISSUE_LABELS
                 and labels['status2id'] == {v: i for i, v in enumerate(STATUS_LABELS)}
                 and labels['issue2id'] == {v: i for i, v in enumerate(ISSUE_LABELS)},
                 'RETRAINED_LABEL_CONTRACT')
        config = _json(root, 'configuration.json')
        selection = _json(root, 'comparison.json')['selections']['Random Forest']
        _require(config['status_uses_issue_probabilities'] is False
                 and config['target_policy'] == 'AI_PROPOSED_UNVERIFIED'
                 and selection['index'] == 7 and selection['seed'] == 42
                 and selection['setting'] == {'min_samples_leaf': 2},
                 'RETRAINED_SELECTION_CONTRACT')
        environment = _json(root, 'environment.json')
        _require(all(importlib.metadata.version(p) == environment['packages'][p] for p in PACKAGES),
                 'RETRAINED_DEPENDENCY_VERSION_MISMATCH')
        return dict(root=root, files_verified=len(manifest['files']), heads_verified=len(heads),
                    contract=contract, training_environment=environment,
                    python_version=platform.python_version(),
                    feature_path=(release['root'] / 'legacy/fasttext_feature_schema_v2.py' if release else None),
                    manifest_sha256=release['manifest_sha256'] if release else MANIFEST_SHA256)
    except RetrainedModelError:
        raise
    except Exception:
        raise RetrainedModelError('RETRAINED_ARTIFACTS_UNAVAILABLE') from None


@dataclass(frozen=True)
class Assets:
    fasttext: Any
    status: Any
    issue: Any
    features: Any
    verification: dict


def clear_cache():
    with _LOCK:
        _CACHE.clear()


def validate_text(pr_text, po_text):
    for value in (pr_text, po_text):
        _require(type(value) is str and 0 < len(value) <= MAX_TEXT and value.strip(),
                 'RETRAINED_INVALID_DESCRIPTION')


def build_features(assets, pr_text, po_text):
    import numpy as np
    validate_text(pr_text, po_text)
    _require(assets.features.tokenize(pr_text) and assets.features.tokenize(po_text),
             'RETRAINED_EMPTY_TOKENS')
    matrix = assets.features.build_pair_features(assets.fasttext, pr_text, po_text)
    _require(matrix.shape == (559,) and matrix.dtype == np.float32 and np.isfinite(matrix).all(),
             'RETRAINED_INVALID_FEATURES')
    return matrix.reshape(1, 559)


def load_assets(model_root):
    """Load once per process after integrity, dependency, shape and reload checks."""
    from inference_releases import require_inference
    require_inference()
    with _LOCK:
        key = str(Path(model_root).resolve())
        if key in _CACHE:
            _require(_CACHE[key].verification.get('selected_release_id') ==
                     configured_identifier(),
                     'RETRAINED_RELEASE_SELECTION_CHANGED')
            return _CACHE[key]
        evidence = verify_artifacts(model_root)
        evidence['selected_release_id'] = configured_identifier()
        root = evidence['root']
        try:
            import numpy as np
            import joblib
            from gensim.models import FastText
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.exceptions import InconsistentVersionWarning
            # Execute only the existing reviewed feature utility. The exported
            # training scripts and source modules are never imported.
            feature_path = evidence.get('feature_path') or Path(__file__).parent / 'model/fasttext_nlp/fasttext_feature_schema_v2.py'
            _require(_sha(feature_path) == FEATURE_SHA256, 'RETRAINED_FEATURE_SOURCE_CHANGED')
            from inference_releases import execute_feature
            features = execute_feature(feature_path, 'retrained_frozen_features')
            _require(features.FEATURE_SCHEMA == SCHEMA
                     and features.NUMERIC_FEATURE_NAMES == evidence['contract']['numeric_feature_names']
                     and features.expected_feature_dimension(100) == 559,
                     'RETRAINED_FEATURE_ORDER')
            with warnings.catch_warnings():
                warnings.simplefilter('error', InconsistentVersionWarning)
                fasttext = FastText.load(str(root / 'fasttext_shared.model'))
                status = joblib.load(root / HEAD_ROOT / 'status.joblib')
                issue = joblib.load(root / HEAD_ROOT / 'issue.joblib')
            _require(fasttext.vector_size == 100, 'RETRAINED_EMBEDDING_DIMENSION')
            for head, labels in ((status, STATUS_LABELS), (issue, ISSUE_LABELS)):
                _require(type(head) is RandomForestClassifier and head.n_features_in_ == 559
                         and set(head.classes_) == set(labels), 'RETRAINED_HEAD_CONTRACT')
                _require(head.get_params() == _json(root, HEAD_ROOT +
                         ('/status_parameters.json' if head is status else '/issue_parameters.json')),
                         'RETRAINED_HEAD_PARAMETERS')
            assets = Assets(fasttext, status, issue, features, evidence)
            probes = _json(root, 'reload_probes.json')
            actual = np.vstack([build_features(assets, *pair) for pair in probes['text_pairs']])
            expected = np.load(root / 'reload_probe_features.npy', allow_pickle=False)
            _require(actual.dtype == expected.dtype and np.array_equal(actual, expected),
                     'RETRAINED_RELOAD_FEATURE_PARITY')
            for name, head in (('status', status), ('issue', issue)):
                _require(head.predict(actual).tolist() == probes['predictions'][HEAD_ROOT + '/' + name + '.joblib'],
                         'RETRAINED_RELOAD_PREDICTION_PARITY')
            _CACHE[key] = assets
            return assets
        except RetrainedModelError:
            raise
        except Exception:
            raise RetrainedModelError('RETRAINED_MODEL_LOAD_FAILED') from None


def _predict(head, matrix, labels):
    import numpy as np
    label = str(head.predict(matrix)[0])
    raw = np.asarray(head.predict_proba(matrix)[0])
    _require(label in labels and raw.shape == (len(labels),) and np.isfinite(raw).all()
             and (raw >= 0).all() and (raw <= 1).all() and np.isclose(raw.sum(), 1),
             'RETRAINED_INVALID_PREDICTION')
    probabilities = {str(cls): float(value) * 100 for cls, value in zip(head.classes_, raw)}
    return label, probabilities[label], probabilities


def predict_pair(model_root, pr_text, po_text):
    validate_text(pr_text, po_text)
    assets = load_assets(model_root)
    try:
        matrix = build_features(assets, pr_text, po_text)
        status, status_probability, status_probabilities = _predict(assets.status, matrix, STATUS_LABELS)
        issue, issue_probability, issue_probabilities = _predict(assets.issue, matrix, ISSUE_LABELS)
        training_python = assets.verification['training_environment']['python']
        notes = ['Experimental AI_PROPOSED_UNVERIFIED targets; independent human validation is absent.',
                 'RF probabilities are uncalibrated model outputs, not compliance confidence.',
                 'Independent heads can disagree. Review deterministic findings and both original documents.']
        if (status == 'Acceptable') != (issue == 'Requirement satisfied'):
            notes.append('Status and issue heads disagree about requirement satisfaction; human review is required.')
        if platform.python_version() != training_python:
            notes.append('Python differs from training; saved synthetic feature/prediction probes pass. Production portability is unverified.')
        return dict(status=status, status_confidence=status_probability, status_probabilities=status_probabilities,
                    issue=issue, issue_confidence=issue_probability, issue_probabilities=issue_probabilities,
                    feature_dimension=559, status_feature_dimension=559, feature_schema=SCHEMA,
                    pr_token_count=len(assets.features.tokenize(pr_text)),
                    po_token_count=len(assets.features.tokenize(po_text)),
                    model_manifest=dict(deployed_model='FastText + Random Forest',
                        architecture='100-dimensional shared FastText; independent unscaled 559-feature RF heads',
                        runtime='retrained_rf', artifact_manifest_sha256=assets.verification['manifest_sha256'],
                        setting=7, classifier_seed=42, target_policy='AI_PROPOSED_UNVERIFIED',
                        production_promotion_allowed=False),
                    diagnostics=dict(warnings=notes, automatic_approval=False, probability_calibrated=False,
                        numeric_features=dict(zip(assets.features.NUMERIC_FEATURE_NAMES,
                                                   matrix[0, 500:].astype(float).tolist()))))
    except RetrainedModelError:
        raise
    except Exception:
        raise RetrainedModelError('RETRAINED_INFERENCE_FAILED') from None


def model_status(model_root):
    try:
        load_assets(model_root)
        return dict(active=True, message='Experimental retrained RF ready for synthetic human-reviewed testing; no automatic approval.',
                    manifest=dict(deployed_model='FastText + Random Forest', runtime='retrained_rf'))
    except RetrainedModelError as error:
        return dict(active=False, message=str(error))
