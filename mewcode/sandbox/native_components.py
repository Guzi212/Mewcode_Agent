"""Windows 原生组件的受控发现、完整性和版本校验。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess

from mewcode import __version__

from .models import SandboxDiagnostic, SandboxState
from .windows_protocol import MAX_MESSAGE_BYTES, PROTOCOL_VERSION

HELPER_FILENAME = "mewcode-windows-sandbox.exe"
MANIFEST_FILENAME = "manifest.json"
TARGET = "windows-x86_64"


class NativeComponentError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NativeComponentManifest:
    protocol_version: int
    app_version: str
    helper_version: str
    target: str
    sha256: str
    development: bool = False

    @classmethod
    def load(cls, path: Path) -> "NativeComponentManifest":
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise NativeComponentError("component_missing", "未找到 Windows Helper manifest") from exc
        if len(data) > 64 * 1024:
            raise NativeComponentError("component_mismatch", "Windows Helper manifest 过大")
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativeComponentError("component_mismatch", "Windows Helper manifest 无效") from exc
        expected = {
            "protocol_version",
            "app_version",
            "helper_version",
            "target",
            "sha256",
            "development",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise NativeComponentError("component_mismatch", "Windows Helper manifest 字段不匹配")
        if isinstance(raw["protocol_version"], bool) or not isinstance(raw["protocol_version"], int):
            raise NativeComponentError("component_mismatch", "manifest 协议版本无效")
        string_fields = ("app_version", "helper_version", "target", "sha256")
        if any(not isinstance(raw[field], str) or not raw[field] for field in string_fields):
            raise NativeComponentError("component_mismatch", "manifest 字符串字段无效")
        if not isinstance(raw["development"], bool):
            raise NativeComponentError("component_mismatch", "manifest development 字段无效")
        digest = raw["sha256"].lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise NativeComponentError("component_mismatch", "manifest SHA-256 无效")
        return cls(
            raw["protocol_version"],
            raw["app_version"],
            raw["helper_version"],
            raw["target"],
            digest,
            raw["development"],
        )


class NativeComponentResolver:
    def __init__(
        self,
        *,
        package_root: Path | None = None,
        development_root: Path | None = None,
        app_version: str = __version__,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        self.package_root = package_root or (
            Path(__file__).resolve().parents[1] / "native" / TARGET
        )
        self.development_root = development_root or (
            repository_root / "native" / "windows-sandbox-helper" / "target" / "release"
        )
        self.app_version = app_version

    def resolve_windows_helper(self) -> Path:
        for root, expected_development in (
            (self.package_root, False),
            (self.development_root, True),
        ):
            manifest_path = root / MANIFEST_FILENAME
            helper_path = root / HELPER_FILENAME
            if not manifest_path.exists() and not helper_path.exists():
                continue
            return self._validate_candidate(root, expected_development)
        raise NativeComponentError("component_missing", "未找到 Windows 原生沙箱组件")

    def diagnose_windows_helper(self) -> SandboxDiagnostic:
        try:
            helper = self.resolve_windows_helper()
            manifest = NativeComponentManifest.load(helper.parent / MANIFEST_FILENAME)
        except NativeComponentError as exc:
            remediation = (
                "运行 scripts/build_windows_helper.ps1 构建受信组件"
                if exc.code == "component_missing"
                else "重新构建或安装与当前 MewCode 版本匹配的 Windows 组件"
            )
            return SandboxDiagnostic(
                SandboxState.BROKEN,
                "windows-appcontainer",
                exc.code,
                str(exc),
                remediation,
            )
        return SandboxDiagnostic(
            SandboxState.READY,
            "windows-appcontainer",
            "ready",
            "Windows 原生沙箱组件完整性校验通过",
            component_version=manifest.helper_version,
        )

    def _validate_candidate(self, root: Path, expected_development: bool) -> Path:
        try:
            canonical_root = root.resolve(strict=True)
            manifest_path = (root / MANIFEST_FILENAME).resolve(strict=True)
            helper_path = (root / HELPER_FILENAME).resolve(strict=True)
        except OSError as exc:
            raise NativeComponentError("component_missing", "Windows Helper 文件不完整") from exc
        if manifest_path.parent != canonical_root or helper_path.parent != canonical_root:
            raise NativeComponentError("component_integrity_failed", "Windows Helper 路径越出受信目录")
        manifest = NativeComponentManifest.load(manifest_path)
        if manifest.development is not expected_development:
            raise NativeComponentError("component_mismatch", "Windows Helper 开发标识与来源不一致")
        if manifest.protocol_version != PROTOCOL_VERSION:
            raise NativeComponentError("component_mismatch", "Windows Helper 协议版本不匹配")
        if manifest.app_version != self.app_version:
            raise NativeComponentError("component_mismatch", "Windows Helper 应用版本不匹配")
        if manifest.target != TARGET:
            raise NativeComponentError("component_mismatch", "Windows Helper 目标架构不匹配")
        self._validate_pe_x86_64(helper_path)
        if self._sha256(helper_path) != manifest.sha256:
            raise NativeComponentError("component_integrity_failed", "Windows Helper SHA-256 校验失败")
        reported_protocol, reported_version = self._probe_version(helper_path)
        if reported_protocol != PROTOCOL_VERSION or reported_version != manifest.helper_version:
            raise NativeComponentError("component_mismatch", "Windows Helper 自报版本不匹配")
        return helper_path

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise NativeComponentError("component_integrity_failed", "无法读取 Windows Helper") from exc
        return digest.hexdigest()

    @staticmethod
    def _validate_pe_x86_64(path: Path) -> None:
        try:
            with path.open("rb") as stream:
                header = stream.read(64)
                if len(header) < 64 or header[:2] != b"MZ":
                    raise NativeComponentError("component_mismatch", "Windows Helper 不是有效 PE 文件")
                pe_offset = int.from_bytes(header[0x3C:0x40], "little")
                if pe_offset < 64 or pe_offset > 16 * 1024 * 1024:
                    raise NativeComponentError("component_mismatch", "Windows Helper PE 头偏移无效")
                stream.seek(pe_offset)
                pe_header = stream.read(6)
        except OSError as exc:
            raise NativeComponentError("component_integrity_failed", "无法读取 Windows Helper PE 头") from exc
        if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
            raise NativeComponentError("component_mismatch", "Windows Helper PE 签名无效")
        if int.from_bytes(pe_header[4:6], "little") != 0x8664:
            raise NativeComponentError("component_mismatch", "Windows Helper 不是 x86-64 架构")

    @staticmethod
    def _probe_version(path: Path) -> tuple[int, str]:
        request = json.dumps(
            {"protocol_version": PROTOCOL_VERSION, "operation": "version"},
            separators=(",", ":"),
        )
        try:
            completed = subprocess.run(
                [str(path)],
                input=request + "\n",
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise NativeComponentError("component_mismatch", "Windows Helper 版本探测失败") from exc
        stdout = completed.stdout.encode("utf-8")
        if completed.returncode != 0 or len(stdout) > MAX_MESSAGE_BYTES or stdout.count(b"\n") != 1:
            raise NativeComponentError("component_mismatch", "Windows Helper 版本响应无效")
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise NativeComponentError("component_mismatch", "Windows Helper 版本响应无效") from exc
        expected = {"protocol_version", "helper_version"}
        if not isinstance(raw, dict) or set(raw) != expected:
            raise NativeComponentError("component_mismatch", "Windows Helper 版本字段无效")
        protocol = raw["protocol_version"]
        version = raw["helper_version"]
        if isinstance(protocol, bool) or not isinstance(protocol, int) or not isinstance(version, str):
            raise NativeComponentError("component_mismatch", "Windows Helper 版本类型无效")
        return protocol, version
