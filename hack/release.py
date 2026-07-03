#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "click>=8.1.8",
#   "gitpython>=3.1.44",
#   "jinja2>=3.1.5",
#   "packaging>=24.2",
#   "pygithub>=2.6.0",
# ]
# ///

from __future__ import annotations

import re
import tempfile
import unittest
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Any

import click
from git import Repo
from git.exc import GitCommandError
from github import Auth, Github
from jinja2 import Environment
from packaging.version import Version


RELEASE_NOTES_TEMPLATE = """# {{ release_tag }}

Open vSwitch base: `{{ base_tag }}`
Pinned OVS commit: `{{ ovs_commit }}` (`{{ describe }}`)
Local patch count: {{ local_patches | length }}

Version calculation: {{ upstream_commit_count }} upstream commit(s) after {{ base_tag }} + {{ local_patches | length }} local patch(es) = {{ release_number }}

## Upstream Base

- Open vSwitch {{ base_tag }}

## Upstream Commits After {{ base_tag }}

{% if upstream_commits -%}
{% for commit in upstream_commits -%}
- {{ commit }}
{% endfor -%}
{% else -%}
- None
{% endif %}

## Local Patches

{% if local_patches -%}
{% for patch in local_patches -%}
- {{ patch.name }}: {{ patch.subject }}
{% endfor -%}
{% else -%}
- None
{% endif %}
"""


@dataclass(frozen=True)
class GitDescribe:
    raw: str
    base_tag: str
    upstream_commit_count: int


@dataclass(frozen=True)
class LocalPatch:
    name: str
    subject: str


def dockerfile_path(root: Path) -> Path:
    return root / "Dockerfile"


def patch_series_path(root: Path) -> Path:
    return root / "patches" / "series"


def ovs_source_path(root: Path) -> Path:
    return root / "ovs"


def repo_root() -> Path:
    repo = Repo(Path.cwd(), search_parent_directories=True)
    return Path(repo.working_tree_dir or Path.cwd()).resolve()


def find_ovs_source(root: Path) -> Path:
    source = ovs_source_path(root)
    if not (source / ".git").exists():
        raise FileNotFoundError(f"Open vSwitch source is not checked out at {source}")
    return source


def parse_ovs_commit(dockerfile: Path) -> str:
    for line in dockerfile.read_text().splitlines():
        match = re.match(r"^ARG\s+OVS_COMMIT=(\S+)", line)
        if match:
            return match.group(1)
    raise ValueError(f"could not find ARG OVS_COMMIT=... in {dockerfile}")


def patch_names(series: Path) -> list[str]:
    if not series.exists():
        raise FileNotFoundError(f"missing patch series: {series}")

    names: list[str] = []
    for line in series.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.append(stripped.split()[0])
    return names


def patch_subject(patch_file: Path) -> str:
    for line in patch_file.read_text(errors="replace").splitlines():
        if line.startswith("Subject: "):
            subject = line.removeprefix("Subject: ")
            return re.sub(r"^\[[^]]+\]\s*", "", subject)
    return patch_file.name


def parse_git_describe(describe: str) -> GitDescribe:
    match = re.match(r"^(?P<base_tag>.+)-(?P<count>\d+)-g[0-9a-f]+$", describe)
    if not match:
        raise ValueError(f"could not parse git describe output: {describe}")
    return GitDescribe(
        raw=describe,
        base_tag=match.group("base_tag"),
        upstream_commit_count=int(match.group("count")),
    )


def describe_ovs_commit(ovs_repo: Repo, ovs_commit: str) -> GitDescribe:
    try:
        ovs_repo.commit(ovs_commit)
    except (ValueError, GitCommandError) as exc:
        raise ValueError(
            f"OVS commit {ovs_commit} is not available in {ovs_repo.working_tree_dir}; update ./ovs"
        ) from exc

    describe = ovs_repo.git.describe("--tags", "--long", "--match", "v[0-9]*", ovs_commit)
    return parse_git_describe(describe)


