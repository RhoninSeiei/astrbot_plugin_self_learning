"""Isolated runner for legacy tests that import the old package directory name."""
import importlib.util
import sys
from pathlib import Path
import pytest
root=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
spec=importlib.util.spec_from_file_location('self_learning_EterU',root/'__init__.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
sys.exit(pytest.main(['-o','addopts=','-p','no:cacheprovider','-q','--tb=short','--show-capture=no', *[str(root/'tests/unit'/name) for name in (
 'test_scheduled_knowledge_ingestion.py', 'test_conversation_goal_structured_tasks.py',
 'test_guardrails_json_parsing.py', 'test_maibot_quality_metrics.py',
 'test_provider_registry_rebind.py', 'test_feature_delegation.py',
 'test_media_placeholder_filter.py', 'test_tiered_learning_trigger.py',
 'test_config.py', 'test_webui_manager.py', 'test_webui_dependencies.py',
 'test_page_api_container_binding.py', 'test_persona_updater_factory.py',
 'test_learning_blacklist_filter.py', 'test_learning_toggle_semantics.py',
 'test_human_style_learning.py', 'test_style_approved_expression_context.py',
 'test_style_context_routing.py', 'test_style_learning_statistics.py',
 'test_style_prompt_security.py', 'test_learning_scheduling.py',
 'test_learning_chain_regressions.py', 'test_framework_llm_adapter.py',
 'test_jargon_learning.py')], str(root/'tests/test_runtime_adaptation.py')]))
