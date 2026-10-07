# meridian-releases

Fake sample of a release-manifest repo, modelled on how Horizon records releases.
Each file in `releases/` lists, for one release candidate, the exact tag and commit
of every component repository. A GitHub Action pushes those commits into the
matching Ketryx version, so the version always reads the code the manifest names.

## Manifest format

One TOML file per release candidate, e.g. `releases/meridian-2.0.0-26100701.toml`:

```toml
release_version = "2.0.0-26100701"
release_type = "RC2"

[components.meridian-os]
"version.tag" = "v1.177.6"
"version.sha" = "1ffbb976a34488d85e42110a16fd6c4b59c1d794"
"repository.provider" = "github"
"repository.org" = "kevinblm-ketryx"
"repository.name" = "meridian-os"
```

The keys are quoted on purpose: `"version.tag"` is one key, not a nested table.

## What the Action does

`.github/workflows/report-to-ketryx.yml` runs when a manifest under `releases/` is
added or changed on `main`. For each such manifest, `scripts/report_to_ketryx.py`:

1. Finds the Ketryx version. A version named exactly like `release_version` wins.
   Otherwise it is the version whose version number equals the numeric core of
   `release_version`, so `2.0.0-26100701` matches a version with number 2.0.0. A
   codename like "Bode" works if its version settings give it that explicit
   version number.
2. Checks each component's `version.sha` against its `version.tag` with
   `git ls-remote`, and fails if they disagree.
3. Sends one `PATCH /api/v1/projects/{project}/versions/{version}` with
   `repositorySettings`, setting each connected repo's per-version ref to its
   commit SHA. Components whose repo isn't connected to the Ketryx project are
   listed as skipped.

Ketryx resolves a 40-character SHA ref directly, without consulting the
repo-wide release ref pattern (`refs/tags/v#`) or the version number. Released
versions are rejected by Ketryx, so the Action can only move unreleased ones.

The job summary links to the Ketryx version's settings page and shows one row per
component.

Run it by hand from the Actions tab (*Report release to Ketryx → Run workflow*)
to re-report a manifest, target a specific version ID, or do a dry run.

## Setup

| Name | Kind | Value |
|---|---|---|
| `KETRYX_URL` | variable | `https://demo.ketryx.com` |
| `KETRYX_PROJECT_ID` | variable | `KXPRJ01M1EV9Y61EQ9V4NYZ7WFSR3WV` |
| `KETRYX_API_KEY` | secret | Org API key with *List versions*, *Get repositories* and *Manage version settings* |

The component repos must already be connected in Ketryx project settings.

## Tests

```
python3.12 -m unittest discover -s tests
```
