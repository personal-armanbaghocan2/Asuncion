"""One owner-dispatched Linux/Hobby gap gate; never approval or deployment.

Reuses the EXISTING two-worker concurrent workload unchanged. Its original
180-second deadline includes cold initialization AND real process exit.
Only sanitized JSON/checkpoints are published; weights stay private on runner.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inference_releases as releases

SCHEMA='ASUNCION_MODEL_RELEASE_GATE_V1'
DESCRIPTOR='deploy/proposals/private_model_delivery_nlp_online_original_v1.PROPOSED.json'
WORK=ROOT/'output/model-release-gate'
RESULTS=Path('/results')
LIMIT=180
MEMORY=2*1024**3
CODE=('fasttext_nlp_runtime.py','retrained_nlp_runtime.py','offline_ocr_mapper.py',
      'inference_releases.py','runtime_model_cache.py','deploy/linux_validation/probe.py',
      'tests/OCR_FILETYPE_REGRESSION_TEST.py','deploy/model_release_gate.py',
      'aoq_flexible.py','nlp_runtime_selection.py','Dockerfile.vercel','deploy/model_release_gate.Dockerfile')


def candidate():
    data=releases.read_json(ROOT/DESCRIPTOR)
    releases.require(len(data)==1,'GATE_CANDIDATE_INVALID')
    identifier=releases.release_id(next(iter(data)))
    return identifier,data[identifier]


def fingerprints():
    return {name:releases.sha256(releases.safe_path(ROOT,name)) for name in CODE}


def prepare():
    from deploy.online_delivery import source_files
    identifier,entry=candidate()
    releases.require(not WORK.exists(),'GATE_NEW_WORKSPACE_REQUIRED')
    runtime=WORK/'context/runtime';runtime.mkdir(parents=True)
    names=source_files(ROOT)|set(CODE)|{DESCRIPTOR,'deploy/validate_linux_runtime.py'}
    for name in sorted(names):
        target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(releases.safe_path(ROOT,name),target)
    (runtime/releases.REGISTRY).write_bytes(releases.canonical(dict(schema=releases.SCHEMA,
        deployment_release=None,releases={identifier:entry})))
    releases.metadata(identifier,base=runtime,deployment=False,dependencies=False)
    shutil.copyfile(ROOT/'deploy/model_release_gate.Dockerfile',WORK/'context/Dockerfile')
    (WORK/'context/.dockerignore').write_text('**\n!Dockerfile\n!runtime/\n!runtime/**\n')
    (WORK/'results').mkdir()


def fetch():
    import runtime_model_cache as cache
    identifier,_=candidate();base=WORK/'context/runtime'
    evidence=releases.metadata(identifier,base=base,deployment=False,dependencies=False)
    settings=cache.delivery_settings(identifier,evidence,base)
    releases.require(not (WORK/'models').exists(),'GATE_MODELS_ALREADY_EXIST')
    with tempfile.TemporaryDirectory(prefix='.fetch-',dir=WORK) as directory:
        directory=Path(directory);staged=directory/'verified';staged.mkdir()
        cache._download(settings,directory/'models.zip')
        cache._extract(directory/'models.zip',staged,evidence)
        verified=releases.verify(identifier,base=base,directory=staged,dependencies=False)
        os.replace(staged,WORK/'models')
    (WORK/'results/fetch.json').write_bytes(releases.canonical(dict(remote_bytes_verified=True,
        files_verified=verified['files_verified'],manifest_sha256=evidence['manifest_sha256'],
        archive_sha256=settings['archive_sha256'],models_executed=False,release_activated=False)))


def _github_only():
    releases.require(os.environ.get('GITHUB_ACTIONS')=='true' and platform.system()=='Linux'
        and os.environ.get('GITHUB_RUN_ID','').isdigit()
        and os.environ.get('GITHUB_RUN_ATTEMPT','').isdigit(),'GATE_GITHUB_UBUNTU_REQUIRED')


def run():
    _github_only()
    identifier,_=candidate()
    name='asuncion-model-gate-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
    args=['docker','run','--rm','--name',name,'--network','none','--read-only',
        '--memory','2g','--memory-swap','2g','--cpus','1','--pids-limit','256',
        '--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',str(os.getuid())+':'+str(os.getgid()),
        '--tmpfs','/tmp:rw,size=512m,mode=1777',
        '--mount','type=bind,src='+str(WORK/'models')+',dst=/app/inference_releases/'+identifier+',readonly',
        '--mount','type=bind,src='+str(WORK/'results')+',dst=/results',
        '--env','PSMS_INFERENCE_RELEASE_ID='+identifier,
        '--env','MODEL_GATE_WORKFLOW_URL=https://github.com/'+os.environ['GITHUB_REPOSITORY']+
            '/actions/runs/'+os.environ['GITHUB_RUN_ID'],
        '--env','MODEL_GATE_COMMIT='+os.environ['GITHUB_SHA'],'asuncion-model-gate:verification']
    (WORK/'results/resources.json').write_bytes(releases.canonical(dict(
        memory_limit_bytes=MEMORY,cpus=1,workers=2,phase_timeout_seconds=LIMIT,
        temporary_bytes=512*1024**2,network='none',models_read_only=True,
        hosted_verified=False)))
    with (WORK/'container-private.log').open('wb') as stream:
        try:
            result=subprocess.run(args,stdout=stream,stderr=subprocess.STDOUT,timeout=LIMIT+30)
        except subprocess.TimeoutExpired:
            # Only the newly named owned appliance, never Supabase containers.
            subprocess.run(['docker','kill',name],capture_output=True,timeout=15)
            raise releases.ReleaseError('GATE_CONTAINER_TIMEOUT') from None
    releases.require(result.returncode==0,'GATE_CONTAINER_FAILED')
    validate_report(releases.read_json(WORK/'results/verification.json'),candidate()[1])


def probe():
    from deploy.validate_linux_runtime import deny_network,pins
    def guard(event,args):
        deny_network(event,args)
        if event=='import' and args[0] in ('app','db'):
            raise PermissionError('GATE_DATABASE_IMPORT_DENIED')
    sys.addaudithook(guard)
    releases.require(not any(os.environ.get(k) for k in (
        'SUPABASE_DB_URL','DATABASE_URL','PSMS_MODEL_PART_TOKENS','PSMS_INFERENCE_READ_TOKEN')),
        'GATE_SECRET_ENVIRONMENT_REJECTED')
    releases.require(platform.system()=='Linux' and platform.machine()=='x86_64'
        and platform.python_version()=='3.13.16','GATE_LINUX_RUNTIME_REQUIRED')
    expected=pins()
    releases.require({k:importlib.metadata.version(k) for k in expected}==expected,
                     'GATE_DEPENDENCY_MISMATCH')
    identifier,entry=candidate()
    releases.verify(identifier)  # real hashes/dependencies, before deserialization
    from deploy.linux_validation.probe import concurrent,checkpoint
    with checkpoint('candidate_concurrent_workload'):
        detail=concurrent(identifier)  # preserves both workers and all meaningful assertions
    with checkpoint('post_inference_original_hashes'):
        releases.verify(identifier)
    import offline_ocr_mapper as mapper
    from deploy.runtime_diagnostics import native_probe
    native=native_probe()
    releases.require(native.get('code')=='READY' and str(mapper._MODEL.device)=='cpu'
        and list(mapper._SCHEMA_EMBEDDINGS.shape)==[12,384],'GATE_NATIVE_SEMANTIC_CONTRACT')
    result=dict(detail=detail,python=platform.python_version(),packages={
        p:importlib.metadata.version(p) for p in releases.PACKAGES},code_sha256=fingerprints(),
        native=native,semantic_device='cpu',semantic_schema_shape=[12,384])
    (RESULTS/'child.json').write_bytes(releases.canonical(result))
    # Normal return is essential: supervisor must observe actual process exit.


def _cgroup(name):
    return (Path('/sys/fs/cgroup')/name).read_text().strip()


def supervise():
    identifier,entry=candidate();path=RESULTS/'verification.json'
    fetch_evidence=releases.read_json(RESULTS/'fetch.json')
    releases.require(fetch_evidence.get('remote_bytes_verified') is True
        and fetch_evidence.get('files_verified')==28
        and fetch_evidence.get('manifest_sha256')==entry['manifest_sha256']
        and fetch_evidence.get('archive_sha256')==entry['delivery']['archive_sha256'],
        'GATE_REMOTE_ARTIFACT_EVIDENCE_REQUIRED')
    report=dict(schema=SCHEMA,passed=False,candidate_release_id=identifier,
        candidate_manifest_sha256=entry['manifest_sha256'],artifact_fingerprint=hashlib.sha256(
            releases.canonical(entry['manifest']['artifacts'])).hexdigest(),
        candidate_archive_sha256=entry['delivery']['archive_sha256'],remote_bytes_verified=True,
        lock_sha256=releases.sha256(ROOT/'deploy/requirements-staging-linux-amd64.lock.txt'),
        source_commit=os.environ.get('MODEL_GATE_COMMIT'),phase_timeout_seconds=LIMIT,
        workflow_run_url=os.environ.get('MODEL_GATE_WORKFLOW_URL'),
        workers=2,process_exit_code=None,models_approved=False,hosted_verified=False)
    path.write_bytes(releases.canonical(report))
    releases.require(_cgroup('memory.max')==str(MEMORY),
                     'GATE_MEMORY_LIMIT_REQUIRED')
    quota,period=map(int,_cgroup('cpu.max').split())
    releases.require(quota==period,'GATE_ONE_CPU_REQUIRED')
    started=time.monotonic()
    with (RESULTS/'child-private.log').open('wb') as stream:
        process=subprocess.Popen([sys.executable,'-B',__file__,'probe'],stdout=stream,
            stderr=subprocess.STDOUT,start_new_session=True)
        try:
            code=process.wait(timeout=LIMIT)
            report['process_exit_code']=code
            if code==0:
                report.update(releases.read_json(RESULTS/'child.json'))
                report['passed']=True
            else:report['error']='GATE_CHILD_FAILED'
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.wait()
            report['error']='GATE_PROCESS_EXIT_TIMEOUT'
    report.update(wall_seconds=round(time.monotonic()-started,3),
        system=platform.system(),architecture=platform.machine(),memory_limit_bytes=MEMORY,
        memory_peak_bytes=int(_cgroup('memory.peak')),cpus=1)
    try:
        validate_report(report,entry)
    except releases.ReleaseError as error:
        report.update(passed=False,error=report.get('error',str(error)))
        path.write_bytes(releases.canonical(report))
        raise
    path.write_bytes(releases.canonical(report))


def validate_report(report,entry):
    releases.require(report.get('schema')==SCHEMA and report.get('passed') is True
        and report.get('process_exit_code')==0 and report.get('system')=='Linux'
        and report.get('architecture')=='x86_64' and report.get('phase_timeout_seconds')==LIMIT
        and report.get('workers')==2 and report.get('cpus')==1
        and report.get('memory_limit_bytes')==MEMORY
        and 0<report.get('memory_peak_bytes',0)<=MEMORY
        and 0<report.get('wall_seconds',0)<LIMIT
        and report.get('models_approved') is False and report.get('hosted_verified') is False,
        'GATE_COMPLETED_LINUX_EVIDENCE_REQUIRED')
    releases.require(report.get('native',{}).get('code')=='READY'
        and report.get('semantic_device')=='cpu' and report.get('semantic_schema_shape')==[12,384],
        'GATE_NATIVE_SEMANTIC_CONTRACT')
    detail=report.get('detail',{})
    releases.require(detail.get('workers')==2 and len(detail.get('tasks',[]))==2
        and all(t.get('passed') is True for t in detail['tasks'])
        and all(detail.get(k) is True for k in ('actual_ocr','loading_race_tested',
            'safe_loading_failure','synthetic_timeout_enforced')),'GATE_WORKLOAD_EVIDENCE_REQUIRED')
    releases.require(report.get('candidate_release_id')==entry['manifest']['release_id']
        and report.get('candidate_manifest_sha256')==entry['manifest_sha256']
        and report.get('candidate_archive_sha256')==entry['delivery']['archive_sha256']
        and report.get('remote_bytes_verified') is True
        and report.get('artifact_fingerprint')==hashlib.sha256(releases.canonical(
            entry['manifest']['artifacts'])).hexdigest()
        and report.get('python')==entry['manifest']['runtime']['locally_verified_python']
        and report.get('packages')==entry['manifest']['runtime']['packages']
        and report.get('lock_sha256')==releases.sha256(ROOT/'deploy/requirements-staging-linux-amd64.lock.txt')
        and report.get('code_sha256')==fingerprints(),'GATE_EVIDENCE_BINDING_MISMATCH')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','fetch','run','probe','supervise'))
    args=parser.parse_args()
    try:
        globals()[args.action]()
        print('MODEL_GATE_'+args.action.upper()+'_PASSED');return 0
    except releases.ReleaseError as error:
        print(str(error));return 1
    except Exception:
        print('MODEL_GATE_'+args.action.upper()+'_FAILED');return 1


if __name__=='__main__':
    raise SystemExit(main())
