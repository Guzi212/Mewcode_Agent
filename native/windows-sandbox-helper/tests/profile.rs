#![cfg(windows)]

use mewcode_windows_sandbox::profile::{
    ProfileStatus, derive_profile_sid, prepare_profile, query_profile,
};
use mewcode_windows_sandbox::state::{
    SandboxSetupState, StatePaths, derive_install_id, profile_name,
};
use mewcode_windows_sandbox::user_sid::UserSid;
use std::time::{SystemTime, UNIX_EPOCH};

#[test]
fn profile_name_depends_only_on_product_and_user() {
    let owner = UserSid::current().unwrap().to_sid_string().unwrap();
    let install_id = derive_install_id(&owner);
    let first = profile_name(&install_id);
    let second = profile_name(&install_id);
    assert_eq!(first, second);
    assert!(first.len() <= 64);
}

#[test]
fn derives_the_same_sid_without_creating_a_profile() {
    let name = "MewCode.Sandbox.0000000000000000";
    let first = derive_profile_sid(name).unwrap().to_sid_string().unwrap();
    let second = derive_profile_sid(name).unwrap().to_sid_string().unwrap();
    assert_eq!(first, second);
    assert!(first.starts_with("S-1-15-2-"));
}

#[test]
fn distinguishes_missing_matching_and_mismatched_state() {
    assert_eq!(query_profile(None).unwrap(), ProfileStatus::Missing);

    let owner_text = UserSid::current().unwrap().to_sid_string().unwrap();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let install_id = format!("missing-{unique:x}");
    let name = format!("MewCode.Test.Missing.{}.{unique:x}", std::process::id());
    let sid = derive_profile_sid(&name).unwrap().to_sid_string().unwrap();
    let mut state = SandboxSetupState {
        schema_version: 1,
        install_id,
        owner_user_sid: owner_text,
        appcontainer_profile_name: name,
        appcontainer_sid: sid.clone(),
        helper_version: "0.1.0".to_owned(),
        ready: true,
    };
    // 只有可推导 SID、但没有真实 profile 数据目录时，不得误报 Matching。
    assert_eq!(
        query_profile(Some(&state)).unwrap(),
        ProfileStatus::Mismatch
    );
    state.appcontainer_sid = "S-1-15-2-0".to_owned();
    assert_eq!(
        query_profile(Some(&state)).unwrap(),
        ProfileStatus::Mismatch
    );
}

#[test]
#[ignore = "会为当前用户创建真实 AppContainer profile"]
fn setup_profile_is_idempotent() {
    let owner = UserSid::current().unwrap();
    let paths = StatePaths::for_current_user(&owner).unwrap();
    let first = prepare_profile(&owner, &paths).unwrap();
    let second = prepare_profile(&owner, &paths).unwrap();
    assert_eq!(first, second);
    assert!(matches!(
        query_profile(Some(&second)).unwrap(),
        ProfileStatus::Matching { .. }
    ));
}
