#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

KEYCHAIN_SERVICE = "github-star-index-token"
GITHUB_API_BASE = "https://api.github.com"
SCHEMA_VERSION = 2
CONFIG_SCHEMA_VERSION = 1
TIMEZONE = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
CONFIG_ENV = "GITHUB_STAR_INDEX_CONFIG"
DEFAULT_STATE = ".github-star-index/state.json"
DEFAULT_PENDING = ".github-star-index/pending.json"
DEFAULT_SOURCE = "github-star"
DEFAULT_OUTPUT = "GitHub Star 索引.md"
PURPOSE_LIMIT = 50
AUDIENCE_LIMIT = 20
MAX_WORKERS = 6
REQUEST_RETRIES = 2

PRIORITY_HEADINGS = {
    "about",
    "overview",
    "introduction",
    "what is",
    "what it does",
    "features",
    "key features",
    "capabilities",
    "why",
}
STOP_HEADINGS = {
    "installation",
    "install",
    "getting started",
    "quick start",
    "usage",
    "configuration",
    "development",
    "contributing",
    "license",
    "sponsors",
    "support",
    "changelog",
    "roadmap",
}


@dataclass
class Project:
    note_path: Path
    relative_note_path: str
    title: str
    url: str
    local_description: str
    tags: list[str]
    language: str
    stars: int
    owner: str
    repo: str

    @property
    def key(self) -> str:
        return f"{self.owner}/{self.repo}".lower()

    @property
    def display_repo(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def note_link(self) -> str:
        return f"[[{self.relative_note_path}|打开笔记]]"


class GitHubClient:
    def __init__(self, token: str, retries: int = REQUEST_RETRIES):
        self.token = token
        self.retries = retries

    def request(self, url: str, accept: str, etag: str = "") -> tuple[int, bytes, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-star-index-sync",
        }
        if etag:
            headers["If-None-Match"] = etag

        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.status, response.read(), response.headers.get("ETag", "")
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    return 304, b"", exc.headers.get("ETag", etag)
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise
            except (TimeoutError, urllib.error.URLError):
                if attempt >= self.retries:
                    raise
            time.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"GitHub 请求失败: {url}")

    def get_repo(self, owner: str, repo: str, etag: str = "") -> tuple[int, dict, str]:
        status, data, response_etag = self.request(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
            "application/vnd.github+json",
            etag,
        )
        payload = json.loads(data.decode("utf-8")) if status != 304 else {}
        return status, payload, response_etag

    def get_readme(self, owner: str, repo: str, etag: str = "") -> tuple[int, str, str]:
        status, data, response_etag = self.request(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/readme",
            "application/vnd.github.raw+json",
            etag,
        )
        text = data.decode("utf-8", errors="ignore") if status != 304 else ""
        return status, text, response_etag


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    commands = {"configure", "doctor", "prepare", "finalize"}
    if not raw or raw[0] not in commands:
        raw.insert(0, "prepare")

    parser = argparse.ArgumentParser(description="Incrementally build a GitHub star index.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--vault", help="Obsidian vault root path")
        subparser.add_argument("--source", help="Source note directory")
        subparser.add_argument("--output", help="Output markdown file")
        subparser.add_argument("--state", help="Persistent state file")
        subparser.add_argument("--pending", help="Prepared work file")

    configure_parser = subparsers.add_parser(
        "configure", help="Save the default vault and index settings"
    )
    configure_parser.add_argument("--vault", required=True, help="Obsidian vault root path")
    configure_parser.add_argument("--source", default=DEFAULT_SOURCE, help="Source note directory")
    configure_parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output markdown file")
    configure_parser.add_argument("--state", default=DEFAULT_STATE, help="Persistent state file")
    configure_parser.add_argument("--pending", default=DEFAULT_PENDING, help="Prepared work file")

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check installation, configuration, vault, and credentials"
    )
    add_common(doctor_parser)

    prepare_parser = subparsers.add_parser("prepare", help="Fetch sources and prepare changed items")
    add_common(prepare_parser)
    prepare_parser.add_argument(
        "--regroup",
        action="store_true",
        help="Allow rebuilding categories and reclassifying all projects",
    )
    prepare_parser.add_argument(
        "--refresh",
        action="append",
        default=[],
        metavar="OWNER/REPO",
        help="Force summary refresh for one repository; may be repeated",
    )

    finalize_parser = subparsers.add_parser("finalize", help="Validate updates and render the index")
    add_common(finalize_parser)
    finalize_parser.add_argument("--updates", required=True, help="Codex-generated update JSON")
    return parser.parse_args(raw)


