"""Fail closed on absent/unapproved model releases before the Vercel asset build."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import inference_releases as releases
from deploy.inference_release import retrieve, synthetic, summary
from deploy.build_vercel_assets import build_assets


def build(base=ROOT):
    identifier = releases.configured_identifier(base=base, deployment=True)
    releases.require(bool(identifier), 'RELEASE_SELECTION_REQUIRED')
    entry = releases.entry_for(identifier, base)
    profiles = (os.environ.get('PSMS_NLP_RUNTIME', 'legacy').strip(), 'semantic_ocr')
    releases.check_approval({'approval': entry['approval']}, entry, profiles)
    if not (Path(base) / entry['directory']).exists():
        retrieve(identifier, base=base)
    evidence = releases.verify(identifier, base=base, deployment=True, profiles=profiles)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    report = summary(evidence)
    report['synthetic'] = synthetic(identifier, base=base)
    report['assets'] = build_assets(base)
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(build(), sort_keys=True))
    except releases.ReleaseError as error:
        print(str(error))
        raise SystemExit(1) from None
    except Exception:
        print('RELEASE_BUILD_FAILED')
        raise SystemExit(1) from None
