"""Tests for release and repository policy tooling."""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools._project import (
    REPOSITORY_PLACEHOLDER,
    changelog_section,
    package_version,
    project_version,
)
from tools.check_workflows import external_action_reference, validate_workflow
from tools.configure_repository import configure
from tools.install_smoke import resolve_wheel
from tools.rebuild_from_sdist import _extract_safely, resolve_distributions
from tools.release_gate import ReleaseGateError, validate_release
from tools.validate_dist import (
    DistributionValidationError,
    _forbidden,
    compare_archives,
    write_checksums,
)
from tools.validate_repository import validate_repository


class ProjectMetadataTests(unittest.TestCase):
    def test_versions_and_changelog_are_consistent(self) -> None:
        version = project_version()
        self.assertEqual(package_version(), version)
        self.assertTrue(changelog_section(version).strip())

    def test_repository_configuration_replaces_every_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pyproject.toml"
            path.write_text(
                f'Repository = "{REPOSITORY_PLACEHOLDER}"\n'
                f'Issues = "{REPOSITORY_PLACEHOLDER}/issues"\n',
                encoding="utf-8",
            )
            self.assertTrue(configure("example/filecommit", path))
            configured = path.read_text(encoding="utf-8")
            self.assertNotIn("OWNER/REPOSITORY", configured)
            self.assertEqual(configured.count("https://github.com/example/filecommit"), 2)
            self.assertFalse(configure("example/filecommit", path))

    def test_repository_configuration_rejects_ambiguous_names(self) -> None:
        with self.assertRaises(ValueError):
            configure("not a repository")

    def test_release_gate_rejects_nonrelease_tag_before_other_checks(self) -> None:
        with self.assertRaises(ReleaseGateError):
            validate_release("latest")

    def test_repository_policy_enforces_build_pin_through_python_39_matrix(self) -> None:
        # The build requirement's Requires-Python metadata is not available
        # offline.  validate_repository checks the exact pin and the CI/release
        # Python 3.9 build matrices together.
        validate_repository(allow_generated=True)


class WorkflowPolicyTests(unittest.TestCase):
    def test_action_reference_requires_full_sha(self) -> None:
        full_sha = "a" * 40
        self.assertEqual(
            external_action_reference(f"actions/checkout@{full_sha}"),
            ("actions/checkout", full_sha),
        )
        self.assertIsNone(external_action_reference("./.github/actions/local"))

    def test_workflow_policy_accepts_least_privilege_and_pinned_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci.yml"
            path.write_text(
                "name: test\n"
                "on: [push]\n"
                "permissions:\n"
                "  contents: read\n"
                "jobs:\n"
                "  test:\n"
                "    runs-on: ubuntu-latest\n"
                "    timeout-minutes: 5\n"
                "    steps:\n"
                f"      - uses: actions/checkout@{'a' * 40}\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_workflow(path), [])

    def test_workflow_policy_rejects_mutable_action_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci.yml"
            path.write_text(
                "name: test\n"
                "on: [push]\n"
                "permissions:\n"
                "  contents: read\n"
                "jobs:\n"
                "  test:\n"
                "    runs-on: ubuntu-latest\n"
                "    timeout-minutes: 5\n"
                "    steps:\n"
                "      - uses: actions/checkout@v7\n",
                encoding="utf-8",
            )
            errors = validate_workflow(path)
            self.assertTrue(any("full lowercase SHA" in error for error in errors))


class DistributionToolTests(unittest.TestCase):
    def test_forbidden_archive_entries(self) -> None:
        self.assertTrue(_forbidden("package/__pycache__/module.pyc"))
        self.assertTrue(_forbidden("package/.github/workflows/release.yml"))
        self.assertFalse(_forbidden("package/src/filecommit/_core.py"))

    def test_checksum_output_is_sorted_and_correct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            second = root / "b.whl"
            first = root / "a.tar.gz"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            destination = root / "SHA256SUMS.txt"
            write_checksums((second, first), destination)
            lines = destination.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], f"{hashlib.sha256(b'first').hexdigest()}  a.tar.gz")
            self.assertEqual(lines[1], f"{hashlib.sha256(b'second').hexdigest()}  b.whl")

    def test_archive_comparison_ignores_zip_container_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.whl"
            second = root / "second.whl"
            archives = (
                (first, (2020, 1, 1, 0, 0, 0)),
                (second, (2021, 2, 2, 0, 0, 0)),
            )
            for path, timestamp in archives:
                with zipfile.ZipFile(path, "w") as archive:
                    info = zipfile.ZipInfo("module.py", timestamp)
                    archive.writestr(info, b"same")
            compare_archives(first, second)

    def test_archive_comparison_reports_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.whl"
            second = root / "second.whl"
            with zipfile.ZipFile(first, "w") as archive:
                archive.writestr("module.py", b"first")
            with zipfile.ZipFile(second, "w") as archive:
                archive.writestr("module.py", b"second")
            with self.assertRaises(DistributionValidationError):
                compare_archives(first, second)

    def test_distribution_resolution_requires_exactly_one_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / "package.whl"
            sdist = root / "package.tar.gz"
            wheel.touch()
            sdist.touch()
            self.assertEqual(resolve_distributions(root), (sdist, wheel))
            self.assertEqual(resolve_wheel(root), wheel)

    def test_safe_extraction_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "unsafe.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                data = b"unsafe"
                member = tarfile.TarInfo("../outside.txt")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            with tarfile.open(archive_path, "r:gz") as archive:
                with self.assertRaises(RuntimeError):
                    _extract_safely(archive, root / "extract")


if __name__ == "__main__":
    unittest.main()
