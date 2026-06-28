#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_NAME = "github-star-index-sync"
REQUIRED_FILES = (
    "SKILL.md",
    "scripts/build_index.py",
    "scripts/install.py",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install GitHub Star Index Sync for Codex and Claude Code."
    )
    parser.add_argument(
        "--home",
        default=str(Path.home()),
        help="Target home directory; primarily useful for testing",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace a different existing canonical installation after backing it up",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned actions without changing files",
    )
    return parser.parse_args(argv)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def verify_package(path: Path) -> None:
    missing = [relative for relative in REQUIRED_FILES if not (path / relative).is_file()]
    if missing:
        raise RuntimeError(f"Skill 包缺少文件: {', '.join(missing)}")


def copy_package(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{SKILL_NAME}.install-", dir=destination.parent)
    )
    try:
        shutil.rmtree(temporary)
        shutil.copytree(
            source,
            temporary,
            ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc"),
            symlinks=True,
        )
        verify_package(temporary)
        os.replace(temporary, destination)
    except Exception:
        remove_path(temporary)
        raise


def run_package_tests(package: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(package / "scripts" / "build_index.py")],
        check=True,
    )
    tests = package / "tests"
    if tests.is_dir():
        subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-q"],
            check=True,
        )


def make_relative_link(entry: Path, target: Path) -> None:
    entry.parent.mkdir(parents=True, exist_ok=True)
    relative_target = os.path.relpath(target, entry.parent)
    entry.symlink_to(relative_target, target_is_directory=True)


def install(args: argparse.Namespace) -> int:
    source = Path(__file__).resolve().parents[1]
    home = Path(args.home).expanduser().resolve()
    canonical = home / ".agents" / "skills" / SKILL_NAME
    entries = [
        home / ".codex" / "skills" / SKILL_NAME,
        home / ".claude" / "skills" / SKILL_NAME,
    ]
    verify_package(source)

    print(f"[PLAN] 来源: {source}")
    print(f"[PLAN] 共享目录: {canonical}")
    for entry in entries:
        print(f"[PLAN] 入口: {entry} -> {canonical}")
    if args.dry_run:
        return 0

    created_canonical = False
    canonical_backup: Path | None = None
    entry_backups: list[tuple[Path, Path]] = []
    created_links: list[Path] = []
    try:
        source_is_canonical = source == canonical or (
            canonical.exists() and source.samefile(canonical)
        )
        if not source_is_canonical:
            if canonical.exists() or canonical.is_symlink():
                if not args.replace:
                    raise FileExistsError(
                        f"共享目录已存在且不是当前来源: {canonical}。"
                        "确认内容后可使用 --replace。"
                    )
                canonical_backup = canonical.with_name(f".{SKILL_NAME}.canonical-backup")
                if canonical_backup.exists() or canonical_backup.is_symlink():
                    raise FileExistsError(f"安装备份路径已存在: {canonical_backup}")
                canonical.rename(canonical_backup)
            copy_package(source, canonical)
            created_canonical = True

        verify_package(canonical)
        for entry in entries:
            if entry.is_symlink() and entry.resolve() == canonical:
                continue
            if entry.exists() or entry.is_symlink():
                entry_resolves_source = (
                    not entry.is_symlink()
                    and entry.resolve() == source
                    and source != canonical
                )
                if not entry_resolves_source and not args.replace:
                    raise FileExistsError(
                        f"入口已存在且未指向共享目录: {entry}。"
                        "确认内容后可使用 --replace。"
                    )
                backup = entry.with_name(f".{SKILL_NAME}.entry-backup")
                if backup.exists() or backup.is_symlink():
                    raise FileExistsError(f"入口备份路径已存在: {backup}")
                entry.rename(backup)
                entry_backups.append((entry, backup))
            make_relative_link(entry, canonical)
            created_links.append(entry)

        for entry in entries:
            if not entry.is_symlink() or entry.resolve() != canonical:
                raise RuntimeError(f"入口验证失败: {entry}")
        run_package_tests(canonical)

        for _, backup in entry_backups:
            remove_path(backup)
        if canonical_backup:
            remove_path(canonical_backup)
    except Exception:
        for link in reversed(created_links):
            if link.is_symlink():
                link.unlink()
        for entry, backup in reversed(entry_backups):
            if backup.exists() or backup.is_symlink():
                backup.rename(entry)
        if created_canonical and (canonical.exists() or canonical.is_symlink()):
            remove_path(canonical)
        if canonical_backup and (canonical_backup.exists() or canonical_backup.is_symlink()):
            canonical_backup.rename(canonical)
        raise

    print(f"[OK] 共享 skill: {canonical}")
    print(f"[OK] Codex 入口: {entries[0]}")
    print(f"[OK] Claude Code 入口: {entries[1]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return install(parse_args(argv))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
