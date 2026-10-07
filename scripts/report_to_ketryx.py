#!/usr/bin/env python3
"""Push the component commits in a release manifest to the matching Ketryx version.

For every component in a releases/*.toml manifest, this sets the Ketryx version's
per-repository ref (Version settings > Repositories) to the component's
"version.sha". Ketryx then reads that exact commit for the version, instead of
resolving the repository-wide release ref pattern.

The Ketryx version is the one named exactly like the manifest's release_version,
or else the one whose version number equals its numeric core
(release_version "2.0.0-26100701" -> version number 2.0.0). Pass --version-id to
target a version directly.

Environment: KETRYX_URL, KETRYX_PROJECT_ID, KETRYX_API_KEY. The API key needs
the "List versions", "Get repositories" and "Manage version settings" scopes.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request

HOSTS = {"github": "github.com", "gitlab": "gitlab.com", "bitbucket": "bitbucket.org"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReportError(Exception):
    pass


def core_version(value):
    """'1.2.0-26091002' -> '1.2.0', '3.0' -> '3.0.0', 'Bode' -> None."""
    m = re.match(r"\d+(?:\.\d+)*", value or "")
    if not m:
        return None
    parts = m.group(0).split(".")
    parts += ["0"] * (3 - len(parts))
    return ".".join(str(int(p)) for p in parts)


def normalize_url(url):
    url = url.strip().lower().rstrip("/")
    return url[:-4] if url.endswith(".git") else url


def resolve_version(versions, release_version):
    exact = [v for v in versions if v["name"] == release_version]
    if len(exact) == 1:
        return exact[0]
    core = core_version(release_version)
    matches = [v for v in versions if core and core_version(v.get("versionNumber")) == core]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ReportError(
            f"No Ketryx version is named {release_version!r} or has version number {core}. "
            "Create it in Ketryx, or rerun with an explicit version ID."
        )
    names = ", ".join(sorted(v["name"] for v in matches))
    raise ReportError(
        f"Several Ketryx versions have version number {core}: {names}. "
        "Rerun with an explicit version ID."
    )


def components(manifest):
    out = []
    for name, c in manifest.get("components", {}).items():
        provider = c.get("repository.provider")
        host = HOSTS.get(provider)
        url = f"https://{host}/{c.get('repository.org')}/{c.get('repository.name')}" if host else None
        out.append(
            {
                "name": name,
                "provider": provider,
                "url": url,
                "tag": c.get("version.tag"),
                "sha": str(c.get("version.sha", "")).strip().lower(),
            }
        )
    return out


def plan(comps, connected_urls):
    """Returns (repositorySettings for the PATCH body, one summary row per component)."""
    connected = {normalize_url(u): u for u in connected_urls}
    bad = [c["name"] for c in comps if c["url"] and not SHA_RE.match(c["sha"])]
    if bad:
        raise ReportError(
            "; ".join(f"{n}: version.sha is missing or not a full 40-character SHA" for n in bad)
        )
    settings, rows = [], []
    for c in comps:
        row = dict(c)
        if not c["url"]:
            row["status"] = f"skipped: unsupported provider {c['provider']!r}"
        elif normalize_url(c["url"]) not in connected:
            row["status"] = "skipped: not connected in Ketryx"
        else:
            ketryx_url = connected[normalize_url(c["url"])]
            settings.append({"url": ketryx_url, "releaseRefPattern": c["sha"]})
            row["status"] = "set"
        rows.append(row)
    return settings, rows


def peeled_sha(ls_remote_output, tag):
    """Commit a tag points at, from `git ls-remote` output (handles annotated tags)."""
    refs = dict(
        reversed(line.split("\t", 1)) for line in ls_remote_output.splitlines() if "\t" in line
    )
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


def check_tag(url, tag, sha):
    """Fails if the tag exists and points somewhere other than the manifest's SHA."""
    if not tag:
        return "no tag in manifest"
    try:
        out = subprocess.run(
            ["git", "ls-remote", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
            capture_output=True, text=True, timeout=30, check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "not checked (repo not readable from CI)"
    actual = peeled_sha(out, tag)
    if actual is None:
        raise ReportError(f"{url}: tag {tag} does not exist")
    if actual != sha:
        raise ReportError(f"{url}: tag {tag} points at {actual}, but the manifest says {sha}")
    return "matches tag"


class Ketryx:
    def __init__(self, base_url, project_id, api_key):
        self.base = base_url.rstrip("/") + f"/api/v1/projects/{project_id}"
        self.web = base_url.rstrip("/") + f"/projects/{project_id}"
        self.key = api_key

    def call(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise ReportError(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:500]}")


def report(ketryx, manifest_path, version_id=None, dry_run=False, check_tags=True):
    with open(manifest_path, "rb") as f:
        manifest = tomllib.load(f)
    release_version = manifest.get("release_version")
    if not release_version:
        raise ReportError(f"{manifest_path}: no release_version")

    versions = ketryx.call("GET", "/versions")["versions"]
    if version_id:
        version = next((v for v in versions if v["id"] == version_id), None)
        if not version:
            raise ReportError(f"Version {version_id} not found in the project")
    else:
        version = resolve_version(versions, release_version)

    repos = ketryx.call("GET", "/repositories")["repositories"]
    settings, rows = plan(components(manifest), [r["url"] for r in repos])
    if not settings:
        raise ReportError("None of the manifest's components are connected to the Ketryx project")
    for row in rows:
        row["check"] = check_tag(row["url"], row["tag"], row["sha"]) if row["status"] == "set" and check_tags else ""

    if not dry_run:
        ketryx.call("PATCH", f"/versions/{version['id']}", {"repositorySettings": settings})

    link = f"{ketryx.web}/versions/{version['id']}/settings"
    lines = [
        f"### {os.path.basename(manifest_path)} → Ketryx version [{version['name']}]({link})"
        + (" (dry run, nothing written)" if dry_run else ""),
        "",
        f"release_version `{release_version}` · release_type `{manifest.get('release_type', '')}`",
        "",
        "| Component | Tag | Commit | Ketryx | Tag check |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | `{row['tag']}` | `{row['sha'][:12]}` | {row['status']} | {row['check']} |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifests", nargs="+", help="release manifest TOML files, applied in sorted order")
    parser.add_argument("--version-id", help="Ketryx version ID (KXVSN...) to update instead of matching")
    parser.add_argument("--dry-run", action="store_true", help="resolve and print, but do not write to Ketryx")
    parser.add_argument("--no-tag-check", action="store_true", help="skip comparing version.sha to the tag")
    args = parser.parse_args()

    missing = [k for k in ("KETRYX_URL", "KETRYX_PROJECT_ID", "KETRYX_API_KEY") if not os.environ.get(k)]
    if missing:
        print(f"::error::Not set: {', '.join(missing)} (repo variables and the KETRYX_API_KEY secret)")
        sys.exit(1)
    ketryx = Ketryx(
        os.environ["KETRYX_URL"], os.environ["KETRYX_PROJECT_ID"], os.environ["KETRYX_API_KEY"]
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    failed = False
    for path in sorted(args.manifests):
        try:
            text = report(ketryx, path, args.version_id, args.dry_run, not args.no_tag_check)
        except (ReportError, OSError, tomllib.TOMLDecodeError) as e:
            failed = True
            text = f"### {os.path.basename(path)}: not reported\n\n{e}\n"
            print(f"::error file={path}::{e}")
        print(text)
        if summary:
            with open(summary, "a") as f:
                f.write(text + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
