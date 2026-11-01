"""Exercise production method bodies without optional plugin services or networks."""
import ast
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

ROOT = Path(__file__).resolve().parents[2]

def method(path, name, **globals_):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    ns = {'asyncio': asyncio, 'logger': SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None), **globals_}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])), path, 'exec'), ns)
    return ns[name]

MANAGER = 'services/integration/lightrag_knowledge_manager.py'
V2 = 'services/core_learning/v2_learning_integration.py'

class IngestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = {}
        self.calls = []
        async def insert(text, ids):
            self.calls.append(ids)
            await asyncio.sleep(0)
            self.states[ids] = {'status': 'processed'}
        self.rag = SimpleNamespace(doc_status=SimpleNamespace(get_by_id=AsyncMock(side_effect=lambda key:self.states.get(key)), index_done_callback=AsyncMock()), ainsert=insert, apipeline_process_enqueue_documents=AsyncMock())
        self.rag.full_docs = SimpleNamespace(get_by_id=AsyncMock(return_value={'content':'persisted'}), index_done_callback=AsyncMock())
        self.manager = SimpleNamespace(_get_rag=AsyncMock(return_value=self.rag), _ingestion_locks={}, _processed_counts={}, _stats_cache={})
        self.run_message = method(MANAGER, 'process_message_for_knowledge_graph', compute_mdhash_id=lambda s,prefix:prefix+hashlib.md5(s.encode()).hexdigest(), sanitize_text_for_encoding=lambda s:s)
        self.message = SimpleNamespace(message='足够长的可学习知识内容。', sender_name='测试', sender_id='1', group_id='g')

    async def run_one(self, group='g'):
        return await self.run_message(self.manager, self.message, group)

    async def test_realtime_and_scheduled_concurrent_ingest_once(self):
        await asyncio.gather(self.run_one(), self.run_one())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.manager._processed_counts['g'], 1)

    async def test_persisted_completed_state_survives_manager_reload(self):
        await self.run_one()
        self.states = json.loads(json.dumps(self.states))
        self.manager._ingestion_locks = {}
        self.manager._processed_counts = {}
        await self.run_one()
        self.assertEqual(len(self.calls), 1)

    async def test_pending_document_resumes_without_reenqueue(self):
        await self.run_one()
        doc_id = self.calls[0]
        self.states[doc_id]['status'] = 'pending'
        async def finish(): self.states[doc_id]['status'] = 'processed'
        self.rag.apipeline_process_enqueue_documents.side_effect = finish
        await self.run_one()
        self.assertEqual(len(self.calls), 1)
        self.rag.apipeline_process_enqueue_documents.assert_awaited_once()

    async def test_failed_document_is_not_success_or_resubmitted(self):
        await self.run_one()
        self.states[self.calls[0]]['status'] = 'failed'
        self.assertEqual(await self.run_one(), 'failed')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.manager._processed_counts['g'], 1)

    async def test_swallowed_library_failure_not_counted_as_success(self):
        async def fail(text, ids): self.states[ids] = {'status': 'failed'}
        self.rag.ainsert = fail
        self.assertEqual(await self.run_one(), 'failed')
        self.assertEqual(self.manager._processed_counts, {})

    async def test_scope_mismatch_rejected_before_rag(self):
        with self.assertRaises(ValueError): await self.run_one('other')
        self.manager._get_rag.assert_not_awaited()

    async def test_batch_only_calls_knowledge_not_memory_or_realtime(self):
        fn = method(V2, 'ingest_scheduled_knowledge', MessageData=lambda **k:SimpleNamespace(**k))
        v2 = SimpleNamespace(_config=SimpleNamespace(knowledge_engine='lightrag'), refresh_provider_bindings=AsyncMock(), _knowledge_manager=SimpleNamespace(process_message_for_knowledge_graph=AsyncMock(return_value='processed')), _memory_manager=SimpleNamespace())
        result = await fn(v2, 'g', [{'group_id':'g','sender_id':'1','sender_name':'测试','message':'足够长的可学习知识内容。','timestamp':1,'platform':'qq'}])
        self.assertEqual(result['processed'], 1)
        v2._knowledge_manager.process_message_for_knowledge_graph.assert_awaited_once()

    def test_both_batch_paths_ingest_before_marking_processed(self):
        source = (ROOT/'services/core_learning/progressive_learning.py').read_text()
        tree = ast.parse(source)
        for name in ['_execute_learning_batch', '_execute_learning_batch_background']:
            node = next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
            calls = [n for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='_ingest_batch_knowledge']
            self.assertEqual(len(calls),2,name)

    async def test_missing_state_exception_retains_batch_for_retry(self):
        self.rag.ainsert = AsyncMock(side_effect=OSError('disk failure'))
        with self.assertRaises(OSError): await self.run_one()
        self.assertEqual(self.manager._processed_counts, {})

    async def test_failed_without_payload_is_not_acknowledged(self):
        async def fail(text, ids): self.states[ids] = {'status': 'failed'}
        self.rag.ainsert = fail
        self.rag.full_docs.get_by_id.return_value = None
        with self.assertRaises(RuntimeError): await self.run_one()

    async def test_flush_failure_is_not_acknowledged(self):
        async def fail(text, ids): self.states[ids] = {'status': 'pending'}
        self.rag.ainsert = fail
        self.rag.doc_status.index_done_callback.side_effect = OSError('flush failed')
        with self.assertRaises(OSError): await self.run_one()

    async def test_empty_batch_resumes_pending_only(self):
        fn = method(V2, 'ingest_scheduled_knowledge', MessageData=lambda **k:SimpleNamespace(**k))
        manager = SimpleNamespace(resume_pending_knowledge=AsyncMock(), process_message_for_knowledge_graph=AsyncMock())
        v2 = SimpleNamespace(_config=SimpleNamespace(knowledge_engine='lightrag'), refresh_provider_bindings=AsyncMock(), _knowledge_manager=manager)
        await fn(v2, 'g', [])
        manager.resume_pending_knowledge.assert_awaited_once_with('g')
        manager.process_message_for_knowledge_graph.assert_not_awaited()

    async def test_native_queue_drained_even_without_new_documents(self):
        fn = method(MANAGER, 'resume_pending_knowledge')
        self.rag.doc_status.get_status_counts = AsyncMock(side_effect=[{'pending':1}, {'processed':1}])
        await fn(self.manager, 'g')
        self.rag.apipeline_process_enqueue_documents.assert_awaited_once()
        self.assertEqual(self.calls, [])

    async def test_same_content_in_different_groups_stays_isolated(self):
        other_states = {}
        async def insert(text, ids): other_states[ids] = {'status':'processed'}
        other = SimpleNamespace(doc_status=SimpleNamespace(get_by_id=AsyncMock(side_effect=lambda key:other_states.get(key))), ainsert=insert)
        self.manager._get_rag.side_effect = lambda group: self.rag if group=='g' else other
        await self.run_one()
        self.message.group_id = 'other'
        await self.run_one('other')
        self.assertEqual(len(self.states), 1)
        self.assertEqual(len(other_states), 1)

    async def test_background_empty_batch_still_drains(self):
        from datetime import datetime
        fn = method('services/core_learning/progressive_learning.py', '_execute_learning_batch_background', datetime=datetime, logger=SimpleNamespace(debug=lambda *a:None,info=lambda *a:None,error=lambda *a,**k:None))
        service = SimpleNamespace(batch_size=20, _ingest_batch_knowledge=AsyncMock(), message_collector=SimpleNamespace(get_unprocessed_messages=AsyncMock(return_value=[])))
        await fn(service,'g')
        service._ingest_batch_knowledge.assert_awaited_once_with('g', [])

    async def test_unpersisted_handoff_blocks_style_finalization(self):
        from datetime import datetime
        fn = method('services/core_learning/progressive_learning.py', '_execute_learning_batch_background', datetime=datetime, logger=SimpleNamespace(debug=lambda *a:None,info=lambda *a:None,error=lambda *a,**k:None))
        rows=[{'group_id':'g','message':'knowledge'}]
        service = SimpleNamespace(batch_size=20, _ingest_batch_knowledge=AsyncMock(side_effect=[None,OSError('disk full')]), message_collector=SimpleNamespace(get_unprocessed_messages=AsyncMock(return_value=rows)), _filter_messages_with_context=AsyncMock(return_value=rows), _get_current_persona=AsyncMock(return_value={}), _mark_messages_processed=AsyncMock(), _save_filtered_messages_for_stats=AsyncMock(), _finalize_learning_batch=AsyncMock())
        await fn(service,'g')
        service._mark_messages_processed.assert_not_awaited()
        service._finalize_learning_batch.assert_not_awaited()
        service._save_filtered_messages_for_stats.assert_not_awaited()

    async def test_actual_loop_is_cancelled_and_awaited_on_stop(self):
        import time
        from datetime import datetime
        source='services/core_learning/progressive_learning.py'
        start=method(source,'start_learning',time=time,datetime=datetime,LearningSession=lambda **kw:SimpleNamespace(**kw))
        stop=method(source,'stop_learning',datetime=datetime)
        entered=asyncio.Event();finished=asyncio.Event()
        async def loop(group):
            try:
                entered.set()
                await asyncio.Event().wait()
            finally: finished.set()
        service=SimpleNamespace(learning_lock=asyncio.Lock(),learning_active={},_learning_tasks={},_group_sessions={},learning_sessions=[],db_manager=SimpleNamespace(save_learning_session_record=AsyncMock()),_learning_loop_safe=loop)
        self.assertTrue(await start(service,'g'))
        await entered.wait()
        await stop(service)
        self.assertTrue(finished.is_set())
        self.assertEqual(service._learning_tasks,{})

    async def test_startup_recovery_respects_whitelist_and_blacklist(self):
        fn=method(V2,'resume_scheduled_knowledge')
        manager=SimpleNamespace(persisted_group_ids=lambda:['allowed','blocked','outside'],resume_pending_knowledge=AsyncMock())
        v2=SimpleNamespace(_config=SimpleNamespace(knowledge_engine='lightrag'),refresh_provider_bindings=AsyncMock(),_knowledge_manager=manager)
        await fn(v2,['allowed','blocked'],['blocked'])
        manager.resume_pending_knowledge.assert_awaited_once_with('allowed')

    async def test_empty_nickname_matches_realtime_fallback(self):
        fn=method(V2,'ingest_scheduled_knowledge',MessageData=lambda **kw:SimpleNamespace(**kw))
        manager=SimpleNamespace(process_message_for_knowledge_graph=AsyncMock(return_value='processed'))
        v2=SimpleNamespace(_config=SimpleNamespace(knowledge_engine='lightrag'),refresh_provider_bindings=AsyncMock(),_knowledge_manager=manager)
        await fn(v2,'g',[{'group_id':'g','sender_id':'42','sender_name':'','message':'足够长的可学习知识内容。'}])
        self.assertEqual(manager.process_message_for_knowledge_graph.await_args.args[0].sender_name,'42')
