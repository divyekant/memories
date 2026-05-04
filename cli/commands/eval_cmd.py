"""CLI commands for running evaluation benchmarks."""

import click
import json
import sys
from pathlib import Path

from cli import app


@app.group("eval")
def eval_group():
    """Run evaluation benchmarks."""
    pass


@eval_group.command("longmemeval")
@click.option("--judge-provider", default="anthropic", help="LLM judge provider")
@click.option("--judge-model", default=None, help="Override judge model")
@click.option("--output", default=None, help="Output file path for results JSON")
@click.option("--compare", default=None, help="Previous results file for regression delta")
@click.option("--questions", default=0, type=int, help="Limit to N questions (0=all)")
@click.option("--k", default=5, help="Number of search results per question")
@click.option("--url", default="http://localhost:8901", help="Memories service URL")
@click.option("--api-key", default="", help="API key")
@click.option("--mode", type=click.Choice(["tool", "system"]), default="tool",
              help="Eval mode: 'tool' = raw API search, 'system' = agent + MCP tools")
def longmemeval(judge_provider, judge_model, output, compare, questions, k, url, api_key, mode):
    """Run LongMemEval benchmark against the Memories engine."""
    import os
    from eval.memories_client import MemoriesClient
    from eval.longmemeval import LongMemEvalRunner
    from eval.setup_validation import validate_eval_setup
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    api_key = api_key or os.getenv("MEMORIES_API_KEY", "")
    url = os.getenv("EVAL_MEMORIES_URL") or os.getenv("MEMORIES_URL") or url
    mcp_server_path = os.getenv(
        "EVAL_MCP_SERVER_PATH",
        str(Path(__file__).parent.parent.parent / "mcp-server" / "index.js"),
    )
    setup_report = validate_eval_setup(
        memories_url=url,
        mcp_server_path=mcp_server_path,
        require_mcp=mode == "system",
        require_claude=mode == "system",
        allow_unsafe_target=os.getenv("EVAL_ALLOW_UNSAFE_TARGET") == "1",
    )
    for message in setup_report.warnings:
        click.echo(f"WARN: {message}", err=True)
    if not setup_report.ok:
        raise click.ClickException("; ".join(setup_report.errors))

    client = MemoriesClient(url=url, api_key=api_key)
    runner = LongMemEvalRunner(client=client, judge_provider=judge_provider, judge_model=judge_model)

    click.echo("Loading LongMemEval dataset...")
    dataset = runner.load_dataset()
    click.echo(f"Loaded {len(dataset)} questions")
    if questions > 0:
        dataset = dataset[:questions]
        click.echo(f"Running subset: {questions} questions")

    click.echo("Initializing judge...")
    runner.init_judge()
    if runner._judge is None:
        raise click.ClickException(
            "Judge initialization failed. Set ANTHROPIC_API_KEY or another supported judge provider."
        )

    # Initialize CCExecutor for system mode
    cc_executor = None
    if mode == "system":
        from eval.cc_executor import CCExecutor
        cc_executor = CCExecutor(
            timeout=120,
            memories_url=url,
            memories_api_key=api_key,
            mcp_server_path=mcp_server_path,
        )
        CCExecutor.cleanup_stale_auto_memory()
        click.echo(f"System eval mode: MCP server at {mcp_server_path}")

    click.echo(f"Running {len(dataset)} questions (k={k}, mode={mode})...")
    scored = []
    source_prefix = "eval/longmemeval"
    for i, question in enumerate(dataset, start=1):
        qid = question.get("question_id", i)
        qtype = question.get("question_type", "unknown")
        click.echo(f"[{i}/{len(dataset)}] {qid} ({qtype})")
        try:
            seeded = runner.seed_question(question, source_prefix=source_prefix)
            click.echo(f"  Seeded {seeded} memory chunks")
            if mode == "system" and cc_executor:
                result = runner.run_question_system(question, cc_executor=cc_executor, source_prefix=source_prefix)
            else:
                result = runner.run_question(question, k=k, source_prefix=source_prefix)
            if not result.get("context"):
                score, reasoning = 0.0, "No context retrieved"
            else:
                score, reasoning = runner._judge_single(result)
            scored.append({**result, "score": score, "reasoning": reasoning})
        finally:
            runner.clear_question(question, source_prefix=source_prefix)

    # Get version from pyproject.toml
    version = "unknown"
    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if line.startswith("version"):
                version = line.split('"')[1]
                break

    report = runner.report(scored, version=version, previous=compare, eval_mode=mode)

    # Print summary
    click.echo(f"\nLongMemEval v{report.version} ({report.timestamp[:10]})")
    click.echo(f"Judge: {report.judge['provider']}/{report.judge['model']}")
    delta_str = ""
    if report.delta:
        d = report.delta.get("overall", 0)
        delta_str = f" ({'+' if d >= 0 else ''}{d*100:.1f}% vs {report.delta['vs_version']})"
    click.echo(f"Overall: {report.overall*100:.1f}%{delta_str}")
    for cat, score in sorted(report.categories.items()):
        cat_delta = ""
        if report.delta and "categories" in report.delta:
            cd = report.delta["categories"].get(cat, 0)
            cat_delta = f" ({'+' if cd >= 0 else ''}{cd*100:.1f}%)"
        click.echo(f"  {cat}: {score*100:.1f}%{cat_delta}")

    # Save results
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(report.to_json())
        click.echo(f"\nResults saved to {output}")