def get_config_path() -> Path:
    explicit = os.environ.get(CONFIG_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "github-star-index-sync" / "config.json"


def load_config(required: bool = False) -> dict[str, Any]:
    path = get_config_path()
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"尚未配置默认 vault。请先运行 `configure --vault <路径>`；配置文件: {path}"
            )
        return {}
    config = load_json(path)
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"配置文件版本不兼容: {config.get('schema_version')}，"
            f"需要 {CONFIG_SCHEMA_VERSION}"
        )
    return config


def apply_runtime_config(args: argparse.Namespace) -> argparse.Namespace:
    config = load_config(required=not bool(args.vault))
    vault_value = args.vault or config.get("vault")
    if not vault_value:
        raise ValueError("缺少 vault 路径，请传入 --vault 或先运行 configure。")
    args.vault = str(Path(str(vault_value)).expanduser().resolve())
    args.source = args.source or config.get("source") or DEFAULT_SOURCE
    args.output = args.output or config.get("output") or DEFAULT_OUTPUT
    args.state = args.state or config.get("state") or DEFAULT_STATE
    args.pending = args.pending or config.get("pending") or DEFAULT_PENDING
    return args


def configure(args: argparse.Namespace) -> int:
    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"vault 目录不存在: {vault}")
    source_path = resolve_path(vault, args.source)
    if not source_path.is_dir():
        raise FileNotFoundError(f"源目录不存在: {source_path}")
    config = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "vault": str(vault),
        "source": args.source,
        "output": args.output,
        "state": args.state,
        "pending": args.pending,
    }
    path = get_config_path()
    atomic_write_json(path, config)
    print(f"[OK] 已保存配置: {path}")
    print(f"[OK] 默认 vault: {vault}")
    return 0


