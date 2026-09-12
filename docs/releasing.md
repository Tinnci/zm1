# Releasing / 发布

The prepared version is **0.3.0**, collecting the continuous observation and
publication fixes since 0.2.1. Review the [changelog](../CHANGELOG.md), especially
the removal of unsupported gas entities.

```sh
uv sync --locked --group dev
uv run pytest
uv run ruff check
uv run python scripts/build_release_package.py --tag v0.3.0
```

The local archive is `dist/zm1.zip`. It contains the component contents directly,
including English/Chinese translations and `brand/icon.png`. It has no enclosing
`custom_components/zm1` directory, repository files or Python caches. This layout
matches `hacs.json` and supports direct extraction to `/config/custom_components/zm1`.

After review, push the release commit and its matching `vX.Y.Z` tag. **Release**
also accepts an existing `tag` for a rerun. Every job checks out that tag, reuses
CI verification and validates project/manifest versions before packaging. GitHub
notes are generated from history; the existing checksum asset is retained.

发布入口仅接受稳定语义版本标签。源代码、官方校验、归档与标签必须对应；普通 CI 和
发布复用同一验证流程，避免再次用 unittest 漏跑 pytest 测试。实机断报改善与闪存减写
比例仍需部署后测量，不能由发布测试推断。
