import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from crew import core, hosts, workflow_hooks
from crew.workflow import WorkflowStore
from crew.context_store import ConflictError

CLI = Path(__file__).resolve().parents[1] / 'bin' / 'crew'

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.project = self.root / 'project'; self.project.mkdir(); (self.project/'.git').mkdir()
        self.env = patch.dict(os.environ, {'CODEX_HOME': str(self.root/'home'), 'CREW_DATA_HOME': str(self.root/'data'), 'CREW_HOST':'codex', 'CREW_LANG':'en'})
        self.env.start(); hosts.select('codex')
        for role in ('implementer','reviewer'):
            core.role_write('project', self.project, role, dict(core.presets('en')[role],model='chosen-model',effort='low'),'create')
        self.store = WorkflowStore(self.project)
        self.task = self.store.context.open_session('codex','parent')['task_id']
        self.d = self.store.start(self.task,'parent','Build feature with tests','implementer','reviewer')
        (self.project/'app.py').write_text('version one')
    def tearDown(self): self.env.stop(); self.tmp.cleanup()
    def change(self, action, **args):
        self.d = self.store.change(self.task,self.store.read(self.task)['version'],action,**args)
        return self.d
    def event(self, name, **args):
        result = workflow_hooks.handle(dict(hook_event_name=name,session_id='parent',cwd=str(self.project),**args),self.project)
        self.d = self.store.read(self.task)
        return result
    def dispatch(self, agent='worker', early=False, reverse=False, response_string=False):
        d = self.change('dispatch'); a = d['attempt']; role = hosts.export().agent_name(a['role'])
        kwargs = dict(tool_name='spawn_agent',tool_use_id='call-'+agent,tool_input={'agent_type':role,'message':'[crew-dispatch:'+a['token']+']\nGoal'})
        self.event('PreToolUse', **kwargs)
        events = ['SubagentStop','SubagentStart'] if reverse else ['SubagentStart','SubagentStop']
        if early:
            for e in events: self.event(e, agent_id=agent,agent_type=role)
        response = {'agent_id':agent}
        self.event('PostToolUse', **kwargs,tool_response=json.dumps(response) if response_string else response)
        if not early:
            for e in events:self.event(e,agent_id=agent,agent_type=role)
        return kwargs
    def deliver(self):return self.change('deliver',files=['app.py'],summary='implemented',tests='unit tests passed')
    def ready(self):
        self.dispatch();self.deliver();self.dispatch('reviewer');self.change('review',verdict='pass',summary='checked code and tests')
    def test_happy_path(self):
        self.ready();self.assertEqual(self.change('finish')['phase'],'completed')
    def test_out_of_order_lifecycle_correlated_exactly(self):
        self.dispatch(early=True,reverse=True,response_string=True)
        self.assertEqual(self.deliver()['phase'],'needs_review')
    def test_duplicate_events_no_version_bump(self):
        kwargs=self.dispatch();version=self.d['version']
        self.event('PostToolUse',**kwargs,tool_response={'agent_id':'worker'})
        self.event('SubagentStart',agent_id='worker',agent_type='crew_implementer')
        self.event('SubagentStop',agent_id='worker',agent_type='crew_implementer')
        self.assertEqual(version,self.d['version'])
    def test_fabricated_summary_cannot_finish(self):
        with self.assertRaises(core.Error): self.change('finish')
        with self.assertRaises(core.Error): self.deliver()
    def test_missing_pre_receipt_rejects_result(self):
        self.change('dispatch')
        self.event('PostToolUse',tool_name='spawn_agent',tool_use_id='unknown',tool_input={},tool_response={'agent_id':'fake'})
        self.assertIsNone(self.d['attempt']['agent_id'])
    def test_spawn_failure_or_ambiguous_result_not_evidence(self):
        d=self.change('dispatch');token=d['attempt']['token']
        kwargs=dict(tool_name='spawn_agent',tool_use_id='x',tool_input={'agent_type':'crew_implementer','message':'[crew-dispatch:'+token+']'})
        self.event('PreToolUse',**kwargs)
        for response in ({'error':'failed','agent_id':'bad'},'agent_id=bad',{},None):
            self.event('PostToolUse',**kwargs,tool_response=response)
            self.assertIsNone(self.d['attempt']['agent_id'])
    def test_wrong_role_denied(self):
        self.change('dispatch')
        result=self.event('PreToolUse',tool_name='spawn_agent',tool_use_id='x',tool_input={'agent_type':'default','message':'[crew-dispatch:'+self.d['attempt']['token']+']'})
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
    def test_unrelated_or_partial_lifecycle_does_not_complete(self):
        kwargs=self.dispatch();self.change('retry',reason='new implementation needed');self.change('dispatch')
        self.event('SubagentStop',agent_id='worker',agent_type='crew_implementer')
        with self.assertRaises(core.Error):self.deliver()
    def test_reviewer_cannot_be_prior_implementer(self):
        self.dispatch();self.deliver();self.dispatch('worker')
        with self.assertRaises(core.Error):self.change('review',verdict='pass',summary='self review')
    def test_changes_invalidate_review(self):
        self.ready();(self.project/'app.py').write_text('changed')
        with self.assertRaises(core.Error):self.change('finish')
    def test_changed_before_review_rejected(self):
        self.dispatch();self.deliver();self.dispatch('reviewer');(self.project/'app.py').write_text('changed')
        with self.assertRaises(core.Error):self.change('review',verdict='pass',summary='stale')
    def test_review_changes_then_new_implementation(self):
        self.dispatch();self.deliver();self.dispatch('reviewer');self.change('review',verdict='changes',summary='fix a defect')
        self.assertEqual(self.d['phase'],'changes_needed')
        self.dispatch('worker2');self.deliver();self.dispatch('reviewer2');self.change('review',verdict='pass',summary='fixed')
        self.assertEqual(self.change('finish')['phase'],'completed')
    def test_compare_and_swap(self):
        version=self.d['version'];self.change('dispatch')
        with self.assertRaises(ConflictError):self.store.change(self.task,version,'pause',reason='stale')
    def test_stop_bounded(self):
        self.assertEqual(self.event('Stop')['decision'],'block')
        self.assertEqual(self.event('Stop'),{})
        self.assertEqual(self.event('Stop',stop_hook_active=True),{})
    def test_pause_cancel_stop_allowed(self):
        self.change('pause',reason='waiting for approval');self.assertEqual(self.event('Stop'),{})
        self.change('resume',reason='approval received');self.change('cancel',reason='user cancelled');self.assertEqual(self.event('Stop'),{})
    def test_resume_keeps_assignment(self):
        self.dispatch();token=self.d['attempt']['token']
        self.change('pause',reason='external approval');self.change('resume',reason='approval received')
        self.event('SessionStart',source='compact');self.event('SessionStart',source='resume')
        self.assertEqual(self.d['attempt']['token'],token)
        self.assertEqual(self.d['phase'],'implementing')
    def test_deleted_context_not_resurrected(self):
        self.store.context.delete(self.task)
        self.assertIn('workflow inactive',workflow_hooks.handle({'hook_event_name':'SessionStart','session_id':'parent'},self.project)['hookSpecificOutput']['additionalContext'])
        new=self.store.context.open_session('codex','parent')
        self.assertNotEqual(new['task_id'],self.task)
        self.assertIsNone(self.store.for_session('parent'))
    def test_ordinary_session_has_no_workflow(self):
        result=workflow_hooks.handle({'hook_event_name':'Stop','session_id':'ordinary'},self.project)
        self.assertEqual(result,{})
    def test_artifact_boundaries(self):
        self.dispatch();(self.project/'link').symlink_to(self.project/'app.py')
        for files in ([], ['../secret'], ['/etc/passwd'],['link']):
            with self.assertRaises(core.Error):self.change('deliver',files=files,summary='test',tests='test')
    def test_cli_read_and_conflict(self):
        cmd=[str(CLI),'workflow','read','--task',self.task,'--project',str(self.project)]
        r=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads(r.stdout)['phase'],'awaiting_assignment')
        r=subprocess.run([str(CLI),'workflow','dispatch','--task',self.task,'--expected-version','0','--project',str(self.project)],capture_output=True,text=True)
        self.assertEqual(r.returncode,2);self.assertEqual(json.loads(r.stdout)['error_type'],'ConflictError')
    def test_cli_hooks_use_real_adapter_and_keep_exit_zero(self):
        event=dict(hook_event_name='Stop',session_id='parent',cwd=str(self.project))
        r=subprocess.run([str(CLI),'hook','receive','--host','codex'],input=json.dumps(event),capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertEqual(json.loads(r.stdout)['decision'],'block')
    def test_ready_can_retry_and_rebuild(self):
        self.ready();(self.project/'app.py').write_text('new version')
        self.change('retry',reason='artifact changed')
        self.assertEqual(self.d['phase'],'changes_needed')
        self.dispatch('new-worker');self.deliver();self.dispatch('new-reviewer');self.change('review',verdict='pass',summary='reviewed new version')
        self.assertEqual(self.change('finish')['phase'],'completed')
    def test_new_goal_in_same_session(self):
        self.ready();self.change('finish');self.change('next',goal='Second feature',acceptance='Second feature tests pass')
        self.assertEqual(self.d['phase'],'awaiting_assignment');self.assertIsNone(self.d['delivery'])
        self.dispatch('second-worker');self.deliver()
    def test_deleted_artifact_restoration_invalidates_review(self):
        (self.project/'app.py').unlink();self.ready()
        self.assertEqual(self.d['delivery']['files'],{'app.py':None})
        (self.project/'app.py').write_text('restored')
        with self.assertRaises(core.Error):self.change('finish')
    def test_review_dispatch_has_delivery_and_acceptance(self):
        from crew.workflow_cli import present
        self.dispatch();self.deliver();self.change('dispatch')
        prompt=present(self.d)['dispatch_input']['message']
        self.assertIn('Independently review',prompt);self.assertIn('app.py',prompt);self.assertIn('Acceptance:',prompt)
        self.assertEqual(present(self.d)['dispatch_input']['agent_type'],hosts.CodexExport.agent_name('reviewer'))
    def test_revision_dispatch_preserves_findings(self):
        from crew.workflow_cli import present
        self.dispatch();self.deliver();self.dispatch('reviewer');self.change('review',verdict='changes',summary='fix missing validation')
        self.change('dispatch')
        self.assertIn('fix missing validation',present(self.d)['dispatch_input']['message'])
    def test_apply_patch_requires_started_implementation(self):
        result=self.event('PreToolUse',tool_name='apply_patch')
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
        self.dispatch();self.d=self.store.read(self.task)
        # dispatch helper stops the worker; a stopped worker has no edit permission.
        self.assertEqual(self.event('PreToolUse',tool_name='apply_patch',agent_id='worker',agent_type='crew_implementer')['hookSpecificOutput']['permissionDecision'],'deny')
        self.deliver();self.assertEqual(self.event('PreToolUse',tool_name='apply_patch')['hookSpecificOutput']['permissionDecision'],'deny')
    def test_unstarted_engineering_edit_gate_and_plain_question(self):
        base=dict(session_id='new-session',hook_event_name='PreToolUse',tool_name='apply_patch')
        self.assertEqual(workflow_hooks.handle(base,self.project)['hookSpecificOutput']['permissionDecision'],'deny')
        base.update(hook_event_name='UserPromptSubmit',prompt='hello')
        self.assertEqual(workflow_hooks.handle(base,self.project),{})
    def test_exported_name_matches_dispatch(self):
        import tomllib
        from crew.workflow_cli import present
        core.export_agents(self.project,'project',True)
        self.change('dispatch')
        exported=tomllib.loads((self.project/'.codex/agents/crew-implementer.toml').read_text())
        self.assertEqual(present(self.d)['dispatch_input']['agent_type'],exported['name'])

    def test_long_sequence_keeps_delegation_at_each_goal(self):
        for index in range(8):
            self.dispatch('worker-'+str(index));self.deliver()
            self.event('SessionStart',source='compact')
            self.assertEqual(self.d['phase'],'needs_review')
            self.dispatch('reviewer-'+str(index));self.change('review',verdict='pass',summary='review evidence')
            self.change('finish')
            if index < 7:self.change('next',goal='next engineering task')
    def test_chinese_dispatch_preserves_user_content(self):
        from crew import i18n
        from crew.workflow_cli import present
        before=i18n.LANG
        try:
            i18n.set_language('zh');self.change('dispatch');packet=present(self.d)
            self.assertIn('实现目标',packet['dispatch_input']['message'])
            self.assertIn('Build feature with tests',packet['dispatch_input']['message'])
            self.assertIn('协调者',packet['next'])
        finally:i18n.set_language(before)
    def test_tui_read_cancel_uses_same_store(self):
        from crew.workflow_cli import menu
        answers=iter([self.task,'cancel','user request','y','q'])
        result=menu(self.project,lambda *args:next(answers),lambda value:None)
        self.assertEqual(result['status'],'closed');self.assertEqual(self.store.read(self.task)['phase'],'cancelled')
    def test_disabled_hook_has_no_gate(self):
        core.set_enabled('project',self.project,False)
        self.assertEqual(self.event('PreToolUse',tool_name='apply_patch'),{})
    def test_cancel_then_late_receipt_does_not_restart(self):
        kwargs=self.dispatch();self.change('cancel',reason='user cancelled')
        self.event('PostToolUse',**kwargs,tool_response={'agent_id':'worker'})
        self.assertEqual(self.d['phase'],'cancelled')

    def test_disabled_role_rejected(self):
        core.role_write('project',self.project,'implementer',None,'disable')
        with self.assertRaises(core.Error):self.change('dispatch')
    def test_model_override_cannot_replace_role_defaults(self):
        self.change('dispatch')
        inp={'agent_type':'crew_implementer','message':'[crew-dispatch:'+self.d['attempt']['token']+']','model':'unexpected-model'}
        result=self.event('PreToolUse',tool_name='spawn_agent',tool_use_id='call',tool_input=inp)
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
    def test_retry_review_after_artifact_change_restarts_implementation(self):
        self.dispatch();self.deliver();self.dispatch('reviewer')
        (self.project/'app.py').write_text('new content')
        self.change('retry',reason='review interrupted after modifications')
        self.assertEqual(self.d['phase'],'changes_needed');self.assertIsNone(self.d['delivery'])

    def test_claude_template_and_tui_stay_compatible(self):
        from crew.workflow_cli import menu
        hosts.select('claude')
        try:
            for lang in ('zh','en'):
                self.assertNotIn('workflow',core.presets(lang)['coordinator']['instructions'])
            with self.assertRaisesRegex(core.Error,'Codex hooks'):
                menu(self.project,lambda *args:self.fail('Must not prompt'),lambda value:None)
        finally:hosts.select('codex')

    def test_multiline_task_and_evidence(self):
        self.dispatch();self.change('deliver',files=['app.py'],summary='Implemented:\n- feature',tests='python -m unittest\n35 passed')
        self.assertIn('\n',self.d['delivery']['tests'])
        self.dispatch('reviewer');self.change('review',verdict='pass',summary='Requirements:\n- passed')
        self.change('finish');self.change('next',goal='Next\nfeature',acceptance='1. works\n2. tested')
        self.assertIn('\n',self.d['acceptance'])

    def begin_implementation(self):
        d=self.change('dispatch');a=d['attempt']
        kwargs=dict(tool_name='spawn_agent',tool_use_id='live',tool_input={'agent_type':'crew_implementer','message':'[crew-dispatch:'+a['token']+']'})
        self.event('PreToolUse',**kwargs)
        self.event('PostToolUse',**kwargs,tool_response={'agent_id':'live-worker'})
        self.event('SubagentStart',agent_id='live-worker',agent_type='crew_implementer')
    def test_identity_gate_allows_exact_live_implementer(self):
        self.begin_implementation()
        self.assertEqual(self.event('PreToolUse',tool_name='apply_patch',agent_id='live-worker',agent_type='crew_implementer'),{})
    def test_identity_gate_rejects_root_or_legacy_missing_identity_without_guess(self):
        self.begin_implementation()
        result=self.event('PreToolUse',tool_name='apply_patch')
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertIn('cannot verify',result['hookSpecificOutput']['permissionDecisionReason'])
    def test_identity_gate_rejects_unassigned_agent_and_role(self):
        self.begin_implementation()
        for agent,role in [('other','crew_implementer'),('live-worker','crew_reviewer'),('live-worker',None)]:
            result=self.event('PreToolUse',tool_name='apply_patch',agent_id=agent,agent_type=role)
            self.assertEqual(result['hookSpecificOutput']['permissionDecision'],'deny')
    def test_explicit_stage_compatibility_and_visible_status(self):
        from crew.workflow_cli import present
        self.change('cancel',reason='older host')
        self.change('next',goal='compatible task',edit_policy='stage')
        self.begin_implementation()
        self.assertEqual(self.event('PreToolUse',tool_name='apply_patch'),{})
        self.assertEqual(present(self.d)['edit_protection'],'stage')
    def test_old_workflow_without_policy_keeps_stage_compatibility(self):
        self.begin_implementation()
        with self.store.context._transaction(write=True) as conn:
            data=json.loads(conn.execute('SELECT data FROM workflows WHERE task_id=?',(self.task,)).fetchone()[0])
            data.pop('edit_policy')
            conn.execute('UPDATE workflows SET data=? WHERE task_id=?',(json.dumps(data),self.task))
        self.assertEqual(self.event('PreToolUse',tool_name='apply_patch'),{})
    def test_identity_gate_keeps_shell_outside_coverage(self):
        self.begin_implementation()
        self.assertEqual(self.event('PreToolUse',tool_name='exec_command'),{})

    def test_compaction_resume_preserves_identity_rule_and_assignment(self):
        self.begin_implementation();token=self.d['attempt']['token']
        for source in ('compact','resume','compact'):
            result=self.event('SessionStart',source=source)
            self.assertIn('identity',result['hookSpecificOutput']['additionalContext'])
            self.event('UserPromptSubmit',prompt='continue engineering')
            self.assertEqual(self.d['attempt']['token'],token)
            self.assertEqual(self.event('PreToolUse',tool_name='apply_patch')['hookSpecificOutput']['permissionDecision'],'deny')
            self.assertEqual(self.event('PreToolUse',tool_name='apply_patch',agent_id='live-worker',agent_type='crew_implementer'),{})
        self.event('SubagentStop',agent_id='live-worker',agent_type='crew_implementer')
        self.deliver();self.event('SessionStart',source='compact')
        self.assertEqual(self.event('PreToolUse',tool_name='apply_patch',agent_id='live-worker',agent_type='crew_implementer')['hookSpecificOutput']['permissionDecision'],'deny')

    def add_resource(self, kind='process', locator='terminal:42'):
        self.change('resource-add',kind=kind,locator=locator,owner='parent',purpose='QA server',creation_evidence='exec_command returned terminal handle 42')
        return next(reversed(self.d['resources']))
    def test_resources_block_completion_and_preserve_evidence(self):
        self.ready();resource=self.add_resource()
        with self.assertRaises(core.Error):self.change('finish')
        self.change('resource-release',resource_id=resource,evidence='original terminal reports exit code 0')
        self.assertEqual(self.change('finish')['phase'],'completed')
        self.assertEqual(self.d['resources'][resource]['evidence_source'],'reported')
    def test_resources_block_review_pass(self):
        self.dispatch();self.deliver();self.dispatch('reviewer');resource=self.add_resource(kind='app')
        with self.assertRaises(core.Error):self.change('review',verdict='pass',summary='test complete')
        self.change('resource-release',resource_id=resource,evidence='closed task-owned app and checked window list')
        self.change('review',verdict='pass',summary='tested and cleaned')
    def test_cancel_retains_resources_and_blocks_next(self):
        resource=self.add_resource();self.change('cancel',reason='user cancelled')
        self.assertEqual(self.event('Stop'),{})
        with self.assertRaises(core.Error):self.change('next',goal='next')
        self.change('resource-retain',resource_id=resource,evidence='user requested leaving this demo running')
        self.change('next',goal='next');self.assertEqual(self.d['resources'],{})
    def test_resource_records_do_not_touch_real_files(self):
        path=self.project/'keep-me';path.write_text('user data')
        resource=self.add_resource(kind='temporary',locator=str(path))
        self.change('resource-release',resource_id=resource,evidence='reported release')
        self.assertEqual(path.read_text(),'user data')
    def test_resource_owner_and_duplicate_validation(self):
        self.add_resource()
        with self.assertRaises(core.Error):self.add_resource()
        with self.assertRaises(core.Error):self.change('resource-add',kind='app',locator='window:3',owner='unknown',purpose='qa',creation_evidence='opened')
    def test_resource_cleanup_requires_evidence(self):
        resource=self.add_resource()
        with self.assertRaises(core.Error):self.change('resource-release',resource_id=resource,evidence='')
    def test_report_keeps_dispatch_and_delivery_after_completion(self):
        from crew.workflow_cli import report
        self.ready();self.change('finish');result=report(self.d)
        self.assertEqual(result['delivery']['agent_id'],'worker');self.assertEqual(result['review']['agent_id'],'reviewer')
        self.assertEqual(result['delivery']['configured_model'],'chosen-model')
        self.assertEqual(result['evidence']['tests_and_cleanup'],'reported')
        self.assertNotIn('files',result['delivery']);self.assertIn('app.py',result['delivery']['artifact_paths'])
    def test_pause_does_not_resume_from_unrelated_tool_success(self):
        self.begin_implementation();self.change('pause',reason='waiting for explicit approval')
        for _ in range(3):
            self.event('PostToolUse',tool_name='exec_command',tool_use_id='unrelated',tool_input={},tool_response={'ok':True})
            self.assertEqual(self.event('Stop'),{})
            self.assertEqual(self.d['phase'],'blocked')
        self.change('resume',reason='user approved the requested action')
        self.assertEqual(self.d['resume_evidence'],'user approved the requested action')

    def test_cancelled_task_can_record_late_discovered_owned_resource(self):
        self.change('cancel',reason='user stopped work')
        resource=self.add_resource(kind='app',locator='task-owned-window:5')
        self.assertEqual(self.d['phase'],'cancelled')
        with self.assertRaises(core.Error):self.change('next',goal='next')
        self.change('resource-release',resource_id=resource,evidence='closed original app and verified disappearance')
        self.change('next',goal='next')

    def test_claude_hook_does_not_claim_codex_tracking(self):
        hosts.select('claude')
        try:self.assertEqual(workflow_hooks.handle({'hook_event_name':'Stop','session_id':'parent'},self.project),{})
        finally:hosts.select('codex')

if __name__=='__main__':unittest.main()
