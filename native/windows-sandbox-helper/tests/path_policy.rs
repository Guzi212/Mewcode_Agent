#![cfg(windows)]

use mewcode_windows_sandbox::paths::{
    PathKind, PathPolicyError, paths_equal, resolve_local_ntfs, verify_unchanged,
};
use std::path::{Path, PathBuf};

fn fixture() -> PathBuf {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("path tests")
        .join(std::process::id().to_string());
    std::fs::create_dir_all(&root).unwrap();
    root
}

#[test]
fn resolves_files_directories_spaces_and_identity() {
    let root = fixture();
    let file = root.join("中文 file.txt");
    std::fs::write(&file, b"content").unwrap();

    let directory = resolve_local_ntfs(&root).unwrap();
    assert_eq!(directory.kind, PathKind::Directory);
    let resolved_file = resolve_local_ntfs(&file).unwrap();
    assert_eq!(resolved_file.kind, PathKind::File);
    verify_unchanged(&resolved_file).unwrap();
    assert!(paths_equal(
        &resolved_file.canonical_path,
        Path::new(
            &resolved_file
                .canonical_path
                .to_string_lossy()
                .to_uppercase()
        )
    ));
}

#[test]
fn rejects_relative_unc_missing_and_device_paths() {
    for path in [
        PathBuf::from("relative\\file.txt"),
        PathBuf::from(r"\\server\share\file.txt"),
        PathBuf::from(r"\\?\D:\device-path.txt"),
        fixture().join("missing.txt"),
    ] {
        assert!(
            resolve_local_ntfs(&path).is_err(),
            "unexpectedly accepted {path:?}"
        );
    }
}

#[test]
fn detects_target_replacement() {
    let root = fixture();
    let file = root.join("replace.txt");
    std::fs::write(&file, b"first").unwrap();
    let resolved = resolve_local_ntfs(&file).unwrap();
    std::fs::remove_file(&file).unwrap();
    std::fs::write(&file, b"second").unwrap();
    assert_eq!(verify_unchanged(&resolved), Err(PathPolicyError::Changed));
}

#[test]
#[ignore = "会在 Rust target 中创建并移除 NTFS junction"]
fn rejects_reparse_points() {
    let root = fixture();
    let target = root.join("junction-target");
    let link = root.join("junction-link");
    std::fs::create_dir_all(&target).unwrap();
    let system_root = std::env::var_os("SystemRoot").unwrap_or_else(|| r"C:\Windows".into());
    let output =
        std::process::Command::new(PathBuf::from(system_root).join("System32").join("cmd.exe"))
            .args(["/D", "/C", "mklink", "/J"])
            .arg(&link)
            .arg(&target)
            .output()
            .unwrap();
    assert!(
        output.status.success(),
        "mklink /J failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        resolve_local_ntfs(&link),
        Err(PathPolicyError::ReparsePoint)
    );
    std::fs::remove_dir(&link).unwrap();
}