def release_tag(base_tag: str, upstream_commit_count: int, local_patch_count: int) -> str:
    base_version = Version(base_tag.removeprefix("v"))
    release_number = upstream_commit_count + local_patch_count
    return f"v{base_version}-{release_number}"


def upstream_commits(ovs_repo: Repo, base_tag: str, ovs_commit: str) -> list[str]:
    log = ovs_repo.git.log(
        "--no-merges",
        "--reverse",
        "--format=%h %s",
        f"{base_tag}..{ovs_commit}",
    )
    return log.splitlines() if log else []


def local_patches(repo: Path, patches: list[str]) -> list[LocalPatch]:
    items: list[LocalPatch] = []
    for patch in patches:
        patch_file = repo / "patches" / patch
        if not patch_file.exists():
            raise FileNotFoundError(f"patch listed in series does not exist: {patch}")
        items.append(LocalPatch(name=patch, subject=patch_subject(patch_file)))
    return items


def render_release_notes(
    *,
    release_tag_value: str,
    ovs_commit: str,
    describe: GitDescribe,
    commits: list[str],
    patches: list[LocalPatch],
) -> str:
    release_number = describe.upstream_commit_count + len(patches)
    template = Environment(keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True).from_string(
        RELEASE_NOTES_TEMPLATE
    )
    return template.render(
        release_tag=release_tag_value,
        base_tag=describe.base_tag,
        ovs_commit=ovs_commit,
        describe=describe.raw,
        upstream_commit_count=describe.upstream_commit_count,
        release_number=release_number,
        upstream_commits=commits,
        local_patches=patches,
    )


def release_tag_from_notes(notes: str) -> str:
    first_line = notes.splitlines()[0]
    if not first_line.startswith("# "):
        raise ValueError("release notes did not start with a markdown heading")
    return first_line.removeprefix("# ")


def sync_draft_release(repo: Any, release_tag_value: str, target: str, notes: str, announce: bool = True) -> None:
    release = next((item for item in repo.get_releases() if item.tag_name == release_tag_value), None)
    if release is not None:
        if not release.draft:
            if announce:
                click.echo(f"Release {release_tag_value} is already published", err=True)
            return
        release.update_release(
            name=release_tag_value,
            message=notes,
            draft=True,
            prerelease=False,
            tag_name=release_tag_value,
            target_commitish=target,
        )
        if announce:
            click.echo(f"Updated draft release {release_tag_value}", err=True)
        return

    repo.create_git_release(
        tag=release_tag_value,
        name=release_tag_value,
        message=notes,
        draft=True,
        prerelease=False,
        target_commitish=target,
    )
    if announce:
        click.echo(f"Created draft release {release_tag_value}", err=True)


def create_or_update_draft(
    *,
    repository: str,
    token: str,
    release_tag_value: str,
    target: str,
    notes: str,
) -> None:
    github = Github(auth=Auth.Token(token))
    sync_draft_release(github.get_repo(repository), release_tag_value, target, notes)