def resolve_path(vault: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else vault / path


def keychain_has_token() -> bool:
    if platform.system() != "Darwin" or not shutil.which("security"):
        return False
    user = os.environ.get("USER", "").strip()
    if not user:
        return False
    result = subprocess.run(
        ["security", "find-generic-password", "-a", user, "-s", KEYCHAIN_SERVICE],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def get_token_source() -> str:
    if os.environ.get("GITHUB_TOKEN", "").strip():
        return "GITHUB_TOKEN 环境变量"
    if keychain_has_token():
        return "macOS Keychain"
    return "未配置"


def get_token() -> str:
    env_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if env_token:
        return env_token

    if platform.system() != "Darwin" or not shutil.which("security"):
        raise RuntimeError(
            "未找到 GitHub Token。非 macOS 环境请设置 GITHUB_TOKEN 环境变量。"
        )
    user = os.environ.get("USER", "").strip()
    if not user:
        raise RuntimeError("GITHUB_TOKEN 未设置，且无法确定当前用户以读取 macOS Keychain。")

    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            user,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    raise RuntimeError(
        "未找到 GitHub Token。请设置 GITHUB_TOKEN，或在 macOS Keychain 中保存 "
        f"`{KEYCHAIN_SERVICE}`。"
    )


def doctor(args: argparse.Namespace) -> int:
    args = apply_runtime_config(args)
    vault = Path(args.vault)
    source_path = resolve_path(vault, args.source)
    state_path = resolve_path(vault, args.state)
    config_path = get_config_path()
    skill_path = Path(__file__).resolve().parents[1]

    print(f"[OK] Python: {platform.python_version()}")
    print(f"[OK] 系统: {platform.system()} {platform.machine()}")
    print(f"[OK] Skill: {skill_path}")
    print(f"[OK] 配置文件: {config_path}")
    print(f"[{'OK' if vault.is_dir() else 'ERROR'}] Vault: {vault}")
    if source_path.is_dir():
        note_count = len(list(source_path.glob("*.md")))
        print(f"[OK] 源目录: {source_path} ({note_count} 个笔记)")
    else:
        print(f"[ERROR] 源目录不存在: {source_path}")
    if state_path.exists():
        state = load_state(state_path)
        print(
            f"[OK] 状态文件: {state_path} "
            f"({len(state['projects'])} 个项目, {len(state['categories'])} 个分类)"
        )
    else:
        print(f"[WARN] 状态文件尚未生成: {state_path}")
    token_source = get_token_source()
    level = "OK" if token_source != "未配置" else "ERROR"
    print(f"[{level}] GitHub Token: {token_source}")
    if platform.system() == "Darwin":
        print("[INFO] Claude Code 若无法访问 vault，请运行 `/add-dir <vault路径>`。")
    return 0 if vault.is_dir() and source_path.is_dir() and token_source != "未配置" else 1


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
            return parsed if isinstance(parsed, list) else value
        except (SyntaxError, ValueError):
            return [part.strip(" '\"") for part in value[1:-1].split(",") if part.strip()]
    return value.strip("'\"")


def parse_frontmatter(note_path: Path) -> dict[str, Any]:
    content = note_path.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.DOTALL)
    if not match:
        raise ValueError(f"{note_path} 缺少 YAML frontmatter。")

    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in match.group(1).splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s+-\s+", raw_line) and current_key:
            if not isinstance(data.get(current_key), list):
                data[current_key] = []
            data[current_key].append(re.sub(r"^\s+-\s+", "", raw_line).strip(" '\""))
            continue
        if ":" not in raw_line:
            current_key = None
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        if not key:
            current_key = None
            continue
        if value.strip():
            data[key] = parse_scalar(value)
            current_key = None
        else:
            data[key] = []
            current_key = key
    return data


def parse_repo_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() not in {"github.com", "www.github.com"} or len(parts) < 2:
        raise ValueError(f"无法从 URL 解析 GitHub 仓库: {url}")
    return parts[0], parts[1].removesuffix(".git")


def parse_int(value: Any) -> int:
    try:
        return int(str(value or "0").replace(",", ""))
    except ValueError:
        return 0


def load_projects(vault: Path, source_dir: str) -> list[Project]:
    source_path = vault / source_dir
    if not source_path.is_dir():
        raise FileNotFoundError(f"源目录不存在: {source_path}")

    projects: list[Project] = []
    seen: dict[str, Path] = {}
    for note_path in sorted(source_path.glob("*.md")):
        data = parse_frontmatter(note_path)
        url = str(data.get("url", "")).strip()
        owner, repo = parse_repo_url(url)
        aliases = data.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        project = Project(
            note_path=note_path,
            relative_note_path=str(note_path.relative_to(vault)).replace("\\", "/"),
            title=str(aliases[0]).strip() if aliases else repo,
            url=url.rstrip("/"),
            local_description=str(data.get("description", "")).strip(),
            tags=[str(tag).strip() for tag in tags if str(tag).strip()],
            language=str(data.get("language", "")).strip(),
            stars=parse_int(data.get("stars")),
            owner=owner,
            repo=repo,
        )
        if project.key in seen:
            raise ValueError(
                f"发现重复仓库 {project.display_repo}: {seen[project.key]} 与 {note_path}"
            )
        seen[project.key] = note_path
        projects.append(project)
    return projects


def normalize_markdown_line(line: str) -> str:
    line = html.unescape(line)
    line = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"<(?:img|picture|source)\b[^>]*>", " ", line, flags=re.IGNORECASE)
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", line)
    line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "- ", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def clean_readme(readme_text: str) -> tuple[str, str]:
    if not readme_text.strip():
        return "", ""

    intro: list[str] = []
    priority: list[str] = []
    all_meaningful: list[str] = []
    current_heading = ""
    in_code = False

    for raw_line in readme_text.splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        if "shields.io" in line or re.search(r"\[!\[.*?\]\(", line):
            continue
        if line.startswith(("![", "<img", "<picture", "<source", "<!--")):
            continue
        if line.startswith("#"):
            heading_level = len(line) - len(line.lstrip("#"))
            current_heading = (
                "" if heading_level == 1 else normalize_markdown_line(line.lstrip("#")).lower()
            )
            continue

        normalized = normalize_markdown_line(line)
        if not normalized or len(normalized) < 8:
            continue
        if re.match(r"^(?:npm|pip|brew|cargo|docker|git)\s+(?:install|clone|run)", normalized):
            continue
        all_meaningful.append(normalized)
        heading_root = re.sub(r"[^a-z0-9 ]+", " ", current_heading).strip()
        if any(heading_root.startswith(value) for value in STOP_HEADINGS):
            continue
        if any(heading_root.startswith(value) for value in PRIORITY_HEADINGS):
            priority.append(normalized)
        elif not current_heading and len(intro) < 12:
            intro.append(normalized)

    selected: list[str] = []
    for line in intro + priority:
        if line not in selected:
            selected.append(line)
        if sum(len(item) for item in selected) >= 3000:
            break
    if not selected:
        selected = all_meaningful[:15]

    evidence = "\n".join(selected)
    digest_source = "\n".join(all_meaningful[:500])
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest() if digest_source else ""
    return evidence[:4000], digest


