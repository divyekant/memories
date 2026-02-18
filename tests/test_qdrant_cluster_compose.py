"""Tests for generated N-node Qdrant compose overlays."""

import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script_name",
    ["render_qdrant_cluster_compose.py", "render_cluster_compose.py"],
)
def test_render_three_node_cluster(tmp_path, script_name):
    output = tmp_path / "docker-compose.qdrant-cluster.generated.yml"
    cmd = [
        "/Users/dk/projects/memories/.venv/bin/python",
        str(ROOT / "scripts" / script_name),
        "--nodes",
        "3",
        "--output",
        str(output),
    ]
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert output.exists()

    text = output.read_text(encoding="utf-8")
    assert "qdrant_node1:" in text
    assert "qdrant_node3:" in text
    assert "--bootstrap" in text
