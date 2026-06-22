# AGENTS.md — svg-vectorizer

Repo-level guidance for coding agents (Codex / Claude Code) working in this repository.

## Progress single-source-of-truth (SSoT)

Progress truth lives in **three channels**; each fact has exactly one home, and every other
channel only **links** — it never restates:

- **README** = overview / architecture / current code shape. Update it in the **same PR** as the code.
- **GitHub Releases** = version / what shipped. Derived from merged PRs — `gh release create vX.Y.Z --generate-notes` (categories in `.github/release.yml`). Do **not** hand-maintain a `CHANGELOG.md`.
- **Linear** = decisions / status / progress — decision issues + project updates: <https://linear.app/wentaoxu-personal-workplace/project/svg-vectorizer图像转-svg-插件加固功能扩展与双平台适配-668f68c543f2/overview>

`plugins/` source, runtime caches (`~/.cache/svg-vectorizer-codex-plugin`), logs, and outputs
are **evidence** — referenced, never authoritative.

### Per-task rules

- Changed **code truth** (how to run, architecture, behavior) → update the README in the same PR.
- Made a **decision** or a **status change** → write it to the Linear issue (or a project update); do not bury it in a doc as "truth".
- **Version / channel sync** → handled by the manual version-iteration workflow when you cut a version. Releases is the version log; there is no hand-written changelog to keep in sync.

### "Current vs historical"

The current direction is whatever Linear says: the latest project update + active milestone
states + decision-issue states. Any repo statement that conflicts must be corrected, or marked
"superseded / evidence — see Linear" — never left as a stale "current" claim.
