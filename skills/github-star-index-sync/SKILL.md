---
name: github-star-index-sync
description: Incrementally update, regenerate, regroup, or correct a categorized GitHub-star index in an Obsidian vault from `github-star/*.md`. Use in Codex or Claude Code when the user asks to update or rebuild the GitHub Star index, sync starred repositories, reorganize categories, correct a repository summary, configure the default vault, or diagnose the skill installation. Generate concise Chinese purpose and audience text primarily from README evidence while preserving unchanged classifications.
---

# GitHub Star Index Sync

Use this skill from Codex as `$github-star-index-sync` or from Claude Code as
`/github-star-index-sync`. Treat `github-star/*.md` as read-only inputs.

Resolve the shared installation for shell commands:

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.agents/skills/github-star-index-sync}"
```

## First-Time Setup

Run the bundled installer from the currently loaded skill directory. It installs one shared copy
under `~/.agents/skills/` and creates Codex and Claude Code symlinks.

Configure the default vault once:

```bash
python3 "$SKILL_DIR/scripts/build_index.py" configure --vault "/path/to/obsidian-vault"
python3 "$SKILL_DIR/scripts/build_index.py" doctor
```

Store the GitHub token in `GITHUB_TOKEN` or the macOS Keychain service
`github-star-index-token`. Never write a token into the skill or its config file.

If Claude Code cannot access a vault outside its working directory, tell the user to run
`/add-dir <vault-path>` and retry. Do not modify Claude Code permissions automatically.

## Update The Index

1. Run `prepare`; configured values are used when flags are omitted:

```bash
python3 "$SKILL_DIR/scripts/build_index.py" prepare
```

2. Exit code `0` with no pending work means the update is complete.
3. Exit code `2` is expected when projects need summarization. Do not treat it as a failure or
   stop the workflow.
4. Read `<vault>/.github-star-index/pending.json`.
5. Create one updates JSON containing every pending project and the complete category list.
6. Run:

```bash
python3 "$SKILL_DIR/scripts/build_index.py" finalize \
  --updates /tmp/github-star-index-updates.json
```

When more than 15 projects are pending, inspect README evidence in batches of 10 to 15 projects,
then merge all results into the single updates JSON before `finalize`.

## Generate Project Updates

For each pending project:

- Read `readme_evidence` first. Use repository description, topics, and local description only as
  supporting or fallback sources.
- Write `purpose` as a concrete Chinese description of what the project does, at most 50
  characters.
- Write `audience` as a Chinese target-user description, at most 20 characters.
- Set `source_basis` to `readme`, `repo-description`, `local-description`, or `insufficient`.
- When evidence is insufficient, use `项目说明不足，需人工确认`; do not infer a purpose.
- Preserve `existing_category_id` when `category_locked` is true.
- Prefer an existing category for a new project. Add one only when no existing category is
  accurate.

Use this shape:

```json
{
  "categories": [
    {
      "id": "stable-lowercase-id",
      "name": "分类名称",
      "description": "分类边界说明"
    }
  ],
  "projects": [
    {
      "key": "owner/repo",
      "category_id": "stable-lowercase-id",
      "purpose": "项目用途",
      "audience": "目标用户",
      "source_basis": "readme"
    }
  ]
}
```

## Regroup Or Correct

Regroup all projects only when explicitly requested:

```bash
python3 "$SKILL_DIR/scripts/build_index.py" prepare --regroup
```

Refresh one summary without unlocking its category:

```bash
python3 "$SKILL_DIR/scripts/build_index.py" prepare --refresh owner/repo
```

Then create the updates JSON and run `finalize`.

## Required Checks

- Run `doctor` after installation or configuration changes.
- Confirm every source note appears exactly once.
- Confirm every item has an Obsidian link, GitHub link, purpose, and audience.
- Confirm normal updates retain existing categories and category order.
- Report degraded GitHub or README fetches.
- Never edit individual `github-star/*.md` notes.
