#![cfg(windows)]

use mewcode_windows_sandbox::diagnostics::{HelperDiagnostic, diagnose_at};
use mewcode_windows_sandbox::state::{StatePaths, derive_install_id};
use mewcode_windows_sandbox::user_sid::UserSid;
use std::path::PathBuf;

fn paths(label: &str, owner: &UserSid) -> StatePaths {
    let install_id = derive_install_id(&owner.to_sid_string().unwrap());
    StatePaths::under(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("diagnostic-tests")
            .join(format!("{label}-{}", std::process::id())),
        &install_id,
    )
}

#[test]
fn missing_state_requires_explicit_setup() {
    let owner = UserSid::current().unwrap();
    assert_eq!(
        diagnose_at(&owner, &paths("missing", &owner)),
        HelperDiagnostic::setup_required()
    );
}

#[test]
fn corrupt_state_is_broken() {
    let owner = UserSid::current().unwrap();
    let paths = paths("corrupt", &owner);
    std::fs::create_dir_all(&paths.root).unwrap();
    std::fs::write(&paths.state_file, b"{broken").unwrap();
    let diagnostic = diagnose_at(&owner, &paths);
    assert_eq!(diagnostic.state, "broken");
    assert_eq!(diagnostic.code, "setup_failed");
}
