# Third-party notices

Repository Quality Guard 正式 `.skill.zip` 携带锁定的 Python wheelhouse 与 Node 离线 payload，以支持外部 Skill 断网 bootstrap；仓库内 `.agents` 只携带第一方运行代码、规则、精确依赖锁和安装器，不携带 wheel、`node_modules`、tests 或构建缓存。`runtime/install_dependencies.py` 会优先复用精确宿主环境；若随包介质存在则先使用本地锁定依赖，只有本地介质缺失或失效时才执行带硬超时的联网 fallback。

- `apted==1.0.3` — MIT — Python AST tree edit distance。
- `tree-sitter==0.26.0` — MIT — Tree-sitter Python binding。
- `tree-sitter-python==0.25.0` — MIT — Python grammar。
- `ruff==0.15.20` — MIT — lint/format quality gate。
- `typescript@5.8.3` — Apache-2.0 — JavaScript/TypeScript AST parser。
- `postcss@8.5.6` — MIT — CSS parser。
- `nanoid@3.3.11` — MIT — PostCSS runtime dependency。
- `picocolors@1.1.1` — ISC — PostCSS runtime dependency。
- `source-map-js@1.2.1` — BSD-3-Clause — PostCSS runtime dependency。

不存在静默 weak-mode fallback。锁定依赖缺失时，完整 `.skill.zip` 会优先使用随包 `offline/wheelhouse` 与 `offline/node_modules.zip`；没有可用本地介质时，启动器/安装器才联网安装精确版本，并对该联网 subprocess 设置硬超时。设置 `RQG_OFFLINE_ONLY=1` 时完全禁止网络；安装态 `.agents` 则只能复用宿主已安装依赖/已有 QG cache，或在非离线模式下进行有界联网安装，否则 fail-loud。Node.js/npm/Clang 属系统能力，不打包进 Skill；只有显式 `--install-system` 或 `RQG_INSTALL_SYSTEM_DEPS=1` 才允许安装器调用宿主包管理器。