class ReleaseHelperTests(unittest.TestCase):
    def test_parse_ovs_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = Path(tmpdir) / "Dockerfile"
            dockerfile.write_text("ARG FROM=debian\nARG OVS_COMMIT=abc123\n")
            self.assertEqual(parse_ovs_commit(dockerfile), "abc123")

    def test_patch_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            series = Path(tmpdir) / "series"
            series.write_text("\n# ignored\nfoo.patch\nbar.patch -p1\n")
            self.assertEqual(patch_names(series), ["foo.patch", "bar.patch"])

    def test_patch_subject(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            patch = Path(tmpdir) / "change.patch"
            patch.write_text("From: Test\nSubject: [PATCH] ovs: tune something\n")
            self.assertEqual(patch_subject(patch), "ovs: tune something")

    def test_parse_git_describe(self) -> None:
        describe = parse_git_describe("v3.3.9-28-g6f247db42")
        self.assertEqual(describe.base_tag, "v3.3.9")
        self.assertEqual(describe.upstream_commit_count, 28)

    def test_release_tag_uses_packaging_version_for_base(self) -> None:
        self.assertEqual(release_tag("v3.3.9", 28, 2), "v3.3.9-30")

    def test_render_release_notes(self) -> None:
        notes = render_release_notes(
            release_tag_value="v3.3.9-30",
            ovs_commit="abc123",
            describe=GitDescribe("v3.3.9-28-gabc123", "v3.3.9", 28),
            commits=["abc123 Fix thing"],
            patches=[LocalPatch("local.patch", "Local fix")],
        )
        self.assertIn("# v3.3.9-30", notes)
        self.assertIn("- abc123 Fix thing", notes)
        self.assertIn("- local.patch: Local fix", notes)

    def test_sync_draft_release_updates_existing_draft(self) -> None:
        release = FakeRelease("v1.0.0-1", draft=True)
        repo = FakeRepository([release])
        sync_draft_release(repo, "v1.0.0-1", "abc123", "notes", announce=False)
        self.assertEqual(release.updated["message"], "notes")
        self.assertFalse(repo.created)

    def test_sync_draft_release_skips_published_release(self) -> None:
        repo = FakeRepository([FakeRelease("v1.0.0-1", draft=False)])
        sync_draft_release(repo, "v1.0.0-1", "abc123", "notes", announce=False)
        self.assertFalse(repo.created)

    def test_sync_draft_release_creates_new_draft(self) -> None:
        repo = FakeRepository([])
        sync_draft_release(repo, "v1.0.0-1", "abc123", "notes", announce=False)
        self.assertEqual(repo.created["tag"], "v1.0.0-1")
        self.assertTrue(repo.created["draft"])


class FakeRelease:
    def __init__(self, tag_name: str, draft: bool) -> None:
        self.tag_name = tag_name
        self.draft = draft
        self.updated: dict[str, Any] = {}

    def update_release(self, **kwargs: Any) -> None:
        self.updated = kwargs


class FakeRepository:
    def __init__(self, releases: list[FakeRelease]) -> None:
        self.releases = releases
        self.created: dict[str, Any] = {}

    def get_releases(self) -> list[FakeRelease]:
        return self.releases

    def create_git_release(self, **kwargs: Any) -> None:
        self.created = kwargs


def run_self_tests() -> None:
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseHelperTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--self-test", is_flag=True, help="run the built-in unit tests")
def main(self_test: bool) -> None:
    if self_test:
        run_self_tests()
        return

    root = repo_root()
    dockerfile = dockerfile_path(root)
    series = patch_series_path(root)
    target = Repo(root).head.commit.hexsha

    if not dockerfile.exists():
        raise FileNotFoundError(f"missing Dockerfile: {dockerfile}")

    ovs_commit = parse_ovs_commit(dockerfile)
    ovs_repo = Repo(find_ovs_source(root))

    describe = describe_ovs_commit(ovs_repo, ovs_commit)
    patches = local_patches(root, patch_names(series))
    tag = release_tag(describe.base_tag, describe.upstream_commit_count, len(patches))
    notes = render_release_notes(
        release_tag_value=tag,
        ovs_commit=ovs_commit,
        describe=describe,
        commits=upstream_commits(ovs_repo, describe.base_tag, ovs_commit),
        patches=patches,
    )

    release_tag_from_notes(notes)

    github_repository = environ.get("GITHUB_REPOSITORY")
    github_token = environ.get("GITHUB_TOKEN") or environ.get("GH_TOKEN")
    if not github_repository:
        raise ValueError("GITHUB_REPOSITORY is required")
    if not github_token:
        raise ValueError("GITHUB_TOKEN or GH_TOKEN is required")

    create_or_update_draft(
        repository=github_repository,
        token=github_token,
        release_tag_value=tag,
        target=target,
        notes=notes,
    )


if __name__ == "__main__":
    main()
