"""Prepare private model proposals and an exact GitHub/Vercel delivery context.

No SQL, Git mutation, upload, approval, retraining or deployment. Inference-enabled
contexts require an ALREADY approved, Linux-verified release. The initial website
context deliberately disables inference and contains an empty release registry.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inference_releases as releases
from deploy.build_vercel_assets import ASSETS
from deploy.inference_release import archive_bytes
from deploy.source_release import safe_name
from deploy.validate_linux_runtime import pins
from scripts.audits.online_backend_manifest import runtime_sources

SOURCE_UPLOAD_LIMIT = 90_000_000  # decimal MB; headroom below Hobby's 100 MB CLI limit

DEPLOY_FILES = ('Dockerfile.vercel','vercel.json','.gitignore','.dockerignore','.vercelignore',
    '.github/workflows/online-production.yml','deploy/online_delivery.py',
    'deploy/verify_online_image.py','deploy/validate_linux_runtime.py','deploy/source_release.py',
    'scripts/audits/online_backend_manifest.py','inference_release_sources.json',
    'tests/test_online_delivery.py','tests/run_online_delivery_checks.py','tests/test_vercel_preparation.py',
    'tests/test_hobby_model_delivery.py','tests/run_hobby_delivery_checks.py',
    'tests/test_supabase_model_delivery.py','tests/run_private_model_delivery_checks.py','deploy/verify_model_parts.py',
    'deploy/Set-PrivateModelTokens.ps1','docs/guides/PRIVATE_MODEL_DELIVERY.md',
    'deploy/proposals/private_model_delivery_nlp_online_original_v1.PROPOSED.json',
    '.github/workflows/model-release-verification.yml','deploy/model_release_gate.Dockerfile',
    'deploy/model_release_gate.py','deploy/finalize_model_release.py',
    'deploy/proposals/model_release_owner_decision.PROPOSED.json',
    'tests/test_model_release_approval_preparation.py','tests/run_model_release_approval_checks.py',
    'docs/reports/MODEL_RELEASE_OWNER_REVIEW.md',
    'docs/guides/ONLINE_CONTAINER_DEPLOYMENT.md')

INITIAL_DEPLOY_FILES=('Dockerfile.vercel','vercel.json','.gitignore','.dockerignore','.vercelignore',
    '.github/workflows/online-production.yml','deploy/online_delivery.py','deploy/verify_online_image.py',
    'deploy/source_release.py','scripts/audits/online_backend_manifest.py',
    'docs/guides/FIRST_PRODUCTION_DEPLOYMENT.md','tests/test_vercel_preparation.py',
    'tests/run_online_delivery_checks.py','tests/run_online_readiness.py',
    'tests/test_database_configuration_isolation.py','tests/test_f03_persistent_workbooks.py',
    'tests/test_ppmp_edit_protection.py','tests/test_online_delivery.py','tests/test_inference_releases.py',
    'tests/test_nlp_comparison_routes.py','deploy/verify_model_parts.py')


def source_files(base=ROOT):
    base=Path(base)
    files=runtime_sources(base) | {'requirements.txt','deploy/retrained-nlp-constraints.txt',
        'deploy/requirements-staging-linux-amd64.lock.txt','deploy/verify_online_image.py',
        'deploy/validate_linux_runtime.py',
        'inference_release_registry.json',releases.SOURCE_CONTRACT,'runtime_model_cache.py'}
    files |= {p.relative_to(base).as_posix() for p in (base/'templates').glob('*.html')}
    files |= {'static/'+name for name in ASSETS}
    return files


def destination(path,base=ROOT):
    target=Path(path).resolve()
    releases.require(target.is_relative_to((Path(base)/'output').resolve())
                     and not target.exists(),'DELIVERY_NEW_PRIVATE_OUTPUT_REQUIRED')
    return target


def review(path,base=ROOT):
    base=Path(base); target=destination(path,base)
    files=source_files(base) | set(DEPLOY_FILES)
    tracked=set(subprocess.check_output(['git','ls-files','-z'],cwd=base).decode().split('\0'))
    target.mkdir(parents=True)
    inventory={}
    for name in sorted(files):
        safe_name(name)
        origin=releases.safe_path(base,name)
        copy=target/'github-source'/name
        copy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(origin,copy)
        inventory[name]=dict(sha256=releases.sha256(origin),bytes=origin.stat().st_size,
                             tracked=name in tracked)
    (target/'github-files.txt').write_text('\n'.join(sorted(files))+'\n')
    (target/'required-untracked.txt').write_text('\n'.join(sorted(files-tracked))+'\n')
    (target/'inventory.json').write_bytes(releases.canonical(dict(files=inventory,
        secrets_or_model_weights_included=False,published=False)))
    # A measurable source-only snapshot, visibly unapproved if the original
    # registry is unapproved. The image gate still refuses that snapshot.
    identifier=releases.read_json(base/releases.SOURCE_CONTRACT)['release_id']
    entry=deepcopy(releases.entry_for(identifier,base))
    if 'manifest' not in entry:
        evidence=releases.verify(identifier,base=base,dependencies=False)
        entry['manifest']=evidence['manifest']
    upload=_copy_context(target/'source-upload',identifier,entry,base)
    return dict(source_files=len(files),untracked=len(files-tracked),output=str(target),
                source_upload_bytes=upload['source_bytes'],published=False,deployable=False)


def initial(path,base=ROOT):
    """Reviewable first website delivery: no model approval, files or credentials needed."""
    base=Path(base);target=destination(path,base)
    files=source_files(base)|set(INITIAL_DEPLOY_FILES)
    tracked=set(subprocess.check_output(['git','ls-files','-z'],cwd=base).decode().split('\0'))
    target.mkdir(parents=True)
    inventory={}
    for name in sorted(files):
        safe_name(name);origin=releases.safe_path(base,name)
        copy=target/'github-source'/name;copy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(origin,copy)
        inventory[name]=dict(sha256=releases.sha256(origin),bytes=origin.stat().st_size,tracked=name in tracked)
    (target/'github-files.txt').write_text('\n'.join(sorted(files))+'\n')
    (target/'required-untracked.txt').write_text('\n'.join(sorted(files-tracked))+'\n')
    (target/'inventory.json').write_bytes(releases.canonical(dict(files=inventory,
        inference_mode='disabled',secrets_or_model_weights_included=False,published=False)))
    report=context(target/'vercel-source',None,base=base,inference_mode='disabled')
    return dict(report,github_files=len(files),required_untracked=len(files-tracked),output=str(target))


def models(path,base=ROOT):
    """Copy original weights into a Linux EXPECTATION proposal, never approve it."""
    base=Path(base); target=destination(path,base)
    contract=releases.read_json(base/releases.SOURCE_CONTRACT)
    original=releases.verify(contract['release_id'],base=base,dependencies=False)
    releases.require(set(contract['artifacts'])==set(releases.SOURCES),'RELEASE_SOURCE_CONTRACT')
    for name,record in contract['artifacts'].items():
        source=releases.safe_path(base,releases.SOURCES[name])
        releases.require(record['source']==releases.SOURCES[name]
            and releases.sha256(source)==record['sha256']
            and original['manifest']['artifacts'][name]['sha256']==record['sha256'],
            'RELEASE_SOURCE_CHANGED')
    identifier='nlp-online-original-v1'
    manifest=deepcopy(original['manifest'])
    manifest['release_id']=identifier
    linux_pins=pins()
    manifest['runtime'].update(locally_verified_python='3.13.16',linux_verified=False,
        validation_expectation_only=True,packages={p:linux_pins[p] for p in releases.PACKAGES})
    manifest['approval']=dict(status='local_validation_only',approved_profiles=[],
                              quality_review=None,deployment_review=None)
    target.mkdir(parents=True)
    root=target/'private-models'/identifier
    root.mkdir(parents=True)
    for name in releases.SOURCES:
        copy=root/name; copy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(releases.safe_path(original['root'],name),copy)
        releases.require(releases.sha256(copy)==manifest['artifacts'][name]['sha256'],
                         'RELEASE_SOURCE_CHANGED')
    (root/releases.MANIFEST).write_bytes(releases.canonical(manifest))
    delivery=_transport(archive_bytes(root),target,identifier)
    entry=dict(directory='inference_releases/'+identifier,model_version=manifest['model_version'],
        manifest_sha256=releases.sha256(root/releases.MANIFEST),approval=manifest['approval'],manifest=manifest,
        delivery=delivery)
    (target/'registry-entry.PROPOSED.json').write_bytes(releases.canonical({identifier:entry}))
    (target/'release.PROPOSED.json').write_bytes(releases.canonical(manifest))
    releases.verify(contract['release_id'],base=base,dependencies=False)
    return dict(release_id=identifier,artifact_files=28,artifact_bytes=original['artifact_bytes'],original_weights_unchanged=True,
        approved=False,linux_verified=False,output=str(target),published=False)


def _transport(payload,target,identifier):
    (target/(identifier+'.zip')).write_bytes(payload)
    parts=[]
    from runtime_model_cache import MAX_PART_BYTES
    for number,offset in enumerate(range(0,len(payload),MAX_PART_BYTES)):
        name=identifier+f'.{number:03d}.part'
        content=payload[offset:offset+MAX_PART_BYTES]
        (target/name).write_bytes(content)
        parts.append(dict(url='',object_name=name,bytes=len(content),sha256=hashlib.sha256(content).hexdigest()))
    return dict(parts=parts,archive_sha256=hashlib.sha256(payload).hexdigest(),archive_bytes=len(payload))


def supabase(path,identifier,directory,project_ref,base=ROOT):
    """Match existing local transport bytes and bind ROOT private object paths.

    No HTTP, credential handling, archive rebuild, approval or registry edits.
    """
    base=Path(base); target=destination(path,base)
    releases.require(bool(directory and project_ref),'DELIVERY_SUPABASE_ARGUMENTS')
    source=Path(directory)
    entry=deepcopy(releases.read_json(releases.safe_path(source,'registry-entry.PROPOSED.json'))[identifier])
    from runtime_model_cache import SIGNED_PARTS_AUTH, MODEL_BUCKET, validate_signed_parts
    delivery=entry['delivery']
    delivery['authentication']=dict(type=SIGNED_PARTS_AUTH,project_ref=project_ref,
        bucket=MODEL_BUCKET,release_id=identifier)
    combined=hashlib.sha256(); total=0
    for index,part in enumerate(delivery['parts']):
        name=identifier+f'.{index:03d}.part'
        releases.require(part['object_name']==name,'RELEASE_SIGNED_OBJECT_PATH')
        local=releases.safe_path(source,name)
        releases.require(local.stat().st_size==part['bytes']
            and releases.sha256(local)==part['sha256'],'RELEASE_ARCHIVE_CHECKSUM')
        with local.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                combined.update(block); total+=len(block)
        part['url']=('https://'+project_ref+'.supabase.co/storage/v1/object/sign/'+MODEL_BUCKET+'/'+name)
    releases.require(total==delivery['archive_bytes']
        and combined.hexdigest()==delivery['archive_sha256'],'RELEASE_ARCHIVE_CHECKSUM')
    validate_signed_parts(delivery,identifier)
    target.mkdir(parents=True)
    descriptor=target/'registry-entry.PROPOSED.json'
    descriptor.write_bytes(releases.canonical({identifier:entry}))
    from deploy.verify_model_parts import inspect_descriptor
    report=inspect_descriptor(descriptor,local_parts=source,base=base)
    (target/'parts-match.json').write_bytes(releases.canonical(report))
    return dict(report,output=str(target))


def package(path,identifier,directory=None,base=ROOT):
    """Repack an ALREADY owner-approved manifest and original bytes; no approval edits.

    Finalizing approval changes release.json, hence its ZIP/part hashes. This
    explicit local step produces new transport pins rather than reusing old ones.
    """
    base=Path(base); target=destination(path,base)
    releases.metadata(identifier,base=base,dependencies=False)
    evidence=releases.verify(identifier,base=base,directory=directory,deployment=True,
        dependencies=False,profiles=(__import__('os').environ.get('PSMS_NLP_RUNTIME','legacy'),'semantic_ocr'))
    target.mkdir(parents=True)
    entry=deepcopy(releases.entry_for(identifier,base))
    entry['delivery']=_transport(archive_bytes(evidence['root']),target,identifier)
    (target/'registry-entry.PROPOSED.json').write_bytes(releases.canonical({identifier:entry}))
    return dict(release_id=identifier,parts=len(entry['delivery']['parts']),
        archive_bytes=entry['delivery']['archive_bytes'],output=str(target),published=False)


def context(path,identifier=None,base=ROOT,inference_mode=None):
    base=Path(base); target=destination(path,base)
    identifier=identifier or None
    mode=inference_mode or ('approved_release' if identifier else 'disabled')
    releases.require(mode in ('disabled','approved_release'),'DELIVERY_INFERENCE_MODE_INVALID')
    if mode=='disabled':
        releases.require(identifier is None,'DELIVERY_DISABLED_RELEASE_CONFLICT')
        return _copy_context(target,None,None,base)
    releases.require(bool(identifier),'RELEASE_SELECTION_REQUIRED')
    # This must precede all output, including selection of the deployment pointer.
    evidence=releases.metadata(identifier,base=base,deployment=True,dependencies=False,
        profiles=(__import__('os').environ.get('PSMS_NLP_RUNTIME','legacy'),'semantic_ocr'))
    from runtime_model_cache import delivery_settings
    delivery_settings(identifier,evidence,base)
    report=_copy_context(target,identifier,releases.entry_for(identifier,base),base)
    releases.metadata(identifier,base=target/'runtime',dependencies=False,
        profiles=(__import__('os').environ.get('PSMS_NLP_RUNTIME','legacy'),'semantic_ocr'))
    return report


def _copy_context(target,identifier,entry,base):
    """Exact source only. Never copy artifacts or download credentials."""
    files=source_files(base)
    target.mkdir(parents=True)
    for name in sorted(files):
        safe_name(name)
        origin=releases.safe_path(base,name)
        copy=target/'runtime'/name; copy.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(origin,copy)
    # Select only the explicitly requested, already-approved entry in this private
    # build copy. No trust pin, approval, original registry or model is changed.
    registry=dict(schema=releases.SCHEMA,deployment_release=identifier,
                  releases={identifier:entry} if entry is not None else {})
    (target/'runtime'/releases.REGISTRY).write_bytes(releases.canonical(registry))
    for name in ('Dockerfile.vercel','vercel.json'):
        shutil.copyfile(releases.safe_path(base,name),target/name)
    docker=(target/'Dockerfile.vercel').read_text(encoding='utf-8-sig')
    releases.require(docker.count('PSMS_INFERENCE_ENABLED=0')==1,'DELIVERY_INFERENCE_BUILD_FLAG_REQUIRED')
    if entry is not None:
        docker=docker.replace('PSMS_INFERENCE_ENABLED=0','PSMS_INFERENCE_ENABLED=1')
    (target/'Dockerfile.vercel').write_text(docker)
    rules=releases.safe_path(base,'.vercelignore').read_text(encoding='utf-8-sig')
    (target/'.dockerignore').write_text(rules)
    (target/'.vercelignore').write_text(rules)
    expected={'Dockerfile.vercel','vercel.json','.dockerignore','.vercelignore'}
    expected |= {'runtime/'+n for n in files}
    actual={p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
    releases.require(actual==expected,'DELIVERY_UNEXPECTED_FILE')
    # Fail even if a future import accidentally adds an artifact/private file.
    forbidden=('.model','.npy','.joblib','.safetensors','.zip','.part','.tar','.gz','.7z',
               '.xlsx','.xls','.xlsm','.csv','.pem','.key','.dump','.backup','.pyc')
    releases.require(not any(n.lower().endswith(forbidden) or Path(n).name.startswith('.env')
        or any(p in Path(n).parts for p in ('__pycache__','venv','.venv','node_modules',
            'uploads','data','output','inference_releases')) for n in actual), 'DELIVERY_PRIVATE_FILE')
    size=sum(p.stat().st_size for p in target.rglob('*') if p.is_file())
    releases.require(size < SOURCE_UPLOAD_LIMIT,'DELIVERY_HOBBY_SOURCE_LIMIT')
    return dict(release_id=identifier,files=len(actual),
        inference_mode='approved_release' if entry is not None else 'disabled',
        source_bytes=size,model_bytes_uploaded=0,
        output=str(target),published=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('initial','review','models','package','context','supabase'))
    parser.add_argument('--output',required=True)
    parser.add_argument('--release')
    parser.add_argument('--inference-mode',choices=('disabled','approved_release'))
    parser.add_argument('--directory',help='Private release (package) or existing transport proposal folder (supabase)')
    parser.add_argument('--project-ref',help='Existing Supabase project for private ROOT archive parts')
    args=parser.parse_args()
    try:
        if args.action=='supabase':
            releases.require(bool(args.release),'RELEASE_SELECTION_REQUIRED')
            result=supabase(args.output,args.release,args.directory,args.project_ref)
        elif args.action=='context':
            result=context(args.output,args.release,inference_mode=args.inference_mode)
        elif args.action=='package':
            releases.require(bool(args.release),'RELEASE_SELECTION_REQUIRED')
            result=package(args.output,args.release,args.directory)
        else:
            result=globals()[args.action](args.output)
        print(json.dumps(result,sort_keys=True)); return 0
    except releases.ReleaseError as error:
        print(str(error)); return 1
    except Exception:
        print('DELIVERY_PREPARATION_FAILED'); return 1


if __name__=='__main__':
    raise SystemExit(main())
