# Third-Party Notices

This repository uses third-party open-source packages.

## Policy

- Upstream package licenses remain with their respective authors.
- Dependency attribution must be preserved when distributing builds.
- For release artifacts, generate a license report from the exact locked dependencies used for that build.

## Generate a License Report

Use the project virtual environment and run:

```bash
./.venv/bin/pip install pip-licenses
./.venv/bin/pip-licenses --from=mixed --format=markdown > THIRD_PARTY_LICENSE_REPORT.md
```

Review the generated report before release publication.
