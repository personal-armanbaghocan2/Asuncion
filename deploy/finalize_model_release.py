"""Finalize ORIGINAL bytes ONLY after real evidence and an explicit owner decision.

No network, SQL, inference, Git mutation or automatic approval. Optional metadata
registration preserves the existing deployment pointer and offline defaults.
All approved output is private under a NEW output path. The source registry and
unapproved v1 transport are preserved. Never set decision=APPROVE for a dry run.
"""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inference_releases as releases
from deploy import model_release_gate as gate
from deploy.online_delivery import destination,_transport
from deploy.inference_release import archive_bytes
from runtime_model_cache import validate_signed_parts

DECISION_SCHEMA='ASUNCION_MODEL_OWNER_DECISION_V1'
ONLINE_REVIEW_SCOPE='AUTHORIZED_ONLINE_REVIEW_ONLY'
ALLOWED_SCOPES=('CAPSTONE_SYNTHETIC_ADVISORY_ONLY',ONLINE_REVIEW_SCOPE)


def finalize(decision_path,evidence_path,candidate_dir,output,*,base=ROOT,register=False):
    base=Path(base)
    decision=releases.read_json(decision_path)
    releases.require(decision.get('schema')==DECISION_SCHEMA and decision.get('decision')=='APPROVE',
                     'FINAL_OWNER_APPROVAL_REQUIRED')
    releases.require(all(isinstance(decision.get(k),str) and decision[k].strip() for k in (
        'approved_by','approved_at_utc','quality_review','deployment_review','workflow_run_url','source_commit'))
        and decision.get('limitations_accepted') is True
        and decision.get('use_scope') in ALLOWED_SCOPES,
        'FINAL_OWNER_REVIEW_FIELDS_REQUIRED')
    if decision['use_scope']==ONLINE_REVIEW_SCOPE:
        releases.require(decision.get('authorized_online_review_conditions_accepted') is True,
                         'FINAL_AUTHORIZED_ONLINE_REVIEW_ACCEPTANCE_REQUIRED')
    approved=datetime.fromisoformat(decision['approved_at_utc'].replace('Z','+00:00'))
    releases.require(approved.tzinfo is not None and approved<=datetime.now(timezone.utc)
        and re.fullmatch(r'https://github\.com/[^/]+/[^/]+/actions/runs/[0-9]+',decision['workflow_run_url'])
        and re.fullmatch(r'[a-f0-9]{40,64}',decision['source_commit']), 'FINAL_OWNER_PROVENANCE_REQUIRED')
    report=releases.read_json(evidence_path)
    identifier,entry=gate.candidate()
    gate.validate_report(report,entry)
    releases.require(decision.get('linux_evidence_sha256')==releases.sha256(evidence_path)
        and decision.get('candidate_manifest_sha256')==entry['manifest_sha256']
        and decision.get('candidate_release_id')==identifier
        and decision['source_commit']==report['source_commit']
        and decision['workflow_run_url']==report.get('workflow_run_url'),'FINAL_EVIDENCE_DECISION_MISMATCH')
    profiles=decision.get('approved_profiles')
    releases.require(isinstance(profiles,list) and len(profiles)==len(set(profiles))
        and {'legacy','semantic_ocr'}<=set(profiles)<=set(releases.PROFILES),'FINAL_PROFILE_APPROVAL_REQUIRED')
    if 'retrained_rf' in profiles:
        releases.require(decision.get('experimental_rf_limitations_accepted') is True,
                         'FINAL_RF_EXPERIMENTAL_REVIEW_REQUIRED')
    final_id=releases.release_id(decision.get('release_id'))
    registry=releases.read_json(base/releases.REGISTRY)
    releases.require(final_id!=identifier and final_id not in registry['releases'], 'FINAL_NEW_RELEASE_ID_REQUIRED')
    target=destination(output,base)
    (base/'output').mkdir(exist_ok=True)
    # Validate everything before creating final output; no local model is loaded.
    with tempfile.TemporaryDirectory(prefix='.final-model-review-',dir=base/'output') as temporary:
        trust=Path(temporary)
        shutil.copyfile(base/releases.SOURCE_CONTRACT,trust/releases.SOURCE_CONTRACT)
        (trust/releases.REGISTRY).write_bytes(releases.canonical(dict(schema=releases.SCHEMA,
            deployment_release=None,releases={identifier:entry})))
        original=releases.metadata(identifier,base=trust,deployment=False,dependencies=False)
        releases.verify(identifier,base=trust,directory=candidate_dir,dependencies=False)
        manifest=deepcopy(original['manifest']);manifest['release_id']=final_id
        manifest['runtime'].update(linux_verified=True,validation_expectation_only=False)
        manifest['approval']=dict(status='approved',approved_profiles=profiles,
            quality_review=decision['quality_review'],deployment_review=decision['deployment_review'],
            approved_by=decision['approved_by'],approved_at_utc=decision['approved_at_utc'],
            use_scope=decision['use_scope'],owner_decision_sha256=releases.sha256(decision_path),
            linux_evidence_sha256=decision['linux_evidence_sha256'],workflow_run_url=decision['workflow_run_url'])
        if decision['use_scope']==ONLINE_REVIEW_SCOPE:
            # These are restrictions on the approval, not an accuracy claim or
            # an approval of a procurement record. Existing routes still require
            # their normal roles, CSRF, review and explicit human confirmation.
            manifest['approval'].update(authorized_documents_only=True,review_only=True,
                human_confirmation_required=True,automatic_procurement_approval=False,
                authorized_online_review_conditions_accepted=True)
        target.mkdir(parents=True)
        root=target/'private-models'/final_id;root.mkdir(parents=True)
        for name in releases.SOURCES:
            source=releases.safe_path(candidate_dir,name)
            copy=root/name;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,copy)
        (root/releases.MANIFEST).write_bytes(releases.canonical(manifest))
        final_entry=dict(directory='inference_releases/'+final_id,model_version=manifest['model_version'],
            manifest=manifest,approval=manifest['approval'],manifest_sha256=releases.sha256(root/releases.MANIFEST))
        (trust/releases.REGISTRY).write_bytes(releases.canonical(dict(schema=releases.SCHEMA,
            deployment_release=None,releases={final_id:final_entry})))
        releases.metadata(final_id,base=trust,deployment=True,dependencies=False,profiles=profiles)
        releases.verify(final_id,base=trust,directory=root,deployment=True,dependencies=False,profiles=profiles)
        final_entry['delivery']=_transport(archive_bytes(root),target,final_id)
        auth=deepcopy(entry['delivery']['authentication']);auth['release_id']=final_id
        final_entry['delivery']['authentication']=auth
        for part in final_entry['delivery']['parts']:
            part['url']=('https://'+auth['project_ref']+'.supabase.co/storage/v1/object/sign/'
                         +auth['bucket']+'/'+part['object_name'])
        validate_signed_parts(final_entry['delivery'],final_id)
        (target/'registry-entry.APPROVED.json').write_bytes(releases.canonical({final_id:final_entry}))
        shutil.copyfile(decision_path,target/'owner-decision.json')
        shutil.copyfile(evidence_path,target/'linux-evidence.json')
        if register:
            registry_path=releases.safe_path(base,releases.REGISTRY)
            releases.require(releases.read_json(registry_path)==registry,
                             'FINAL_REGISTRY_CHANGED_DURING_FINALIZATION')
            updated=deepcopy(registry)
            updated['releases'][final_id]=final_entry
            publish=trust/'registry-to-publish.json'
            publish.write_bytes(releases.canonical(updated))
            os.replace(publish,registry_path)
    return dict(release_id=final_id,original_files_unchanged=28,parts=len(final_entry['delivery']['parts']),
        manifest_sha256=final_entry['manifest_sha256'],archive_sha256=final_entry['delivery']['archive_sha256'],
        registry_modified=register,published=False,deployed=False,output=str(target))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decision',type=Path,required=True)
    parser.add_argument('--linux-evidence',type=Path,required=True)
    parser.add_argument('--candidate-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--register',action='store_true',
        help='After approval/finalization, add the validated entry while preserving the existing deployment pointer')
    args=parser.parse_args()
    try:
        print(json.dumps(finalize(args.decision,args.linux_evidence,args.candidate_dir,args.output,register=args.register),sort_keys=True))
        return 0
    except releases.ReleaseError as error:
        print(str(error));return 1
    except Exception:
        print('FINAL_RELEASE_PREPARATION_FAILED');return 1


if __name__=='__main__':
    raise SystemExit(main())
