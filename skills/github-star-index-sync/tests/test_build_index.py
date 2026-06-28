from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_index.py"
SPEC = importlib.util.spec_from_file_location("build_index", SCRIPT)
build_index = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = build_index
SPEC.loader.exec_module(build_index)


class BuildIndexTests(unittest.TestCase):
    def test_old_cli_defaults_to_prepare(self) -> None:
        args = build_index.parse_args(["--vault", "/tmp/vault"])
        self.assertEqual(args.command, "prepare")
        self.assertFalse(args.regroup)

    def test_configure_and_load_default_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            (vault / "github-star").mkdir(parents=True)
            config_path = root / "config.json"
            with mock.patch.dict(
                os.environ,
                {build_index.CONFIG_ENV: str(config_path)},
                clear=False,
            ):
                args = build_index.parse_args(
                    ["configure", "--vault", str(vault)]
                )
                self.assertEqual(build_index.configure(args), 0)
                runtime = build_index.apply_runtime_config(
                    build_index.parse_args(["prepare"])
                )
            self.assertEqual(runtime.vault, str(vault.resolve()))
            self.assertEqual(runtime.source, "github-star")
            self.assertEqual(runtime.output, "GitHub Star 索引.md")
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("token", saved)

    def test_explicit_runtime_values_override_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configured = root / "configured"
            explicit = root / "explicit"
            (configured / "github-star").mkdir(parents=True)
            (explicit / "custom-source").mkdir(parents=True)
            config_path = root / "config.json"
            config = {
                "schema_version": build_index.CONFIG_SCHEMA_VERSION,
                "vault": str(configured),
                "source": "github-star",
                "output": "Configured.md",
                "state": build_index.DEFAULT_STATE,
                "pending": build_index.DEFAULT_PENDING,
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {build_index.CONFIG_ENV: str(config_path)},
                clear=False,
            ):
                runtime = build_index.apply_runtime_config(
                    build_index.parse_args(
                        [
                            "prepare",
                            "--vault",
                            str(explicit),
                            "--source",
                            "custom-source",
                            "--output",
                            "Explicit.md",
                        ]
                    )
                )
            self.assertEqual(runtime.vault, str(explicit.resolve()))
            self.assertEqual(runtime.source, "custom-source")
            self.assertEqual(runtime.output, "Explicit.md")

    def test_missing_config_without_vault_is_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "missing.json"
            with mock.patch.dict(
                os.environ,
                {build_index.CONFIG_ENV: str(config_path)},
                clear=False,
            ):
                with self.assertRaisesRegex(FileNotFoundError, "configure --vault"):
                    build_index.apply_runtime_config(
                        build_index.parse_args(["prepare"])
                    )

    def test_configure_rejects_invalid_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            with mock.patch.dict(
                os.environ,
                {build_index.CONFIG_ENV: str(config_path)},
                clear=False,
            ):
                args = build_index.parse_args(
                    ["configure", "--vault", str(Path(directory) / "missing")]
                )
                with self.assertRaisesRegex(FileNotFoundError, "vault 目录不存在"):
                    build_index.configure(args)
            self.assertFalse(config_path.exists())

    def test_frontmatter_supports_lists_colons_and_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            note = Path(directory) / "repo.md"
            note.write_text(
                "---\r\n"
                "aliases:\r\n"
                "  - Demo\r\n"
                "tags: ['github/topic/cli', 'type/github-star']\r\n"
                "description: Tool: with details\r\n"
                "url: https://github.com/acme/demo\r\n"
                "---\r\n",
                encoding="utf-8",
            )
            data = build_index.parse_frontmatter(note)
        self.assertEqual(data["aliases"], ["Demo"])
        self.assertEqual(data["tags"], ["github/topic/cli", "type/github-star"])
        self.assertEqual(data["description"], "Tool: with details")

    def test_duplicate_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            source = vault / "github-star"
            source.mkdir()
            body = (
                "---\n"
                "aliases:\n  - Demo\n"
                "url: https://github.com/acme/demo\n"
                "---\n"
            )
            (source / "one.md").write_text(body, encoding="utf-8")
            (source / "two.md").write_text(body, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "重复仓库"):
                build_index.load_projects(vault, "github-star")

    def test_readme_cleaning_keeps_features_and_drops_noise(self) -> None:
        readme = """
# Demo
[![build](https://shields.io/x)](https://example.com)

Demo converts documents into structured data for AI workflows.

## Features
- Extracts tables and formulas.
- Exports Markdown and JSON.

## Installation
pip install demo
"""
        evidence, digest = build_index.clean_readme(readme)
        self.assertIn("converts documents", evidence)
        self.assertIn("Extracts tables", evidence)
        self.assertNotIn("pip install", evidence)
        self.assertNotIn("shields.io", evidence)
        self.assertEqual(len(digest), 64)

    def test_source_fingerprint_ignores_topic_order(self) -> None:
        first = {
            "repo_description": "Demo",
            "topics": ["cli", "tool"],
            "readme_digest": "abc",
            "local_description": "",
        }
        second = {**first, "topics": ["tool", "cli"]}
        self.assertEqual(
            build_index.source_fingerprint(first),
            build_index.source_fingerprint(second),
        )

    def test_markdown_shape_sorting_and_separators(self) -> None:
        state = {
            "updated_at": "2026-06-28 12:00:00 Asia/Shanghai",
            "categories": [
                {"id": "tools", "name": "开发工具", "description": "工具", "order": 0}
            ],
            "projects": {
                "acme/z": {
                    "title": "Zulu",
                    "repo": "acme/z",
                    "url": "https://github.com/acme/z",
                    "note_path": "github-star/z.md",
                    "purpose": "处理开发任务。",
                    "audience": "开发者",
                    "category_id": "tools",
                },
                "acme/a": {
                    "title": "Alpha",
                    "repo": "acme/a",
                    "url": "https://github.com/acme/a",
                    "note_path": "github-star/a.md",
                    "purpose": "辅助开发工作。",
                    "audience": "开发者",
                    "category_id": "tools",
                },
            },
        }
        markdown = build_index.render_markdown(state)
        self.assertLess(markdown.index("### Alpha"), markdown.index("### Zulu"))
        self.assertIn("- **Obsidian：** [[github-star/a.md|打开笔记]]", markdown)
        self.assertIn("- **GitHub：** [acme/a](https://github.com/acme/a)", markdown)
        self.assertIn("- **项目用途：** 辅助开发工作。", markdown)
        self.assertIn("- **适合用户：** 开发者", markdown)
        self.assertEqual(markdown.count("\n---\n"), 1)
        self.assertIn("Codex：`$github-star-index-sync`", markdown)
        self.assertIn("Claude Code：`/github-star-index-sync`", markdown)

    def test_summary_length_is_enforced(self) -> None:
        with self.assertRaisesRegex(ValueError, "超过50字"):
            build_index.validate_summary_text("用" * 51, "项目用途", 50)
        with self.assertRaisesRegex(ValueError, "超过20字"):
            build_index.validate_summary_text("人" * 21, "适合用户", 20)

    def test_normal_sync_cannot_move_existing_project(self) -> None:
        pending = {
            "mode": "incremental",
            "categories": [
                {"id": "tools", "name": "开发工具", "description": "开发工具", "order": 0}
            ],
            "projects": [
                {
                    "key": "acme/demo",
                    "category_locked": True,
                    "existing_category_id": "tools",
                    "source_basis": "readme",
                }
            ],
        }
        updates = {
            "categories": [
                {"id": "tools", "name": "开发工具", "description": "开发工具"},
                {"id": "other", "name": "其他", "description": "其他项目"},
            ],
            "projects": [
                {
                    "key": "acme/demo",
                    "category_id": "other",
                    "purpose": "演示用途。",
                    "audience": "开发者",
                    "source_basis": "readme",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "不能改变已有项目分类"):
            build_index.validate_updates(pending, updates)

    def test_normal_sync_cannot_reorder_existing_categories(self) -> None:
        pending = {
            "mode": "incremental",
            "categories": [
                {"id": "first", "name": "第一类", "description": "第一类项目", "order": 0},
                {"id": "second", "name": "第二类", "description": "第二类项目", "order": 1},
            ],
            "projects": [
                {
                    "key": "acme/demo",
                    "category_locked": True,
                    "existing_category_id": "first",
                    "source_basis": "readme",
                }
            ],
        }
        updates = {
            "categories": [
                {"id": "second", "name": "第二类", "description": "第二类项目"},
                {"id": "first", "name": "第一类", "description": "第一类项目"},
            ],
            "projects": [
                {
                    "key": "acme/demo",
                    "category_id": "first",
                    "purpose": "演示用途。",
                    "audience": "开发者",
                    "source_basis": "readme",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "不能改变已有分类顺序"):
            build_index.validate_updates(pending, updates)

    def test_insufficient_source_requires_explicit_marker(self) -> None:
        pending = {
            "mode": "regroup",
            "categories": [],
            "projects": [
                {
                    "key": "acme/unknown",
                    "category_locked": False,
                    "existing_category_id": "",
                    "source_basis": "insufficient",
                }
            ],
        }
        updates = {
            "categories": [
                {"id": "other", "name": "其他项目", "description": "暂时无法归类的项目"}
            ],
            "projects": [
                {
                    "key": "acme/unknown",
                    "category_id": "other",
                    "purpose": "这是一个开发工具。",
                    "audience": "开发者",
                    "source_basis": "insufficient",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "必须标记为需人工确认"):
            build_index.validate_updates(pending, updates)

    def test_environment_token_has_priority(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=False):
            with mock.patch.object(build_index.subprocess, "run") as run:
                self.assertEqual(build_index.get_token(), "env-token")
                run.assert_not_called()

    def test_keychain_token_is_used_without_environment_token(self) -> None:
        result = mock.Mock(returncode=0, stdout="keychain-token\n")
        with mock.patch.dict(
            os.environ, {"GITHUB_TOKEN": "", "USER": "tester"}, clear=False
        ):
            with mock.patch.object(build_index.platform, "system", return_value="Darwin"):
                with mock.patch.object(build_index.shutil, "which", return_value="/usr/bin/security"):
                    with mock.patch.object(build_index.subprocess, "run", return_value=result):
                        self.assertEqual(build_index.get_token(), "keychain-token")

    def test_doctor_is_read_only_and_never_prints_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            source = vault / "github-star"
            source.mkdir(parents=True)
            (source / "demo.md").write_text("---\nurl: https://github.com/a/b\n---\n")
            config_path = root / "config.json"
            config = {
                "schema_version": build_index.CONFIG_SCHEMA_VERSION,
                "vault": str(vault),
                "source": "github-star",
                "output": build_index.DEFAULT_OUTPUT,
                "state": build_index.DEFAULT_STATE,
                "pending": build_index.DEFAULT_PENDING,
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            before = config_path.read_bytes()
            output = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {
                    build_index.CONFIG_ENV: str(config_path),
                    "GITHUB_TOKEN": "never-print-this-token",
                },
                clear=False,
            ):
                with redirect_stdout(output):
                    result = build_index.doctor(build_index.parse_args(["doctor"]))
            self.assertEqual(result, 0)
            self.assertNotIn("never-print-this-token", output.getvalue())
            self.assertIn("GITHUB_TOKEN 环境变量", output.getvalue())
            self.assertEqual(before, config_path.read_bytes())

    def test_github_client_handles_etag_304(self) -> None:
        headers = {"ETag": '"cached"'}
        error = urllib.error.HTTPError(
            "https://example.com", 304, "Not Modified", headers, None
        )
        client = build_index.GitHubClient("token", retries=0)
        with mock.patch.object(build_index.urllib.request, "urlopen", side_effect=error):
            status, data, etag = client.request(
                "https://example.com", "application/json", '"cached"'
            )
        self.assertEqual(status, 304)
        self.assertEqual(data, b"")
        self.assertEqual(etag, '"cached"')

    def test_finalize_writes_state_and_index_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            pending_path = vault / ".github-star-index" / "pending.json"
            updates_path = vault / "updates.json"
            source = {
                "fingerprint": "abc",
                "source_basis": "readme",
                "readme_evidence": "Demo manages terminal sessions.",
            }
            candidate = build_index.empty_state()
            candidate["projects"]["acme/demo"] = {
                "key": "acme/demo",
                "repo": "acme/demo",
                "title": "Demo",
                "url": "https://github.com/acme/demo",
                "note_path": "github-star/demo.md",
                "source": source,
            }
            pending = {
                "schema_version": build_index.SCHEMA_VERSION,
                "mode": "regroup",
                "vault": str(vault),
                "categories": [],
                "projects": [
                    {
                        "key": "acme/demo",
                        "category_locked": False,
                        "existing_category_id": "",
                        "source_basis": "readme",
                    }
                ],
                "candidate_state": candidate,
            }
            updates = {
                "categories": [
                    {"id": "tools", "name": "开发工具", "description": "开发辅助工具"}
                ],
                "projects": [
                    {
                        "key": "acme/demo",
                        "category_id": "tools",
                        "purpose": "管理终端会话。",
                        "audience": "终端用户",
                        "source_basis": "readme",
                    }
                ],
            }
            build_index.atomic_write_json(pending_path, pending)
            build_index.atomic_write_json(updates_path, updates)
            args = build_index.parse_args(
                [
                    "finalize",
                    "--vault",
                    str(vault),
                    "--pending",
                    ".github-star-index/pending.json",
                    "--updates",
                    str(updates_path),
                ]
            )
            self.assertEqual(build_index.finalize(args), 0)
            self.assertFalse(pending_path.exists())
            self.assertTrue((vault / ".github-star-index" / "state.json").exists())
            self.assertIn("### Demo", (vault / "GitHub Star 索引.md").read_text())

    def test_unchanged_render_does_not_rewrite_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            output_path = root / "index.md"
            state = {
                "schema_version": build_index.SCHEMA_VERSION,
                "updated_at": "2026-06-28 10:00:00 Asia/Shanghai",
                "categories": [
                    {"id": "tools", "name": "工具", "description": "工具", "order": 0}
                ],
                "projects": {
                    "acme/demo": {
                        "title": "Demo",
                        "repo": "acme/demo",
                        "url": "https://github.com/acme/demo",
                        "note_path": "github-star/demo.md",
                        "purpose": "演示用途。",
                        "audience": "开发者",
                        "category_id": "tools",
                    }
                },
            }
            build_index.atomic_write_text(output_path, build_index.render_markdown(state))
            before = output_path.stat().st_mtime_ns
            time.sleep(0.01)
            self.assertFalse(
                build_index.commit_state_and_index(state_path, output_path, state)
            )
            self.assertEqual(before, output_path.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