def fetch_readme_fallback(owner: str, repo: str, default_branch: str) -> str:
    branches = [branch for branch in [default_branch, "main", "master"] if branch]
    for branch in dict.fromkeys(branches):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "github-star-index-sync"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
    return ""


def source_fingerprint(source: dict[str, Any]) -> str:
    payload = {
        "repo_description": source.get("repo_description", ""),
        "topics": sorted(source.get("topics", [])),
        "readme_digest": source.get("readme_digest", ""),
        "local_description": source.get("local_description", ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_project(
    project: Project,
    previous: dict[str, Any],
    client: GitHubClient,
) -> tuple[str, dict[str, Any], list[str]]:
    warnings: list[str] = []
    old_source = previous.get("source", {}) if previous else {}
    repo_description = old_source.get("repo_description", "")
    topics = list(old_source.get("topics", []))
    default_branch = old_source.get("default_branch", "")
    repo_etag = old_source.get("repo_etag", "")
    readme_etag = old_source.get("readme_etag", "")
    readme_evidence = old_source.get("readme_evidence", "")
    readme_digest = old_source.get("readme_digest", "")

    try:
        status, repo_data, repo_etag = client.get_repo(project.owner, project.repo, repo_etag)
        if status != 304:
            repo_description = str(repo_data.get("description") or "").strip()
            topics = [str(topic).strip() for topic in repo_data.get("topics") or []]
            default_branch = str(repo_data.get("default_branch") or "").strip()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{project.display_repo}: 仓库元数据获取失败 ({exc})")

    try:
        status, readme_text, readme_etag = client.get_readme(
            project.owner, project.repo, readme_etag
        )
        if status != 304:
            readme_evidence, readme_digest = clean_readme(readme_text)
    except Exception as exc:  # noqa: BLE001
        fallback = fetch_readme_fallback(project.owner, project.repo, default_branch)
        if fallback:
            readme_evidence, readme_digest = clean_readme(fallback)
            warnings.append(f"{project.display_repo}: API README 失败，已使用 raw README ({exc})")
        elif readme_evidence:
            warnings.append(f"{project.display_repo}: README 获取失败，已使用缓存 ({exc})")
        else:
            warnings.append(f"{project.display_repo}: README 获取失败 ({exc})")

    if readme_evidence:
        source_basis = "readme"
    elif repo_description:
        source_basis = "repo-description"
    elif project.local_description and project.local_description.lower() != "no description":
        source_basis = "local-description"
    else:
        source_basis = "insufficient"

    source = {
        "repo_description": repo_description,
        "topics": topics,
        "default_branch": default_branch,
        "repo_etag": repo_etag,
        "readme_etag": readme_etag,
        "readme_evidence": readme_evidence,
        "readme_digest": readme_digest,
        "local_description": project.local_description,
        "source_basis": source_basis,
    }
    source["fingerprint"] = source_fingerprint(source)
    return project.key, source, warnings


def now_string() -> str:
    return dt.datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": "",
        "categories": [],
        "projects": {},
    }


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    state = load_json(path)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"状态文件版本不兼容: {state.get('schema_version')}，需要 {SCHEMA_VERSION}"
        )
    state.setdefault("categories", [])
    state.setdefault("projects", {})
    state.setdefault("updated_at", "")
    return state


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    atomic_write_text(path, content)


