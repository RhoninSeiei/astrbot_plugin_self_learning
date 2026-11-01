"""Run explicitly in an isolated image: actual stores, synthetic LLM/embedding."""
import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('data.plugins.learning_smoke', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(spec);sys.modules[spec.name]=pkg;spec.loader.exec_module(pkg)
from data.plugins.learning_smoke.services.integration.lightrag_knowledge_manager import LightRAGKnowledgeManager
from lightrag.prompt import PROMPTS
# Tokenization is synthetic too, so no vocabulary download is needed offline.
from data.plugins.learning_smoke.services.integration import lightrag_knowledge_manager as manager_module
from functools import partial
from lightrag.utils import Tokenizer
manager_module.LightRAG = partial(manager_module.LightRAG, tokenizer=Tokenizer("offline", SimpleNamespace(
    encode=lambda text: list(text.encode('utf-8')),
    decode=lambda tokens: bytes(tokens).decode('utf-8', errors='replace'),
)))

calls=[]
async def llm(prompt, **kwargs):
    calls.append(prompt)
    delimiter=PROMPTS.get('DEFAULT_TUPLE_DELIMITER','<|#|>')
    end=PROMPTS.get('DEFAULT_COMPLETION_DELIMITER','<|COMPLETE|>')
    return f'entity{delimiter}TESTALPHA{delimiter}organization{delimiter}TESTALPHA is a synthetic test organization.\n{end}'
class Embedding:
    def get_dim(self): return 8
    async def get_embeddings(self, texts): return [[0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8] for _ in texts]

async def main():
    with tempfile.TemporaryDirectory() as directory:
        config=SimpleNamespace(data_dir=directory,lightrag_enable_llm_cache=False)
        manager=LightRAGKnowledgeManager(config,SimpleNamespace(generate_response=llm),Embedding())
        await manager.start()
        a=SimpleNamespace(message='TESTALPHA is a synthetic test organization.',sender_name='test',group_id='group_a')
        b=SimpleNamespace(message=a.message,sender_name='test',group_id='group_b')
        first=await manager.process_message_for_knowledge_graph(a,'group_a')
        n=len(calls)
        duplicate=await manager.process_message_for_knowledge_graph(a,'group_a')
        assert duplicate=='duplicate' and len(calls)==n,(duplicate,len(calls),n)
        second=await manager.process_message_for_knowledge_graph(b,'group_b')
        assert first==second=='processed',(first,second)
        assert len(calls)>n,'Group B must process its independent document'
        for group in ('group_a','group_b'):
            graph=list((Path(directory)/'lightrag'/group).rglob('*.graphml'))
            assert graph and any('TESTALPHA' in p.read_text() for p in graph),(group,graph)
        await manager.stop()
        restored=LightRAGKnowledgeManager(config,SimpleNamespace(generate_response=llm),Embedding())
        n=len(calls)
        assert await restored.process_message_for_knowledge_graph(a,'group_a')=='duplicate'
        assert len(calls)==n
        await restored.stop()
        print('OFFLINE_SMOKE_OK: graph output, same-group dedup, cross-group isolation, reload dedup')
asyncio.run(main())
