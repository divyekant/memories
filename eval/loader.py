"""YAML scenario loader for the Memories eval harness."""

from __future__ import annotations

import os
from typing import Optional

import yaml

from eval.models import Scenario


def load_scenario(path: str) -> Scenario:
    """Load a single YAML file into a Scenario model.

    Raises on malformed YAML or schema-validation errors.
    """
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return Scenario(**data)


def load_all_scenarios(
    scenarios_dir: str, category: Optional[str] = None
) -> list[Scenario]:
    """Load all .yaml files from category subdirectories.

    Directory layout expected::

        scenarios_dir/
            coding/
                coding-001.yaml
            recall/
                recall-001.yaml

    Args:
        scenarios_dir: Root directory containing category subdirectories.
        category: If provided, only load from this subdirectory.

    Returns:
        List of Scenario objects sorted by id. Empty list if no scenarios found.
    """
    scenarios: list[Scenario] = []

    if not os.path.isdir(scenarios_dir):
        return scenarios

    subdirs = [category] if category else sorted(os.listdir(scenarios_dir))

    for subdir in subdirs:
        subdir_path = os.path.join(scenarios_dir, subdir)
        if not os.path.isdir(subdir_path):
            continue
        for filename in sorted(os.listdir(subdir_path)):
            if not filename.endswith((".yaml", ".yml")):
                continue
            filepath = os.path.join(subdir_path, filename)
            scenarios.append(load_scenario(filepath))

    scenarios.sort(key=lambda s: s.id)
    return scenarios