def project_record(project: Project, source: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    record = dict(previous)
    record.update(
        {
            "key": project.key,
            "repo": project.display_repo,
            "title": project.title,
            "url": project.url,
            "note_path": project.relative_note_path,
            "language": project.language,
            "stars": project.stars,
            "source": source,
        }
    )
    return record


def project_for_pending(record: dict[str, Any], locked_category: bool) -> dict[str, Any]:
    source = record["source"]
    return {
        "key": record["key"],
        "repo": record["repo"],
        "title": record["title"],
        "url": record["url"],
        "note_path": record["note_path"],
        "language": record.get("language", ""),
        "topics": source.get("topics", []),
        "repo_description": source.get("repo_description", ""),
        "local_description": source.get("local_description", ""),
        "readme_evidence": source.get("readme_evidence", ""),
        "source_basis": source.get("source_basis", "insufficient"),
        "existing_category_id": record.get("category_id", ""),
        "category_locked": locked_category,
    }


def render_markdown(state: dict[str, Any]) -> str:
    projects = state.get("projects", {})
    categories = sorted(state.get("categories", []), key=lambda item: item["order"])
    grouped: dict[str, list[dict[str, Any]]] = {item["id"]: [] for item in categories}
    for record in projects.values():
        grouped.setdefault(record["category_id"], []).append(record)

    lines = [
        "# GitHub Star 索引",
        "",
        "> 这是自动生成页。请运行 GitHub Star Index Sync skill 重建"
        "（Codex：`$github-star-index-sync`；Claude Code：`/github-star-index-sync`）。",
        f"> 更新时间：{state.get('updated_at') or now_string()}",
        f"> 项目总数：{len(projects)}",
        f"> 分类总数：{sum(1 for category in categories if grouped.get(category['id']))}",
        "",
    ]
    nonempty = [category for category in categories if grouped.get(category["id"])]
    for category_index, category in enumerate(nonempty):
        items = sorted(
            grouped[category["id"]],
            key=lambda item: (item["title"].casefold(), item["repo"].casefold()),
        )
        lines.append(f"## {category['name']}（{len(items)}）")
        lines.append("")
        for index, record in enumerate(items):
            lines.extend(
                [
                    f"### {record['title']}",
                    "",
                    f"- **Obsidian：** [[{record['note_path']}|打开笔记]]",
                    f"- **GitHub：** [{record['repo']}]({record['url']})",
                    f"- **项目用途：** {record['purpose']}",
                    f"- **适合用户：** {record['audience']}",
                    "",
                ]
            )
            if index < len(items) - 1:
                lines.extend(["---", ""])
        if category_index < len(nonempty) - 1:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def meaningful_index_changed(output_path: Path, state: dict[str, Any]) -> bool:
    if not output_path.exists():
        return True
    existing = output_path.read_text(encoding="utf-8")
    candidate = render_markdown(state)
    timestamp_pattern = r"^> 更新时间：.*$"
    return re.sub(timestamp_pattern, "", existing, flags=re.MULTILINE) != re.sub(
        timestamp_pattern, "", candidate, flags=re.MULTILINE
    )


def prune_categories(state: dict[str, Any]) -> None:
    used = {record.get("category_id") for record in state["projects"].values()}
    categories = [category for category in state["categories"] if category["id"] in used]
    categories.sort(key=lambda item: item["order"])
    for index, category in enumerate(categories):
        category["order"] = index
    state["categories"] = categories


def commit_state_and_index(state_path: Path, output_path: Path, state: dict[str, Any]) -> bool:
    changed = meaningful_index_changed(output_path, state)
    if changed:
        state["updated_at"] = now_string()
    atomic_write_json(state_path, state)
    if changed:
        atomic_write_text(output_path, render_markdown(state))
    return changed


def prepare(args: argparse.Namespace) -> int:
    vault = Path(args.vault).expanduser().resolve()
    state_path = resolve_path(vault, args.state or DEFAULT_STATE)
    pending_path = resolve_path(vault, args.pending or DEFAULT_PENDING)
    output_path = resolve_path(vault, args.output or DEFAULT_OUTPUT)
    source_dir = args.source or DEFAULT_SOURCE
    state = load_state(state_path)
    projects = load_projects(vault, source_dir)
    old_projects = state.get("projects", {})
    token = get_token()
    client = GitHubClient(token)
    warnings: list[str] = []
    fetched: dict[str, dict[str, Any]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_project, project, old_projects.get(project.key, {}), client): project
            for project in projects
        }
        for future in concurrent.futures.as_completed(futures):
            key, source, project_warnings = future.result()
            fetched[key] = source
            warnings.extend(project_warnings)

    candidate = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": state.get("updated_at", ""),
        "categories": [dict(category) for category in state.get("categories", [])],
        "projects": {},
    }
    pending_items: list[dict[str, Any]] = []
    refresh_keys = {value.lower() for value in args.refresh}
    known_keys = {project.key for project in projects}
    unknown_refresh = sorted(refresh_keys - known_keys)
    if unknown_refresh:
        raise ValueError(f"--refresh 未匹配到仓库: {', '.join(unknown_refresh)}")

    for project in projects:
        previous = old_projects.get(project.key, {})
        record = project_record(project, fetched[project.key], previous)
        candidate["projects"][project.key] = record
        changed = (
            not previous.get("purpose")
            or not previous.get("audience")
            or previous.get("source", {}).get("fingerprint")
            != record["source"].get("fingerprint")
            or project.key in refresh_keys
            or args.regroup
        )
        if changed:
            locked = bool(previous.get("category_id")) and not args.regroup
            pending_items.append(project_for_pending(record, locked))

    if not pending_items:
        prune_categories(candidate)
        changed = commit_state_and_index(state_path, output_path, candidate)
        if pending_path.exists():
            pending_path.unlink()
        print(f"[OK] 无待更新项目；索引{'已更新' if changed else '未发生变化'}: {output_path}")
        print(f"[OK] 项目总数: {len(candidate['projects'])}")
        for warning in sorted(warnings):
            print(f"[WARN] {warning}")
        return 0

    pending_payload = {
        "schema_version": SCHEMA_VERSION,
        "mode": "regroup" if args.regroup else "incremental",
        "vault": str(vault),
        "source": source_dir,
        "output": str(output_path),
        "state_path": str(state_path),
        "created_at": now_string(),
        "categories": candidate["categories"],
        "projects": pending_items,
        "candidate_state": candidate,
        "warnings": sorted(warnings),
    }
    atomic_write_json(pending_path, pending_payload)
    print(f"[PENDING] 需要 AI 助手归纳 {len(pending_items)} 个项目: {pending_path}")
    print("[PENDING] 生成 updates.json 后运行 finalize；旧索引未被修改。")
    for warning in sorted(warnings):
        print(f"[WARN] {warning}")
    return 2


