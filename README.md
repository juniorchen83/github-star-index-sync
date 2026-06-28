# github-star-index-sync

`github-star-index-sync` 是一个用于整理 GitHub Star 索引的 skill。它会读取 `github-star/*.md` 里的资料，结合仓库 README 证据，帮助你在 Obsidian 知识库中增量更新、重新生成或修正星标项目索引。

这个仓库保存的是 skill 的源码、脚本和测试，适合直接从 GitHub 查看、维护和发布。

## 主要功能

- 增量更新星标项目索引
- 重新生成整个索引
- 重新分组项目分类
- 修正某个项目的简介或用途说明
- 配置默认的 Obsidian vault
- 检查 skill 是否安装正常

## 适用场景

- 你已经在 Obsidian 里维护 GitHub Star 笔记
- 你想把星标项目整理成可读索引
- 你需要批量补充项目用途、受众说明
- 你想用 Codex 或 Claude Code 自动化整理星标资料

## 运行原则

- `github-star/*.md` 视为只读输入，不要直接修改
- 项目用途和受众尽量根据 README 证据生成
- 证据不足时，不要硬猜，保留为“项目说明不足，需人工确认”
- 已有分类在没有充分理由时应尽量保留
- 涉及 GitHub token 时，不要把 token 写进仓库文件

## 仓库结构

```text
github-star-index-sync-repo/
├── README.md
├── LICENSE
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

### 目录说明

- `skills/github-star-index-sync/SKILL.md`：skill 的入口说明
- `skills/github-star-index-sync/scripts/`：安装、准备、生成索引的脚本
- `skills/github-star-index-sync/tests/`：基础测试
- `skills/github-star-index-sync/agents/`：与 agent 行为相关的配置

## 前置条件

- Python 3
- 能访问本地 Obsidian vault
- 能访问 GitHub API
- 如果要自动安装到 Claude Code / Codex 的环境中，需要本机具备相应目录权限

## 安装说明

### 方式一：从仓库源码安装

如果你已经把仓库克隆到本地，可以直接运行安装脚本：

```bash
python3 skills/github-star-index-sync/scripts/install.py
```

安装脚本会：

- 把 skill 安装到共享目录 `~/.agents/skills/github-star-index-sync`
- 为 Codex 和 Claude Code 创建入口
- 检查核心脚本和测试是否可用

如果你想先看安装计划，不真正修改文件，可以先试：

```bash
python3 skills/github-star-index-sync/scripts/install.py --dry-run
```

### 方式二：从打包文件安装

如果你后面把这个 skill 打包成 `.skill` 文件，也可以通过打包产物安装。这个仓库当前保留的是源码结构，便于后续继续维护和发布。

## 首次配置

### 1. 配置默认 vault

首次使用时，先配置你的 Obsidian vault：

```bash
python3 skills/github-star-index-sync/scripts/build_index.py configure --vault "/path/to/obsidian-vault"
python3 skills/github-star-index-sync/scripts/build_index.py doctor
```

### 2. 配置 GitHub 访问凭证

建议把 GitHub token 放在环境变量 `GITHUB_TOKEN` 中，或者放在 macOS Keychain 的 `github-star-index-token` 服务里。

不要把 token 写进仓库文件、README、skill 配置或提交记录里。

## 使用方法

### 1. 更新索引

```bash
python3 skills/github-star-index-sync/scripts/build_index.py prepare
```

如果命令返回有待处理内容，就根据生成的 `pending.json` 补充项目说明，再执行 `finalize`：

```bash
python3 skills/github-star-index-sync/scripts/build_index.py finalize \
  --updates /tmp/github-star-index-updates.json
```

### 2. 重新分组

如果你希望系统性重排分类，可以显式启用重新分组模式：

```bash
python3 skills/github-star-index-sync/scripts/build_index.py prepare --regroup
```

### 3. 刷新单个项目

如果只想更新某一个仓库的简介：

```bash
python3 skills/github-star-index-sync/scripts/build_index.py prepare --refresh owner/repo
```

## 生成结果包含什么

这个 skill 主要产出一份结构化的 GitHub Star 索引，通常会包含：

- 项目名称
- 项目链接
- 本地笔记链接
- 项目用途
- 目标受众
- 分类信息

## 示例输出

下面是一段**示意性**输出，不代表真实仓库内容，只是帮助你快速理解最终索引长什么样：

```md
# GitHub Star 索引

> 这是自动生成页。请运行 GitHub Star Index Sync skill 重建（Codex：`$github-star-index-sync`；Claude Code：`/github-star-index-sync`）。
> 更新时间：2026-06-28 21:30:00
> 项目总数：2
> 分类总数：1

## AI 工具（2）

### Awesome MCP Tools

- **Obsidian：** [[github-star/awesome-mcp-tools.md|打开笔记]]
- **GitHub：** [awesome/mcp-tools](https://github.com/awesome/mcp-tools)
- **项目用途：** 汇总常用 MCP 工具和使用示例
- **适合用户：** 研究 AI 工具的人

---

### README GPT

- **Obsidian：** [[github-star/readme-gpt.md|打开笔记]]
- **GitHub：** [readme/gpt](https://github.com/readme/gpt)
- **项目用途：** 帮助生成和整理 README 文档
- **适合用户：** 写项目说明的人
```

你可以从这个示例看出，这个 skill 生成的结果一般会把每个项目拆成四块：

- Obsidian 笔记入口
- GitHub 仓库链接
- 项目用途
- 适合用户
