#![cfg(windows)]

use mewcode_windows_sandbox::state::{
    SandboxSetupState, StatePaths, derive_install_id, load_state, write_state_atomic,
};
use mewcode_windows_sandbox::user_sid::UserSid;
use std::path::PathBuf;

fn test_paths(label: &str, install_id: &str) -> StatePaths {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("state-tests")
        .join(format!("{}-{}-{}", label, std::process::id(), install_id));
    StatePaths::under(base, install_id)
}

fn prepared(owner: &UserSid) -> SandboxSetupState {
    SandboxSetupState::prepared(owner, "S-1-15-2-12345".to_owned()).unwrap()
}

#[test]
fn install_id_is_deterministic_and_user_specific() {
    assert_eq!(
        derive_install_id("S-1-5-21-1"),
        derive_install_id("S-1-5-21-1")
    );
    assert_ne!(
        derive_install_id("S-1-5-21-1"),
        derive_install_id("S-1-5-21-2")
    );
}

#[test]
fn atomically_writes_and_replaces_state() {
    let owner = UserSid::current().unwrap();
    let state = prepared(&owner);
    let paths = test_paths("replace", &state.install_id);
    write_state_atomic(&paths, &owner, &state).unwrap();
    assert_eq!(load_state(&paths, &owner).unwrap(), state);

    let mut replacement = state.clone();
    replacement.appcontainer_sid = "S-1-15-2-67890".to_owned();
    write_state_atomic(&paths, &owner, &replacement).unwrap();
    assert_eq!(load_state(&paths, &owner).unwrap(), replacement);
    assert_eq!(
        std::fs::read_dir(&paths.root)
            .unwrap()
            .filter_map(Result::ok)
            .count(),
        1
    );
}

#[test]
fn rejects_unknown_schema_owner_and_corruption() {
    let owner = UserSid::current().unwrap();
    let state = prepared(&owner);

    let unknown = test_paths("schema", &state.install_id);
    write_state_atomic(&unknown, &owner, &state).unwrap();
    let content = std::fs::read_to_string(&unknown.state_file).unwrap();
    std::fs::write(
        &unknown.state_file,
        content.replace("\"schema_version\":1", "\"schema_version\":99"),
    )
    .unwrap();
    assert!(
        load_state(&unknown, &owner)
            .unwrap_err()
            .to_string()
            .contains("schema")
    );

    let mismatch = test_paths("owner", &state.install_id);
    write_state_atomic(&mismatch, &owner, &state).unwrap();
    let content = std::fs::read_to_string(&mismatch.state_file).unwrap();
    std::fs::write(
        &mismatch.state_file,
        content.replace(&state.owner_user_sid, "S-1-5-21-0"),
    )
    .unwrap();
    assert!(
        load_state(&mismatch, &owner)
            .unwrap_err()
            .to_string()
            .contains("所有者")
    );

    let corrupt = test_paths("corrupt", &state.install_id);
    write_state_atomic(&corrupt, &owner, &state).unwrap();
    std::fs::write(&corrupt.state_file, b"{broken").unwrap();
    assert!(
        load_state(&corrupt, &owner)
            .unwrap_err()
            .to_string()
            .contains("损坏")
    );
}