def validate_categories(categories: Any) -> list[dict[str, Any]]:
    if not isinstance(categories, list) or not categories:
        raise ValueError("updates.json 必须提供非空 categories 数组。")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    names: set[str] = set()
    for index, raw in enumerate(categories):
        if not isinstance(raw, dict):
            raise ValueError("每个分类必须是对象。")
        category_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,47}", category_id):
            raise ValueError(f"分类 id 不合法: {category_id}")
        if not name or len(name) > 30:
            raise ValueError(f"分类名称为空或超过30字: {name}")
        if not description or len(description) > 100:
            raise ValueError(f"分类说明为空或超过100字: {name}")
        if category_id in ids or name in names:
            raise ValueError(f"分类 id 或名称重复: {category_id}/{name}")
        ids.add(category_id)
        names.add(name)
        result.append(
            {
                "id": category_id,
                "name": name,
                "description": description,
                "order": index,
            }
        )
    return result


def validate_summary_text(value: Any, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空。")
    if "\n" in text or "\r" in text:
        raise ValueError(f"{label}不能包含换行。")
    if len(text) > limit:
        raise ValueError(f"{label}超过{limit}字: {text}")
    return text


def validate_updates(pending: dict[str, Any], updates: dict[str, Any]) -> tuple[list, dict]:
    categories = validate_categories(updates.get("categories"))
    category_ids = {category["id"] for category in categories}
    raw_projects = updates.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("updates.json 必须提供 projects 数组。")

    expected = {project["key"]: project for project in pending["projects"]}
    submitted: dict[str, dict[str, Any]] = {}
    allowed_basis = {"readme", "repo-description", "local-description", "insufficient"}
    for raw in raw_projects:
        if not isinstance(raw, dict):
            raise ValueError("项目更新必须是对象。")
        key = str(raw.get("key", "")).lower().strip()
        if key not in expected:
            raise ValueError(f"updates.json 包含未知项目: {key}")
        if key in submitted:
            raise ValueError(f"updates.json 项目重复: {key}")
        category_id = str(raw.get("category_id", "")).strip()
        if category_id not in category_ids:
            raise ValueError(f"{key} 使用了不存在的分类: {category_id}")
        if expected[key].get("category_locked") and category_id != expected[key].get(
            "existing_category_id"
        ):
            raise ValueError(f"普通同步不能改变已有项目分类: {key}")
        basis = str(raw.get("source_basis", "")).strip()
        if basis not in allowed_basis:
            raise ValueError(f"{key} 的 source_basis 不合法: {basis}")
        available_sources = {
            name
            for name, field in (
                ("readme", "readme_evidence"),
                ("repo-description", "repo_description"),
                ("local-description", "local_description"),
            )
            if str(expected[key].get(field, "")).strip()
            and str(expected[key].get(field, "")).strip().lower() != "no description"
        }
        if not available_sources:
            available_sources.add(str(expected[key].get("source_basis", "insufficient")))
        if basis not in available_sources:
            raise ValueError(
                f"{key} 的 source_basis 没有对应的可用内容: {basis}"
            )
        purpose = validate_summary_text(raw.get("purpose"), f"{key} 项目用途", PURPOSE_LIMIT)
        if basis == "insufficient" and purpose != "项目说明不足，需人工确认":
            raise ValueError(f"{key} 缺少可靠说明时必须标记为需人工确认。")
        submitted[key] = {
            "category_id": category_id,
            "purpose": purpose,
            "audience": validate_summary_text(
                raw.get("audience"), f"{key} 适合用户", AUDIENCE_LIMIT
            ),
            "source_basis": basis,
        }
    missing = sorted(set(expected) - set(submitted))
    if missing:
        raise ValueError(f"updates.json 缺少项目: {', '.join(missing)}")

    if pending.get("mode") != "regroup":
        old_by_id = {item["id"]: item for item in pending.get("categories", [])}
        new_by_id = {item["id"]: item for item in categories}
        old_order = [item["id"] for item in pending.get("categories", [])]
        retained_order = [item["id"] for item in categories if item["id"] in old_by_id]
        if retained_order != old_order:
            raise ValueError("普通同步不能改变已有分类顺序。")
        for category_id, old in old_by_id.items():
            new = new_by_id.get(category_id)
            if not new:
                raise ValueError(f"普通同步不能删除已有分类: {category_id}")
            if new["name"] != old["name"] or new["description"] != old["description"]:
                raise ValueError(f"普通同步不能修改已有分类: {category_id}")
    return categories, submitted


def finalize(args: argparse.Namespace) -> int:
    vault = Path(args.vault).expanduser().resolve()
    pending_path = resolve_path(vault, args.pending or DEFAULT_PENDING)
    updates_path = Path(args.updates).expanduser().resolve()
    if not pending_path.exists():
        raise FileNotFoundError(f"找不到待处理文件: {pending_path}")
    pending = load_json(pending_path)
    updates = load_json(updates_path)
    if pending.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("待处理文件版本不兼容，请重新运行 prepare。")
    if Path(pending.get("vault", "")).resolve() != vault:
        raise ValueError("待处理文件属于其他 vault。")

    categories, submitted = validate_updates(pending, updates)
    state = pending["candidate_state"]
    state["categories"] = categories
    for key, update in submitted.items():
        record = state["projects"][key]
        record.update(update)
        record["summary_fingerprint"] = record["source"]["fingerprint"]

    incomplete = [
        key
        for key, record in state["projects"].items()
        if not record.get("purpose") or not record.get("audience") or not record.get("category_id")
    ]
    if incomplete:
        raise ValueError(f"状态中仍有未完成项目: {', '.join(sorted(incomplete))}")

    prune_categories(state)
    state_path = resolve_path(vault, args.state or DEFAULT_STATE)
    output_path = resolve_path(vault, args.output or DEFAULT_OUTPUT)
    changed = commit_state_and_index(state_path, output_path, state)
    pending_path.unlink()
    print(f"[OK] 索引{'已更新' if changed else '内容未变化'}: {output_path}")
    print(f"[OK] 状态文件: {state_path}")
    print(f"[OK] 项目总数: {len(state['projects'])}")
    print(f"[OK] 分类总数: {len(state['categories'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "configure":
        return configure(args)
    if args.command == "doctor":
        return doctor(args)
    args = apply_runtime_config(args)
    if args.command == "prepare":
        return prepare(args)
    return finalize(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
