import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import report_to_ketryx as r  # noqa: E402

SHA_A = "a" * 40
SHA_B = "b" * 40


def manifest(**components):
    return {"release_version": "2.0.0-26100101", "components": components}


def component(name, sha=SHA_A, tag="v1.0.0", provider="github", org="acme"):
    return {
        "version.tag": tag,
        "version.sha": sha,
        "repository.provider": provider,
        "repository.org": org,
        "repository.name": name,
    }


class CoreVersionTest(unittest.TestCase):
    def test_strips_build_suffix(self):
        self.assertEqual(r.core_version("1.2.0-26091002"), "1.2.0")

    def test_pads_short_versions(self):
        self.assertEqual(r.core_version("3.0"), "3.0.0")

    def test_keeps_build_metadata_out(self):
        self.assertEqual(r.core_version("1.1.0-26060501+jur"), "1.1.0")

    def test_non_numeric_is_none(self):
        self.assertIsNone(r.core_version("Bode"))


class ResolveVersionTest(unittest.TestCase):
    versions = [
        {"id": "V1", "name": "Bode", "versionNumber": "2.0.0"},
        {"id": "V2", "name": "1.2.0", "versionNumber": "1.2.0"},
        {"id": "V3", "name": "Start to Sub", "versionNumber": None},
        {"id": "V4", "name": "3.0", "versionNumber": "3.0.0"},
        {"id": "V5", "name": "3.0.0", "versionNumber": "3.0.0"},
    ]

    def test_matches_codename_by_version_number(self):
        self.assertEqual(r.resolve_version(self.versions, "2.0.0-26100101")["id"], "V1")

    def test_exact_name_wins(self):
        self.assertEqual(r.resolve_version(self.versions, "3.0.0")["id"], "V5")

    def test_ambiguous_number_fails(self):
        with self.assertRaisesRegex(r.ReportError, "3.0, 3.0.0"):
            r.resolve_version(self.versions, "3.0.0-26100101")

    def test_no_match_fails(self):
        with self.assertRaisesRegex(r.ReportError, "No Ketryx version"):
            r.resolve_version(self.versions, "9.9.9-1")


class PlanTest(unittest.TestCase):
    connected = ["https://github.com/Acme/os.git", "https://gitlab.com/other/thing"]

    def test_uses_connected_url_spelling_and_sha(self):
        settings, rows = r.plan(r.components(manifest(os=component("os"))), self.connected)
        self.assertEqual(
            settings, [{"url": "https://github.com/Acme/os.git", "releaseRefPattern": SHA_A}]
        )
        self.assertEqual(rows[0]["status"], "set")

    def test_skips_unconnected_and_unknown_provider(self):
        m = manifest(
            apps=component("apps"),
            agent=component("agent", provider="perforce"),
        )
        settings, rows = r.plan(r.components(m), self.connected)
        self.assertEqual(settings, [])
        self.assertEqual(
            sorted(row["status"] for row in rows),
            ["skipped: not connected in Ketryx", "skipped: unsupported provider 'perforce'"],
        )

    def test_bad_sha_fails_whole_manifest(self):
        with self.assertRaisesRegex(r.ReportError, "os: version.sha"):
            r.plan(r.components(manifest(os=component("os", sha="abc123"))), self.connected)

    def test_sha_is_lowercased(self):
        settings, _ = r.plan(
            r.components(manifest(os=component("os", sha=SHA_B.upper()))), self.connected
        )
        self.assertEqual(settings[0]["releaseRefPattern"], SHA_B)


class PeeledShaTest(unittest.TestCase):
    def test_prefers_peeled_commit_for_annotated_tags(self):
        out = f"{SHA_A}\trefs/tags/v1.0.0\n{SHA_B}\trefs/tags/v1.0.0^{{}}\n"
        self.assertEqual(r.peeled_sha(out, "v1.0.0"), SHA_B)

    def test_lightweight_tag(self):
        self.assertEqual(r.peeled_sha(f"{SHA_A}\trefs/tags/v1.0.0\n", "v1.0.0"), SHA_A)

    def test_missing_tag(self):
        self.assertIsNone(r.peeled_sha("", "v1.0.0"))


if __name__ == "__main__":
    unittest.main()
