# github-star-index-sync

这是 `github-star-index-sync` skill 的 GitHub 发布草稿仓库。

## 仓库结构

```text
github-star-index-sync-repo/
├── README.md
└── skills/
    └── github-star-index-sync/
        ├── SKILL.md
        ├── agents/
        │   └── openai.yaml
        ├── scripts/
        │   ├── build_index.py
        │   └── install.py
        └── tests/
            ├── test_build_index.py
            └── test_install.py
```

## 这个仓库放什么

- `skills/github-star-index-sync/SKILL.md` 是 skill 的入口文件
- `skills/github-star-index-sync/scripts/` 放运行和安装脚本
- `skills/github-star-index-sync/tests/` 放基础测试
- `skills/github-star-index-sync/agents/` 放与 agent 行为相关的配置

## 使用方式

### 1. 作为 GitHub 仓库发布

把这个仓库推到你的 GitHub 账号下后，其他人可以通过仓库内容查看 skill 代码。

建议保留这个固定路径：

```text
skills/github-star-index-sync/SKILL.md
```

这样后续升级时，外部引用和安装路径都更稳定。

### 2. 作为 skill 安装包的来源

如果你后面要再提供一个单文件安装版本，可以在本地把 skill 打包成 `.skill` 文件，再把产物放到 GitHub Releases。

## 发布前检查

- 不要提交 GitHub token、Obsidian vault 路径或其他敏感信息
- 保持 skill 名称 `github-star-index-sync` 不变
- 如果要更新 skill，优先在这个仓库里改源码，再重新打包

## 下一步

如果你要继续，我可以接着帮你做下面任意一项：

1. 补一个适合开源的 `LICENSE`
2. 补一个更完整的 `gitignore`
3. 直接帮你整理成可以 `git init`、`git add`、`git push` 的完整发布版本
