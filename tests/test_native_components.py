import hashlib
import json
from pathlib import Path

import pytest

from mewcode.sandbox.native_components import (
    HELPER_FILENAME,
    MANIFEST_FILENAME,
    NativeComponentError,
    NativeComponentResolver,
)
from mewcode.sandbox.windows_protocol import PROTOCOL_VERSION


def _fake_pe(machine: int = 0x8664) -> bytes:
    data = bytearray(256)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (128).to_bytes(4, "little")
    data[128:132] = b"PE\0\0"
    data[132:134] = machine.to_bytes(2, "little")
    return bytes(data)


def _component(root: Path, *, development: bool, machine: int = 0x8664, **changes):
    root.mkdir(parents=True)
    helper = root / HELPER_FILENAME
    helper.write_bytes(_fake_pe(machine))
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "app_version": "0.1.0",
        "helper_version": "0.1.0",
        "target": "windows-x86_64",
        "sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        "development": development,
    }
    manifest.update(changes)
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    return helper


def _resolver(tmp_path: Path, monkeypatch, *, packaged=False):
    package = tmp_path / "package"
    development = tmp_path / "development"
    root = package if packaged else development
    helper = _component(root, development=not packaged)
    resolver = NativeComponentResolver(
        package_root=package,
        development_root=development,
        app_version="0.1.0",
    )
    monkeypatch.setattr(
        resolver, "_probe_version", lambda _: (PROTOCOL_VERSION, "0.1.0")
    )
    return resolver, helper, root


@pytest.mark.parametrize("packaged", [False, True])
def test_resolver_accepts_only_matching_fixed_source(tmp_path, monkeypatch, packaged):
    resolver, helper, _ = _resolver(tmp_path, monkeypatch, packaged=packaged)

    assert resolver.resolve_windows_helper() == helper.resolve()


def test_resolver_rejects_tampered_helper(tmp_path, monkeypatch):
    resolver, helper, _ = _resolver(tmp_path, monkeypatch)
    helper.write_bytes(helper.read_bytes() + b"tampered")

    with pytest.raises(NativeComponentError) as error:
        resolver.resolve_windows_helper()

    assert error.value.code == "component_integrity_failed"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"protocol_version": PROTOCOL_VERSION + 1}, "component_mismatch"),
        ({"app_version": "9.0.0"}, "component_mismatch"),
        ({"target": "windows-arm64"}, "component_mismatch"),
        ({"development": False}, "component_mismatch"),
    ],
)
def test_resolver_rejects_manifest_mismatch(tmp_path, monkeypatch, changes, code):
    resolver, _, root = _resolver(tmp_path, monkeypatch)
    manifest_path = root / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(changes)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(NativeComponentError) as error:
        resolver.resolve_windows_helper()

    assert error.value.code == code


def test_resolver_rejects_wrong_pe_architecture(tmp_path, monkeypatch):
    package = tmp_path / "package"
    development = tmp_path / "development"
    _component(development, development=True, machine=0xAA64)
    resolver = NativeComponentResolver(
        package_root=package,
        development_root=development,
        app_version="0.1.0",
    )
    monkeypatch.setattr(
        resolver, "_probe_version", lambda _: (PROTOCOL_VERSION, "0.1.0")
    )

    with pytest.raises(NativeComponentError) as error:
        resolver.resolve_windows_helper()

    assert error.value.code == "component_mismatch"


def test_resolver_rejects_version_probe_mismatch(tmp_path, monkeypatch):
    resolver, _, _ = _resolver(tmp_path, monkeypatch)
    monkeypatch.setattr(
        resolver, "_probe_version", lambda _: (PROTOCOL_VERSION, "other")
    )

    with pytest.raises(NativeComponentError) as error:
        resolver.resolve_windows_helper()

    assert error.value.code == "component_mismatch"


def test_missing_component_has_actionable_diagnostic(tmp_path):
    resolver = NativeComponentResolver(
        package_root=tmp_path / "package",
        development_root=tmp_path / "development",
    )

    diagnostic = resolver.diagnose_windows_helper()

    assert diagnostic.code == "component_missing"
    assert "build_windows_helper.ps1" in diagnostic.remediation
