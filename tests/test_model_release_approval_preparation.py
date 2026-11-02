"""New approval/exit gates only. Synthetic bytes, no Docker/Linux/ML/DB/HTTP."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

import inference_releases as releases
from deploy import model_release_gate as gate
from deploy.finalize_model_release import finalize,DECISION_SCHEMA,ONLINE_REVIEW_SCOPE
from deploy.inference_release import archive_bytes
from test_inference_releases import ReleaseTests

ROOT=Path(__file__).resolve().parents[1]


class ApprovalTests(unittest.TestCase):
    setUpFixture=ReleaseTests.setUp
    write_contract=ReleaseTests.write_contract
    write_registry=ReleaseTests.write_registry
    pin_modified_manifest=ReleaseTests.pin_modified_manifest

    def setUp(self):
        self.setUpFixture()
        (self.base/'output').mkdir()
        self.manifest['runtime'].update(locally_verified_python='3.13.16',linux_verified=False,
                                       validation_expectation_only=True)
        self.entry['manifest']=self.manifest
        self.pin_modified_manifest()
        self.entry['delivery']=dict(archive_sha256=hashlib.sha256(archive_bytes(self.root)).hexdigest(),
            authentication=dict(type='supabase_signed_parts',project_ref='fenrqjzupidswgrjzdgn',
                bucket='psms-private-model-releases',release_id=self.identifier))
        self.write_registry()
        self.evidence=dict(schema=gate.SCHEMA,passed=True,system='Linux',architecture='x86_64',
            candidate_release_id=self.identifier,candidate_manifest_sha256=self.entry['manifest_sha256'],
            candidate_archive_sha256=self.entry['delivery']['archive_sha256'],remote_bytes_verified=True,
            artifact_fingerprint=hashlib.sha256(releases.canonical(self.manifest['artifacts'])).hexdigest(),
            lock_sha256=releases.sha256(ROOT/'deploy/requirements-staging-linux-amd64.lock.txt'),
            code_sha256=gate.fingerprints(),source_commit='1'*40,python='3.13.16',
            workflow_run_url='https://github.com/synthetic/example/actions/runs/123',
            packages=self.manifest['runtime']['packages'],memory_limit_bytes=gate.MEMORY,
            memory_peak_bytes=1234567,cpus=1,workers=2,phase_timeout_seconds=180,
            wall_seconds=100.0,process_exit_code=0,models_approved=False,hosted_verified=False,
            native=dict(code='READY'),semantic_device='cpu',semantic_schema_shape=[12,384],
            detail=dict(workers=2,tasks=[dict(passed=True),dict(passed=True)],actual_ocr=True,
                loading_race_tested=True,safe_loading_failure=True,synthetic_timeout_enforced=True))
        self.evidence_path=self.base/'evidence.json'
        self.evidence_path.write_bytes(releases.canonical(self.evidence))
        self.decision=dict(schema=DECISION_SCHEMA,decision='APPROVE',release_id='synthetic-final-v2',
            candidate_release_id=self.identifier,candidate_manifest_sha256=self.entry['manifest_sha256'],
            linux_evidence_sha256=releases.sha256(self.evidence_path),source_commit='1'*40,
            workflow_run_url='https://github.com/synthetic/example/actions/runs/123',
            approved_by='SYNTHETIC UNIT FIXTURE ONLY',approved_at_utc=(datetime.now(timezone.utc)-
                timedelta(seconds=1)).isoformat(),quality_review='SYNTHETIC REVIEW',deployment_review='SYNTHETIC REVIEW',
            approved_profiles=['legacy','semantic_ocr'],limitations_accepted=True,
            experimental_rf_limitations_accepted=False,use_scope='CAPSTONE_SYNTHETIC_ADVISORY_ONLY')
        self.decision_path=self.base/'decision.json'
        self.decision_path.write_bytes(releases.canonical(self.decision))
        manager=patch.object(gate,'candidate',return_value=(self.identifier,self.entry))
        manager.start();self.addCleanup(manager.stop)

    def finalize(self):
        return finalize(self.decision_path,self.evidence_path,self.root,self.base/'output/final',base=self.base)

    def test_default_nonapproval_and_changed_evidence_create_no_final_output(self):
        self.decision['decision']='NOT_APPROVED'
        self.decision_path.write_bytes(releases.canonical(self.decision))
        with self.assertRaisesRegex(releases.ReleaseError,'OWNER_APPROVAL_REQUIRED'):
            self.finalize()
        self.assertFalse((self.base/'output/final').exists())
        self.decision['decision']='APPROVE';self.decision['linux_evidence_sha256']='0'*64
        self.decision_path.write_bytes(releases.canonical(self.decision))
        with self.assertRaisesRegex(releases.ReleaseError,'EVIDENCE_DECISION_MISMATCH'):
            self.finalize()
        self.assertFalse((self.base/'output/final').exists())

    def test_written_success_without_exit_and_weaker_resource_limits_are_rejected(self):
        for changes in (dict(process_exit_code=None),dict(wall_seconds=181),dict(workers=1),
                        dict(memory_limit_bytes=4*1024**3),dict(cpus=2),dict(remote_bytes_verified=False),
                        dict(candidate_manifest_sha256='0'*64)):
            with self.subTest(changes=changes),self.assertRaises(releases.ReleaseError):
                gate.validate_report(dict(self.evidence,**changes),self.entry)

    def test_synthetic_finalization_preserves_bytes_registry_and_profile_restriction(self):
        registry=(self.base/releases.REGISTRY).read_bytes()
        manifest=(self.root/releases.MANIFEST).read_bytes()
        result=self.finalize()
        self.assertFalse(result['registry_modified']);self.assertFalse(result['deployed'])
        self.assertEqual((self.base/releases.REGISTRY).read_bytes(),registry)
        self.assertEqual((self.root/releases.MANIFEST).read_bytes(),manifest)
        new=releases.read_json(self.base/'output/final/registry-entry.APPROVED.json')['synthetic-final-v2']
        self.assertEqual(new['manifest']['artifacts'],self.manifest['artifacts'])
        self.assertEqual(new['approval']['approved_profiles'],['legacy','semantic_ocr'])
        self.assertTrue(new['manifest']['runtime']['linux_verified'])
        self.assertNotIn('retrained_rf',new['approval']['approved_profiles'])
        self.assertNotEqual(new['manifest_sha256'],self.entry['manifest_sha256'])
        self.assertNotEqual(new['delivery']['archive_sha256'],self.entry['delivery']['archive_sha256'])
        for name,record in self.manifest['artifacts'].items():
            self.assertEqual(releases.sha256(self.base/'output/final/private-models/synthetic-final-v2'/name),
                             record['sha256'])

    def test_rf_profile_requires_its_explicit_experimental_review(self):
        self.decision['approved_profiles'].append('retrained_rf')
        self.decision_path.write_bytes(releases.canonical(self.decision))
        with self.assertRaisesRegex(releases.ReleaseError,'RF_EXPERIMENTAL_REVIEW_REQUIRED'):
            self.finalize()
        self.assertFalse((self.base/'output/final').exists())

    def test_authorized_online_review_requires_owner_acceptance_and_retains_restrictions(self):
        self.decision.update(use_scope=ONLINE_REVIEW_SCOPE,
            approved_profiles=['legacy','semantic_ocr','retrained_rf'],
            experimental_rf_limitations_accepted=True,
            authorized_online_review_conditions_accepted=False)
        self.decision_path.write_bytes(releases.canonical(self.decision))
        with self.assertRaisesRegex(releases.ReleaseError,'AUTHORIZED_ONLINE_REVIEW_ACCEPTANCE_REQUIRED'):
            self.finalize()
        self.assertFalse((self.base/'output/final').exists())
        self.decision['authorized_online_review_conditions_accepted']=True
        self.decision_path.write_bytes(releases.canonical(self.decision))
        before=(self.base/releases.REGISTRY).read_bytes()
        self.finalize()
        entry=releases.read_json(self.base/'output/final/registry-entry.APPROVED.json')['synthetic-final-v2']
        approval=entry['approval']
        self.assertEqual(approval['use_scope'],ONLINE_REVIEW_SCOPE)
        for key in ('authorized_documents_only','review_only','human_confirmation_required'):
            self.assertIs(approval[key],True)
        self.assertIs(approval['automatic_procurement_approval'],False)
        self.assertEqual(entry['manifest']['artifacts'],self.manifest['artifacts'])
        self.assertEqual((self.base/releases.REGISTRY).read_bytes(),before)

    def test_online_scope_does_not_allow_unsigned_decisions_or_unrestricted_use(self):
        self.decision.update(use_scope=ONLINE_REVIEW_SCOPE,
                             authorized_online_review_conditions_accepted=True)
        for changes in ({'decision':'NOT_APPROVED'},{'approved_by':''},
                        {'limitations_accepted':False},{'use_scope':'UNRESTRICTED_MUNICIPAL_USE'}):
            invalid=dict(self.decision,**changes)
            self.decision_path.write_bytes(releases.canonical(invalid))
            with self.assertRaises(releases.ReleaseError):self.finalize()
            self.assertFalse((self.base/'output/final').exists())

    def test_registration_preserves_offline_pointer_and_existing_entries(self):
        before=releases.read_json(self.base/releases.REGISTRY)
        result=finalize(self.decision_path,self.evidence_path,self.root,self.base/'output/final',
                        base=self.base,register=True)
        after=releases.read_json(self.base/releases.REGISTRY)
        self.assertTrue(result['registry_modified'])
        self.assertEqual(after.get('deployment_release'),before.get('deployment_release'))
        for key,value in before['releases'].items():self.assertEqual(after['releases'][key],value)
        new=after['releases']['synthetic-final-v2']
        self.assertEqual(new['manifest']['artifacts'],self.manifest['artifacts'])
        self.assertNotIn('?',new['delivery']['parts'][0]['url'])
        self.assertFalse(result['published']);self.assertFalse(result['deployed'])

    def test_child_success_file_cannot_override_a_process_exit_timeout(self):
        results=self.base/'results';results.mkdir()
        (results/'fetch.json').write_bytes(releases.canonical(dict(remote_bytes_verified=True,
            files_verified=28,manifest_sha256=self.entry['manifest_sha256'],
            archive_sha256=self.entry['delivery']['archive_sha256'])))
        (results/'child.json').write_bytes(releases.canonical(dict(passed=True)))
        resources={'memory.max':str(gate.MEMORY),'cpu.max':'100000 100000','memory.peak':'1234567'}
        with patch.object(gate,'RESULTS',results),patch.object(gate,'_cgroup',side_effect=resources.__getitem__), \
             patch.object(gate.subprocess,'Popen') as popen, \
             patch.object(gate.signal,'SIGKILL',9,create=True), \
             patch.object(gate.os,'killpg',create=True) as kill:
            process=popen.return_value;process.pid=12345
            process.wait.side_effect=[subprocess.TimeoutExpired('SYNTHETIC',180),-9]
            with self.assertRaisesRegex(releases.ReleaseError,'COMPLETED_LINUX_EVIDENCE_REQUIRED'):
                gate.supervise()
            kill.assert_called_once_with(12345,signal.SIGKILL)
        report=releases.read_json(results/'verification.json')
        self.assertFalse(report['passed']);self.assertIsNone(report['process_exit_code'])
        self.assertEqual(report['error'],'GATE_PROCESS_EXIT_TIMEOUT')


class WorkflowTests(unittest.TestCase):
    def test_production_workflow_passes_explicit_classifier_to_the_build_gate(self):
        import yaml
        value=yaml.load((ROOT/'.github/workflows/online-production.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(set(value['on']),{'workflow_dispatch'})
        inputs=value['on']['workflow_dispatch']['inputs']
        self.assertEqual(inputs['nlp_runtime']['options'],['legacy','retrained_rf'])
        self.assertEqual(inputs['nlp_runtime']['default'],'legacy')
        self.assertEqual(inputs['deploy_approved']['default'],'false')
        job=value['jobs']['deploy']
        self.assertEqual(job['env']['PSMS_NLP_RUNTIME'],'${{ inputs.nlp_runtime }}')
        self.assertIn('--runtime "$PSMS_NLP_RUNTIME"',job['steps'][-2]['run'])

    def test_workflow_is_manual_scopes_secret_to_fetch_and_uploads_only_metadata(self):
        import yaml
        value=yaml.load((ROOT/'.github/workflows/model-release-verification.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(set(value['on']),{'workflow_dispatch'})
        self.assertEqual(value['on']['workflow_dispatch']['inputs']['verification_authorized']['default'],'false')
        self.assertNotIn('environment',value['jobs']['verify'])
        steps=value['jobs']['verify']['steps']
        secret_steps=[s for s in steps if 'PSMS_MODEL_PART_TOKENS' in s.get('env',{})]
        self.assertEqual(len(secret_steps),1);self.assertIn(' fetch',secret_steps[0]['run'])
        self.assertEqual(secret_steps[0]['env']['PSMS_MODEL_PART_TOKENS'],
                         '${{ secrets.PSMS_MODEL_VERIFICATION_PART_TOKENS }}')
        upload=steps[-1]['with']['path'].splitlines()
        self.assertEqual(len(upload),4)
        self.assertTrue(all(p.endswith(('.json','.jsonl')) and '*' not in p for p in upload))
        with patch.dict(os.environ,{},clear=True),patch.object(gate.subprocess,'run') as docker:
            with self.assertRaisesRegex(releases.ReleaseError,'GITHUB_UBUNTU_REQUIRED'):
                gate.run()
            docker.assert_not_called()


if __name__=='__main__':
    unittest.main(verbosity=2)
