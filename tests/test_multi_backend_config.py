"""Test multi-backend config loading and routing resolution."""
import subprocess
import json
import os
import pytest
import tempfile
import yaml


def _run_lib_function(func_call, env=None, config_content=None):
    """Source _lib.sh and call a function, return stdout."""
    lib_path = os.path.join(os.path.dirname(__file__), "..",
                            "mcp-server", "assets", "claude-code", "hooks", "_lib.sh")
    script = f'source "{lib_path}"\n{func_call}'
    full_env = {**os.environ, **(env or {})}
    if config_content:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            full_env["MEMORIES_BACKENDS_FILE"] = f.name
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, env=full_env,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


class TestConfigLoader:
    def test_load_backends_from_yaml(self):
        """_load_backends should parse YAML config into JSON."""
        config = yaml.dump({
            "backends": {
                "local": {"url": "http://localhost:8900", "api_key": "key1", "scenario": "dev"},
                "prod": {"url": "https://prod.example.com", "api_key": "key2", "scenario": "prod"},
            }
        })
        stdout, _, rc = _run_lib_function("_load_backends", config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 2

    def test_fallback_to_env_vars(self):
        """Without config file, should create single backend from env vars."""
        env = {"MEMORIES_URL": "http://localhost:8900", "MEMORIES_API_KEY": "testkey"}
        stdout, _, rc = _run_lib_function("_load_backends", env=env)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 1
        assert data[0]["url"] == "http://localhost:8900"

    def test_env_var_interpolation(self):
        """API key with ${VAR} should be resolved from environment."""
        config = yaml.dump({
            "backends": {
                "prod": {"url": "https://prod.example.com", "api_key": "${MY_SECRET_KEY}"},
            }
        })
        env = {"MY_SECRET_KEY": "resolved-key"}
        stdout, _, rc = _run_lib_function("_load_backends", env=env, config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert data[0]["api_key"] == "resolved-key"

    def test_url_env_var_interpolation(self):
        """URL with ${VAR} should be resolved from environment."""
        config = yaml.dump({
            "backends": {
                "prod": {"url": "${PROD_MEMORIES_URL}", "api_key": "key1"},
            }
        })
        env = {"PROD_MEMORIES_URL": "https://resolved.example.com"}
        stdout, _, rc = _run_lib_function("_load_backends", env=env, config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert data[0]["url"] == "https://resolved.example.com"


class TestRoutingResolution:
    def test_dev_prod_search_routing(self):
        """dev+prod scenario should search both backends."""
        config = yaml.dump({
            "backends": {
                "local": {"url": "http://localhost:8900", "api_key": "k1", "scenario": "dev"},
                "prod": {"url": "https://prod.example.com", "api_key": "k2", "scenario": "prod"},
            }
        })
        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "search"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 2

    def test_dev_prod_extract_routing(self):
        """dev+prod scenario should extract to dev only."""
        config = yaml.dump({
            "backends": {
                "local": {"url": "http://localhost:8900", "api_key": "k1", "scenario": "dev"},
                "prod": {"url": "https://prod.example.com", "api_key": "k2", "scenario": "prod"},
            }
        })
        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "extract"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 1
        assert "localhost" in data[0]["url"]

    def test_diy_routing(self):
        """DIY config with explicit routing should use those rules."""
        config = yaml.dump({
            "backends": {
                "alpha": {"url": "http://alpha:8900", "api_key": "a"},
                "beta": {"url": "http://beta:8900", "api_key": "b"},
            },
            "routing": {
                "search": ["alpha", "beta"],
                "extract": ["alpha"],
            }
        })
        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "search"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 2

    def test_diy_routing_block_style_yaml(self):
        """Block-style YAML routing with 4-space indent should parse correctly."""
        # Write raw YAML (not yaml.dump) to get block style with 4-space indent
        config = """backends:
  alpha:
    url: http://alpha:8900
    api_key: a
  beta:
    url: http://beta:8900
    api_key: b
routing:
  search:
    - alpha
    - beta
  extract:
    - alpha
"""
        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "extract"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 1
        assert data[0]["name"] == "alpha"

        stdout, _, rc = _run_lib_function(
            '_get_backends_for_op "search"', config_content=config)
        assert rc == 0
        data = json.loads(stdout)
        assert len(data) == 2

    def test_single_backend_passthrough(self):
        """Single backend should route everything to it."""
        config = yaml.dump({
            "backends": {
                "only": {"url": "http://localhost:8900", "api_key": "k", "scenario": "dev"},
            }
        })
        for op in ["search", "extract", "add", "feedback"]:
            stdout, _, rc = _run_lib_function(
                f'_get_backends_for_op "{op}"', config_content=config)
            assert rc == 0
            data = json.loads(stdout)
            assert len(data) == 1

    def test_project_context_uses_one_backend_only_and_does_not_change_legacy_routing(self, tmp_path):
        """Collaborative activation is fail-closed, while ordinary routing still fans out."""
        project_dir = tmp_path / "project"
        (project_dir / ".memories").mkdir(parents=True)
        (project_dir / ".memories" / "project.yaml").write_text(
            "project_id: shared-demo\nshared_memory: true\n"
        )
        config = """backends:
  alpha:
    url: http://alpha:8900
    api_key: a
  beta:
    url: http://beta:8900
    api_key: b
"""
        stdout, _, rc = _run_lib_function(
            'CWD="$PROJECT_DIR"; _memories_project_context "$PROJECT_DIR"',
            env={"PROJECT_DIR": str(project_dir), "MEMORIES_URL": ""},
            config_content=config,
        )
        assert rc == 0
        context = json.loads(stdout)
        assert context["active"] is False
        assert context["reason"] == "multiple_backends"

        stdout, _, rc = _run_lib_function(
            'CWD="$PROJECT_DIR"; _get_backends_for_op "search"',
            env={"PROJECT_DIR": str(project_dir), "MEMORIES_URL": ""},
            config_content=config,
        )
        assert rc == 0
        assert len(json.loads(stdout)) == 2
