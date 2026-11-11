#!/usr/bin/env python3
"""Reproducible exact serialized-input comparison, not model-token billing."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crew.context_store import ContextStore
from crew.context_hooks import handle


def wire(data): return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def run():
    with tempfile.TemporaryDirectory() as tmp, patch('crew.context_store.time.time', return_value=1700000000.0):
        root = Path(tmp); project = root / 'project'; project.mkdir(); (project / '.git').mkdir()
        os.environ['CODEX_HOME'] = str(root / 'codex')
        os.environ['CREW_DATA_HOME'] = str(root / 'data')
        store = ContextStore(project)
        opened = store.open_session('codex', 'benchmark-session')
        task = opened['task_id']
        state = {
            'summary': '实现协作任务状态共享，使用有版本号的检查点和增量更新。',
            'goal': '在不重复投喂完整工程对话的情况下保留决策依据、证据与下一步。',
            'constraints': ['保持现有接口；不能在后台启动其他模型；跨 worktree 共享需要显式任务关联。'] * 10,
            'decisions': ['保存已验证事实，保留用户的明确决定；未知结果标记为未验证。'] * 10,
            'evidence': ['tests/example_test.py:42；相关测试通过，完整日志在项目工件中。'] * 10,
            'progress': ['初始化完成'], 'next': ['验证增量读取'], 'blockers': []}
        saved = store.checkpoint(task, opened['version'], state)
        version = saved['version']
        initial = wire(store.read(task))
        baseline = len(initial); optimized = len(initial); rounds = []
        for number in range(1, 31):
            saved = store.patch(task, version, {'progress': [f'阶段 {number} 验证完成'], 'next': [f'执行阶段 {number + 1}']}, request_id=f'round-{number}')
            full = wire(store.read(task))
            delta = wire(store.read(task, since=version))
            header = handle({'hook_event_name': 'UserPromptSubmit', 'session_id': 'benchmark-session', 'turn_id': f'turn-{number}'}, str(project))
            overhead = len(wire(header))
            # Both workflows pay the same supported hook protocol overhead.
            baseline += len(full) + overhead
            optimized += len(delta) + overhead
            rounds.append({'round': number, 'full_utf8_bytes': len(full), 'delta_utf8_bytes': len(delta), 'hook_utf8_bytes': overhead})
            version = saved['version']
        return {'kind': 'synthetic_serialized_input_proxy', 'rounds': 30, 'clock': 'fixed fixture',
            'baseline': 'initial snapshot + full current structured state and hook header each round',
            'optimized': 'initial snapshot + version delta and same hook header each round',
            'baseline_utf8_bytes': baseline, 'optimized_utf8_bytes': optimized,
            'reduction_percent': round((1 - optimized / baseline) * 100, 2),
            'actual_model_tokens': None, 'model_calls': 0,
            'limits': 'Exact UTF-8 bytes, not tokenizer tokens or account usage. No performance or billing claim for an actual Codex/Claude conversation.',
            'samples': rounds}

if __name__ == '__main__': print(json.dumps(run(), ensure_ascii=False, indent=2))
