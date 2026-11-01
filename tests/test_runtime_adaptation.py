"""Dependency-free behavior regressions for scoped runtime adaptations.

Load the actual methods from source so these checks also run without AstrBot's
optional WebUI, graph, and database dependencies installed.
"""
import ast
import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]


def load_methods(path, class_name, names, **extra):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    if {n.name for n in methods} != set(names):
        raise AssertionError(f"Missing methods: {set(names) - {n.name for n in methods}}")
    for method in methods:
        method.decorator_list = [d for d in method.decorator_list if isinstance(d, ast.Name) and d.id == "staticmethod"]
    cls.body = methods
    cls.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    namespace = dict(Any=Any, Dict=Dict, List=List, Optional=Optional,
                     asyncio=asyncio, logger=logging.getLogger("adaptation-test"))
    namespace.update(extra)
    exec(compile(module, str(ROOT / path), "exec"), namespace)
    return namespace[class_name], namespace


class WebUIBindingTests(unittest.IsolatedAsyncioTestCase):
    async def check_binding(self, enabled, server):
        cls, _ = load_methods("webui/manager.py", "WebUIManager", ["setup_and_start"], _server_instance=server)
        manager = cls()
        manager._config = SimpleNamespace(enable_web_interface=enabled)
        manager._database_manager = object()
        manager._acquire_persona_manager = AsyncMock(return_value="persona")
        manager._setup_services = AsyncMock()
        await manager.setup_and_start()
        manager._setup_services.assert_awaited_once_with("persona", manager._database_manager)

    async def test_embedded_pages_bind_when_standalone_disabled(self):
        server = SimpleNamespace(start=AsyncMock())
        await self.check_binding(False, server)
        server.start.assert_not_awaited()

    async def test_embedded_pages_bind_without_optional_web_server(self):
        await self.check_binding(True, None)

    async def test_standalone_still_starts_after_binding(self):
        server = SimpleNamespace(start=AsyncMock(), host="localhost", port=8080)
        await self.check_binding(True, server)
        server.start.assert_awaited_once()

    async def test_failed_binding_does_not_start_server(self):
        server = SimpleNamespace(start=AsyncMock())
        cls, _ = load_methods("webui/manager.py", "WebUIManager", ["setup_and_start"], _server_instance=server)
        manager = cls()
        manager._config = SimpleNamespace(enable_web_interface=True)
        manager._database_manager = None
        manager._acquire_persona_manager = AsyncMock(return_value=None)
        manager._setup_services = AsyncMock(side_effect=RuntimeError("binding failed"))
        await manager.setup_and_start()
        server.start.assert_not_awaited()


class HookScopeTests(unittest.IsolatedAsyncioTestCase):
    def service(self, **config):
        class TextPart:
            def __init__(self, text):
                self.text = text
                self.temporary = False

            def mark_as_temp(self):
                self.temporary = True

        cls, _ = load_methods("services/hooks/llm_hook_handler.py", "LLMHookHandler", ["handle", "_fetch_v2", "_fetch_social", "_collect_jargon", "_inject", "_append_extra_user_content", "_legacy_inject"], time=time, AstrMessageEvent=Any, get_event_persona_scope=lambda *args: "persona", TextPart=TextPart, CACHE_FRIENDLY_LLM_HOOK_TARGET="extra_user_content_parts", LEGACY_LLM_HOOK_TARGETS={"system_prompt", "prompt"})
        service = cls()
        settings = dict(enable_llm_hooks=True, llm_hook_allowed_sessions=["qq:GroupMessage:g1"], enable_command_pass_through=False, llm_hook_include_v2=False, rerank_top_k=5)
        settings.update(config)
        service._config = SimpleNamespace(**settings)
        service._diversity_manager = object()
        service._group_id_to_unified_origin = {}
        service._v2_integration = SimpleNamespace(get_enhanced_context=AsyncMock(return_value={"knowledge_context": "knowledge"}))
        service._memory_delegated = lambda: False
        service._perf_tracker = SimpleNamespace(record=Mock())
        for name in ["social", "diversity", "jargon", "few_shots", "shadow"]:
            setattr(service, "_fetch_" + name, AsyncMock(return_value=None))
        for name in ["social", "v2", "diversity", "jargon", "few_shots", "shadow", "session_updates"]:
            setattr(service, "_collect_" + name, Mock())
        service._inject = Mock()
        return service

    async def test_other_session_is_untouched(self):
        for origin in ["qq:GroupMessage:g2", "other:GroupMessage:g1", None]:
            service = self.service()
            req = SimpleNamespace(prompt="original", system_prompt="persona")
            await service.handle(SimpleNamespace(unified_msg_origin=origin), req)
            service._fetch_social.assert_not_awaited()
            self.assertEqual(vars(req), dict(prompt="original", system_prompt="persona"))
            self.assertEqual(service._group_id_to_unified_origin, {})

    async def test_allowed_session_keeps_local_context_without_v2(self):
        service = self.service()
        del service._collect_jargon
        del service._inject
        service._fetch_jargon.return_value = "confirmed local meaning"
        service._diversity_manager = SimpleNamespace(get_current_style=lambda: "style", get_current_pattern=lambda: "pattern")
        event = SimpleNamespace(unified_msg_origin="qq:GroupMessage:g1", get_group_id=lambda: "g1", get_sender_id=lambda: "user")
        req = SimpleNamespace(prompt="request", system_prompt="existing group persona", extra_user_content_parts=[])
        await service.handle(event, req)
        service._fetch_jargon.assert_awaited_once_with(event, "g1")
        service._fetch_diversity.assert_awaited_once_with("g1")
        service._v2_integration.get_enhanced_context.assert_not_awaited()
        service._perf_tracker.record.assert_called_once()
        self.assertEqual(req.prompt, "request")
        self.assertEqual(req.system_prompt, "existing group persona")
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertIn("confirmed local meaning", req.extra_user_content_parts[0].text)
        self.assertTrue(req.extra_user_content_parts[0].temporary)

    async def test_v2_enabled_preserves_existing_retrieval(self):
        service = self.service(llm_hook_include_v2=True)
        result = await service._fetch_v2("request", "g1")
        self.assertEqual(result, {"knowledge_context": "knowledge"})
        service._v2_integration.get_enhanced_context.assert_awaited_once_with("request", "g1", top_k=5)

    async def test_local_only_disables_v2_and_background_guidance(self):
        for local_only in [False, True]:
            service = self.service(llm_hook_local_only=local_only, llm_hook_include_v2=True)
            del service._fetch_social
            service._config.include_social_relations = True
            service._config.include_affection_info = True
            service._config.enable_expression_patterns = True
            service._config.enable_goal_driven_chat = False
            service._social_context_injector = SimpleNamespace(format_complete_context=AsyncMock(return_value="local learned context"))
            self.assertEqual(await service._fetch_social("g1", "user"), "local learned context")
            kwargs = service._social_context_injector.format_complete_context.await_args.kwargs
            self.assertIs(kwargs["include_behavior_guidance"], not local_only)
            self.assertTrue(kwargs["include_expression_patterns"])
            await service._fetch_v2("request", "g1")
            self.assertEqual(service._v2_integration.get_enhanced_context.await_count, 0 if local_only else 1)


if __name__ == "__main__":
    unittest.main()
