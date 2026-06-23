# Contributing

Use the [README](README.md) for user-facing setup, tool reference, and examples.
Use [docs/architecture.md](docs/architecture.md) for MCP, CLI, runtime cache, and
pipeline details.

## Prerequisites

- Git
- Python 3.10 through 3.12 for the plugin runtime path
- Node.js and npm; CI uses Node 22
- GitHub CLI `gh` for PR and release work

## Start From A Fresh Checkout

```sh
git clone https://github.com/w2030298-art/svg-vectorizer-codex-plugin.git
cd svg-vectorizer-codex-plugin
git fetch origin
git switch main
git merge --ff-only origin/main
```

Create one issue-scoped branch from the updated `main`:

```sh
git switch -c w2030298/wen-NNN-short-description
```

If `main` has local-only commits or uncommitted work, stop and resolve that
before branching.

## Local Development

Windows PowerShell. Use an installed Python from the supported 3.10-3.12
range; the example below uses 3.10.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r plugins\svg-vectorizer\server\requirements.txt
.\.venv\Scripts\python -m unittest tests.test_pipeline -v
.\.venv\Scripts\python -m unittest tests.test_mcp_smoke -v
```

POSIX shells. Use `python3.10`, `python3.11`, or `python3.12`, depending on
what is installed.

```sh
python3.10 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r plugins/svg-vectorizer/server/requirements.txt
python -m unittest tests.test_pipeline -v
python -m unittest tests.test_mcp_smoke -v
```

The MCP smoke tests start the Node MCP server and exercise real JSON-RPC tool
calls. Some tests create isolated cache directories under the system temp
directory and may install `@resvg/resvg-js` for renderer-enabled validation.

Optional CLI smoke:

```sh
python plugins/svg-vectorizer/server/pipeline_cli.py --tool run_svg_pipeline --input-json '{"input_path":"tests/fixtures/warm_icon.png","output_dir":"outputs/contributing-smoke","mode":"vtracer","mask_mode":"warm-icon","quality_profile":"balanced"}'
```

`outputs/`, `.venv/`, `node_modules/`, `__pycache__/`, and test caches are
ignored. Do not commit generated output unless the issue explicitly asks for
source-backed evidence.

## Code Style

- Keep changes issue-scoped and match the existing file style.
- Keep the MCP server a thin Node bootstrap and JSON-RPC layer.
- Keep conversion, validation, and repair behavior in
  `svg_vectorizer_pipeline.py`; keep `pipeline_cli.py` as a dispatch shim.
- Update MCP tool schemas, README tool docs, architecture notes, and tests in
  the same PR when behavior changes.
- Use `unittest`, small fixtures, and deterministic temp directories for tests.
- Prefer bounded parameter reruns for repair. Do not hand-edit SVG paths unless
  the issue explicitly asks for that.

## Commit And PR

Before opening a PR:

```sh
git status -sb
python -m unittest tests.test_pipeline -v
python -m unittest tests.test_mcp_smoke -v
```

Use clear, issue-scoped commits. PR titles should include the Linear issue key
and type, for example:

```text
Docs WEN-418: add contributor architecture and release workflow docs
```

Include the local verification commands in the PR body. Apply the GitHub label
that matches the change type so generated release notes land in the right
category. The configured release categories live in `.github/release.yml`:
`Feature`, `Improvement`, `Refactor`, `Bug`, `Docs`, `Chore`, and `Test`.

## Version Records

GitHub Releases is the authoritative version log. After PRs are merged, cut
release notes from merged PR metadata:

```sh
gh release create vX.Y.Z --generate-notes
```

Do not hand-maintain `CHANGELOG.md`. If a repository-level `CHANGELOG.md` is
ever required, keep it as a thin pointer to GitHub Releases or generate it from
Releases, and mark it as derived and non-authoritative.
