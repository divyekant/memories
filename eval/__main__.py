"""CLI entrypoint: python -m eval [options]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import yaml

from eval.models import EvalConfig
from eval.loader import load_all_scenarios
from eval.runner import EvalRunner
from eval.reporter import save_report, format_summary
from eval.memories_client import MemoriesClient
from eval.cc_executor import CCExecutor
from eval.judge import LLMJudge

logger = logging.getLogger("eval")


def main():
    parser = argparse.ArgumentParser(description="Memories Efficacy Eval Harness")
    parser.add_argument("--config", default="eval/config.yaml", help="Path to eval config YAML")
    parser.add_argument("--scenarios", default="eval/scenarios", help="Path to scenarios directory")
    parser.add_argument("--results", default="eval/results", help="Path to results directory")
    parser.add_argument("--category", default=None, help="Run only scenarios in this category")
    parser.add_argument("--scenario", default=None, help="Run a single scenario by ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load config
    with open(args.config) as f:
        config_data = yaml.safe_load(f)

    # Override with env vars
    config_data["memories_api_key"] = os.getenv("MEMORIES_API_KEY") or config_data.get("memories_api_key", "")
    mcp_server_path = os.getenv("EVAL_MCP_SERVER_PATH") or config_data.pop("mcp_server_path", "")
    config = EvalConfig(**config_data)

    # Load scenarios
    scenarios = load_all_scenarios(args.scenarios, category=args.category)
    if args.scenario:
        scenarios = [s for s in scenarios if s.id == args.scenario]

    if not scenarios:
        logger.error("No scenarios found.")
        sys.exit(1)

    logger.info("Loaded %d scenarios", len(scenarios))

    # Build dependencies
    memories = MemoriesClient(url=config.memories_url, api_key=config.memories_api_key)
    if not memories.health_check():
        logger.error("Memories service not reachable at %s", config.memories_url)
        sys.exit(1)

    executor = CCExecutor(
        timeout=config.cc_timeout,
        memories_url=config.memories_url,
        memories_api_key=config.memories_api_key,
        mcp_server_path=mcp_server_path,
    )

    # LLM judge (optional)
    judge = None
    try:
        sys.path.insert(0, os.getcwd())
        from llm_provider import get_provider
        provider = get_provider()
        if provider:
            judge = LLMJudge(provider)
            logger.info("LLM judge enabled: %s/%s", provider.provider_name, provider.model)
    except ImportError:
        logger.warning("llm_provider not importable — LLM judge disabled")

    # Run
    runner = EvalRunner(config, memories_client=memories, cc_executor=executor, judge=judge)
    report = runner.run_all(scenarios)

    # Save & print
    path = save_report(report, args.results)
    summary = format_summary(report)
    print(summary)
    logger.info("Report saved to %s", path)


if __name__ == "__main__":
    main()
