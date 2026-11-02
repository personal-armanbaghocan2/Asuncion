"""Owner-only transport verification, independent of production activation.

Default: local bytes only. --remote explicitly downloads private parts with
object-scoped tokens, hashes/extracts bytes without executing any model code.
Never prints URLs, tokens, upstream response bodies or exceptions.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inference_releases as releases
import runtime_model_cache as cache

DEFAULT_DESCRIPTOR=ROOT/'deploy/proposals/private_model_delivery_nlp_online_original_v1.PROPOSED.json'


def inspect_descriptor(descriptor,*,local_parts=None,remote=False,credentials_only=False,base=ROOT):
    data=releases.read_json(descriptor)
    releases.require(isinstance(data,dict) and len(data)==1,'RELEASE_METADATA_INVALID')
    identifier=releases.release_id(next(iter(data)))
    entry=data[identifier]
    output=Path(base)/'output';output.mkdir(exist_ok=True)
    # Temporary trust copy is for this NON-ACTIVATING byte verifier only. It
    # never enters the application registry/cache or bypasses production gates.
    with tempfile.TemporaryDirectory(prefix='.private-part-check-',dir=output) as temporary:
        temporary=Path(temporary)
        (temporary/releases.REGISTRY).write_bytes(releases.canonical(dict(schema=releases.SCHEMA,
            deployment_release=None,releases={identifier:entry})))
        shutil.copyfile(Path(base)/releases.SOURCE_CONTRACT,temporary/releases.SOURCE_CONTRACT)
        evidence=releases.metadata(identifier,base=temporary,deployment=False,dependencies=False)
        settings=cache.delivery_settings(identifier,evidence,temporary)
        cache.validate_signed_parts(settings,identifier)
        report=dict(release_id=identifier,parts=len(settings['parts']),
            archive_bytes=settings['archive_bytes'],archive_sha256=settings['archive_sha256'],
            local_bytes_verified=False,remote_bytes_verified=False,models_executed=False,
            release_activated=False,approval_status=entry['approval']['status'])
        if credentials_only:
            _,expiry=cache._signed_tokens(settings)
            report['earliest_token_expiry_utc']=datetime.fromtimestamp(expiry,timezone.utc).isoformat()
            report['token_claims_checked_not_signatures']=True
            return report
        if remote:
            archive=temporary/'models.zip'
            cache._download(settings,archive)  # each part + whole archive pins
            staged=temporary/'verified';staged.mkdir()
            cache._extract(archive,staged,evidence)
            verified=releases.verify(identifier,base=temporary,directory=staged,
                deployment=False,dependencies=False)
            report.update(remote_bytes_verified=True,original_artifact_files_verified=verified['files_verified'])
        else:
            releases.require(local_parts is not None,'DELIVERY_LOCAL_PARTS_REQUIRED')
            digest=hashlib.sha256();size=0
            for part in settings['parts']:
                path=releases.safe_path(local_parts,part['object_name'])
                releases.require(path.stat().st_size==part['bytes'] and
                    releases.sha256(path)==part['sha256'],'RELEASE_ARCHIVE_CHECKSUM')
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):
                        digest.update(block);size+=len(block)
            releases.require(size==settings['archive_bytes'] and digest.hexdigest()==settings['archive_sha256'],
                             'RELEASE_ARCHIVE_CHECKSUM')
            report['local_bytes_verified']=True
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--descriptor',type=Path,default=DEFAULT_DESCRIPTOR)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--remote',action='store_true')
    mode.add_argument('--credentials-only',action='store_true')
    parser.add_argument('--local-parts',type=Path,default=ROOT/'output/hobby_delivery/model-proposal')
    args=parser.parse_args()
    try:
        report=inspect_descriptor(args.descriptor,local_parts=args.local_parts,
            remote=args.remote,credentials_only=args.credentials_only)
        print(json.dumps(report,sort_keys=True));return 0
    except releases.ReleaseError as error:
        print(str(error));return 1
    except Exception:
        print('PRIVATE_MODEL_PART_VERIFICATION_FAILED');return 1


if __name__=='__main__':
    raise SystemExit(main())
