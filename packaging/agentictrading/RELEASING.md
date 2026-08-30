# Releasing `agentictrading`

The package is published to PyPI automatically by
`.github/workflows/publish-pypi.yml` whenever a `v*` tag is pushed.

## One-time setup: PyPI Trusted Publishing (no token needed)

The workflow uses OIDC Trusted Publishing, so no API token is stored in GitHub.
Configure it once on PyPI:

1. Go to https://pypi.org/manage/project/agentictrading/settings/publishing/
   (Project → Settings → Publishing).
2. Add a new **GitHub** trusted publisher with:
   - **Owner:** `TrentonNewWorld`
   - **Repository:** `AgenticTrading`
   - **Workflow name:** `publish-pypi.yml`
   - **Environment:** `pypi`
3. In the GitHub repo, create an environment named `pypi`
   (Settings → Environments → New environment → `pypi`). Optionally add
   protection rules (required reviewers) for an approval gate before publish.

That's it — no `PYPI_API_TOKEN` secret is required.

> Prefer a token instead of OIDC? Add a repo secret `PYPI_API_TOKEN`, delete the
> `permissions: id-token: write` block + `environment:` from the `publish` job,
> and give the publish step:
> `with: { password: ${{ secrets.PYPI_API_TOKEN }} }`.

## Cutting a release

PyPI versions are immutable — every release needs a new version number.

1. Bump the version in the single source of truth:

   `src/agentictrading/__init__.py` → `__version__ = "0.1.1"`

   (`pyproject.toml` reads this automatically via `dynamic = ["version"]`.)

2. Commit, then tag and push:

   ```bash
   git add -A && git commit -m "Release agentictrading 0.1.1"
   git tag v0.1.1
   git push origin main --tags
   ```

3. The workflow builds, checks the tag matches `__version__`, and publishes to
   PyPI. Watch it under the repo's **Actions** tab.

## Manual fallback

```bash
cd packaging/agentictrading
rm -rf dist build src/*.egg-info
python -m build
python -m twine check dist/*
python -m twine upload dist/*        # username __token__, password = PyPI token
```
