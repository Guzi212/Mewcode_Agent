#![cfg(windows)]

use mewcode_windows_sandbox::acl::{
    AclEntryRecord, AclTransaction, GrantMode, inheritance_for, list_ace_bytes, rights_for,
};
use mewcode_windows_sandbox::paths::resolve_local_ntfs;
use mewcode_windows_sandbox::state::{StatePaths, load_state};
use mewcode_windows_sandbox::user_sid::UserSid;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

fn real_state() -> (StatePaths, String) {
    let owner = UserSid::current().unwrap();
    let paths = StatePaths::for_current_user(&owner).unwrap();
    let state = load_state(&paths, &owner).unwrap();
    (paths, state.appcontainer_sid)
}

fn exercise(path: &Path, request_id: &str, mode: GrantMode) {
    let (_, sandbox_sid) = real_state();
    let target = resolve_local_ntfs(path).unwrap();
    let before = list_ace_bytes(&target.canonical_path).unwrap();
    let test_state = StatePaths::under(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("runner-acl-target-state"),
        request_id,
    );
    let mut transaction = AclTransaction::begin(test_state, request_id.to_owned(), |id| {
        Ok(vec![
            AclEntryRecord::new(
                &target,
                sandbox_sid,
                rights_for(mode, target.kind),
                inheritance_for(target.kind),
                id,
            )
            .unwrap(),
        ])
    })
    .unwrap();
    transaction.verify_targets_unchanged().unwrap();
    transaction.rollback().unwrap();
    assert_eq!(list_ace_bytes(&target.canonical_path).unwrap(), before);
}

#[test]
#[ignore = "会对真实工作区目录临时添加并精确回滚 AppContainer ACE"]
fn workspace_directory_acl_round_trips() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let workspace = manifest
        .join("target")
        .join("runner-acl-target-fixtures")
        .join("workspace");
    std::fs::create_dir_all(&workspace).unwrap();
    exercise(&workspace, "runner-workspace", GrantMode::Write);
}

#[test]
#[ignore = "会对真实 venv Python 临时添加并精确回滚 AppContainer ACE"]
fn venv_python_acl_round_trips() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let source = manifest
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join(".venv")
        .join("Scripts")
        .join("python.exe");
    let fixture_root = manifest
        .join("target")
        .join("runner-acl-target-fixtures")
        .join("python");
    std::fs::create_dir_all(&fixture_root).unwrap();
    let fixture = fixture_root.join("python.exe");
    std::fs::copy(source, &fixture).unwrap();
    exercise(&fixture, "runner-python", GrantMode::Execute);
}

#[test]
#[ignore = "会验证目录事务 ACE 向现有子文件传播并精确回滚"]
fn child_first_directory_transaction_round_trips() {
    let (_, sandbox_sid) = real_state();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-propagation-target");
    std::fs::create_dir_all(&root).unwrap();
    let child = root.join("child.txt");
    std::fs::write(&child, b"propagation").unwrap();
    let root = resolve_local_ntfs(&root).unwrap();
    let child_target = resolve_local_ntfs(&child).unwrap();
    let before = list_ace_bytes(&child).unwrap();
    let test_state = StatePaths::under(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("runner-acl-target-state"),
        "runner-propagation",
    );
    let mut transaction =
        AclTransaction::begin(test_state, "runner-propagation".to_owned(), |id| {
            Ok(vec![
                AclEntryRecord::new(
                    &child_target,
                    sandbox_sid.clone(),
                    rights_for(GrantMode::Read, child_target.kind),
                    inheritance_for(child_target.kind),
                    id,
                )
                .unwrap(),
                AclEntryRecord::new(
                    &root,
                    sandbox_sid,
                    rights_for(GrantMode::Read, root.kind),
                    inheritance_for(root.kind),
                    id,
                )
                .unwrap(),
            ])
        })
        .unwrap();
    let during = list_ace_bytes(&child).unwrap();
    assert_ne!(during, before);
    transaction.rollback().unwrap();
    assert_eq!(list_ace_bytes(&child).unwrap(), before);
}

#[test]
#[ignore = "会验证新增根目录 ACE 不会覆盖现有子文件"]
fn root_inheritance_covers_existing_child_and_rolls_back() {
    let (_, sandbox_sid) = real_state();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-root-inheritance")
        .join(format!("{}-{unique}", std::process::id()));
    std::fs::create_dir_all(&root_path).unwrap();
    let child = root_path.join("child.txt");
    std::fs::write(&child, b"inheritance").unwrap();
    let root = resolve_local_ntfs(&root_path).unwrap();
    let root_before = list_ace_bytes(&root_path).unwrap();
    let child_before = list_ace_bytes(&child).unwrap();
    let state_id = format!("root-inheritance-{unique}");
    let test_state = StatePaths::under(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("runner-acl-target-state"),
        &state_id,
    );
    let mut transaction = AclTransaction::begin(test_state, state_id, |transaction_id| {
        Ok(vec![
            AclEntryRecord::new(
                &root,
                sandbox_sid,
                rights_for(GrantMode::Read, root.kind),
                inheritance_for(root.kind),
                transaction_id,
            )
            .unwrap(),
        ])
    })
    .unwrap();
    assert_ne!(list_ace_bytes(&child).unwrap(), child_before);
    transaction.rollback().unwrap();
    assert_eq!(list_ace_bytes(&root_path).unwrap(), root_before);
    assert_eq!(list_ace_bytes(&child).unwrap(), child_before);
}

#[test]
#[ignore = "会验证事务期间新建目标的继承 ACE 可被精确清理"]
fn newly_created_descendants_match_post_rollback_controls() {
    let (_, sandbox_sid) = real_state();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-new-descendants")
        .join(format!("{}-{unique}", std::process::id()));
    std::fs::create_dir_all(&root_path).unwrap();
    let existing_child = root_path.join("existing.txt");
    std::fs::write(&existing_child, b"existing").unwrap();

    let root = resolve_local_ntfs(&root_path).unwrap();
    let child = resolve_local_ntfs(&existing_child).unwrap();
    let root_before = list_ace_bytes(&root_path).unwrap();
    let child_before = list_ace_bytes(&existing_child).unwrap();
    let state_id = format!("new-descendants-{unique}");
    let test_state = StatePaths::under(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("runner-acl-target-state"),
        &state_id,
    );
    let mut transaction = AclTransaction::begin(
        test_state,
        format!("new-descendants-{unique}"),
        |transaction_id| {
            Ok(vec![
                AclEntryRecord::new(
                    &child,
                    sandbox_sid.clone(),
                    rights_for(GrantMode::Write, child.kind),
                    inheritance_for(child.kind),
                    transaction_id,
                )
                .unwrap(),
                AclEntryRecord::new(
                    &root,
                    sandbox_sid,
                    rights_for(GrantMode::Write, root.kind),
                    inheritance_for(root.kind),
                    transaction_id,
                )
                .unwrap(),
            ])
        },
    )
    .unwrap();

    let created_directory = root_path.join("created");
    let created_file = created_directory.join("result.txt");
    std::fs::create_dir(&created_directory).unwrap();
    std::fs::write(&created_file, b"created during transaction").unwrap();
    let created_directory_during = list_ace_bytes(&created_directory).unwrap();
    let created_file_during = list_ace_bytes(&created_file).unwrap();

    transaction.rollback().unwrap();
    assert_eq!(list_ace_bytes(&root_path).unwrap(), root_before);
    assert_eq!(list_ace_bytes(&existing_child).unwrap(), child_before);

    let control_directory = root_path.join("control");
    let control_file = control_directory.join("result.txt");
    std::fs::create_dir(&control_directory).unwrap();
    std::fs::write(&control_file, b"created after rollback").unwrap();
    let created_directory_after = list_ace_bytes(&created_directory).unwrap();
    let created_file_after = list_ace_bytes(&created_file).unwrap();
    assert_ne!(created_directory_during, created_directory_after);
    assert_ne!(created_file_during, created_file_after);
    assert_eq!(
        created_directory_after,
        list_ace_bytes(&control_directory).unwrap()
    );
    assert_eq!(created_file_after, list_ace_bytes(&control_file).unwrap());
}

#[test]
#[ignore = "会验证覆盖既有文件后按新文件 ID 清理继承 ACE"]
fn atomic_replacement_rolls_back_without_stale_ace() {
    let (_, sandbox_sid) = real_state();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-atomic-replacement")
        .join(format!("{}-{unique}", std::process::id()));
    std::fs::create_dir_all(&root_path).unwrap();
    let target_path = root_path.join("result.txt");
    std::fs::write(&target_path, b"before").unwrap();
    let root = resolve_local_ntfs(&root_path).unwrap();
    let target = resolve_local_ntfs(&target_path).unwrap();
    let root_before = list_ace_bytes(&root_path).unwrap();
    let state_id = format!("atomic-replacement-{unique}");
    let test_state = StatePaths::under(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("runner-acl-target-state"),
        &state_id,
    );
    let mut transaction = AclTransaction::begin(test_state, state_id, |transaction_id| {
        Ok(vec![
            AclEntryRecord::new(
                &target,
                sandbox_sid.clone(),
                rights_for(GrantMode::Write, target.kind),
                inheritance_for(target.kind),
                transaction_id,
            )
            .unwrap(),
            AclEntryRecord::new(
                &root,
                sandbox_sid,
                rights_for(GrantMode::Write, root.kind),
                inheritance_for(root.kind),
                transaction_id,
            )
            .unwrap(),
        ])
    })
    .unwrap();

    let temporary = root_path.join("temporary.txt");
    std::fs::write(&temporary, b"after").unwrap();
    std::fs::remove_file(&target_path).unwrap();
    std::fs::rename(&temporary, &target_path).unwrap();
    transaction.rollback().unwrap();

    assert_eq!(list_ace_bytes(&root_path).unwrap(), root_before);
    let control = root_path.join("control.txt");
    std::fs::write(&control, b"control").unwrap();
    assert_eq!(
        list_ace_bytes(&target_path).unwrap(),
        list_ace_bytes(&control).unwrap()
    );
}

#[test]
#[ignore = "会验证事务期内移动既有文件后按文件 ID 精确回滚"]
fn moved_existing_file_is_rolled_back_at_its_new_path() {
    let (_, sandbox_sid) = real_state();
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-moved-existing")
        .join(format!("{}-{unique}", std::process::id()));
    std::fs::create_dir_all(&root_path).unwrap();
    let source = root_path.join("source.txt");
    let destination = root_path.join("destination.txt");
    std::fs::write(&source, b"content").unwrap();
    let root = resolve_local_ntfs(&root_path).unwrap();
    let source_target = resolve_local_ntfs(&source).unwrap();
    let root_before = list_ace_bytes(&root_path).unwrap();
    let source_before = list_ace_bytes(&source).unwrap();
    let state_id = format!("moved-existing-{unique}");
    let test_state = StatePaths::under(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join("runner-acl-target-state"),
        &state_id,
    );
    let mut transaction = AclTransaction::begin(test_state, state_id, |transaction_id| {
        Ok(vec![
            AclEntryRecord::new(
                &source_target,
                sandbox_sid.clone(),
                rights_for(GrantMode::Write, source_target.kind),
                inheritance_for(source_target.kind),
                transaction_id,
            )
            .unwrap(),
            AclEntryRecord::new(
                &root,
                sandbox_sid,
                rights_for(GrantMode::Write, root.kind),
                inheritance_for(root.kind),
                transaction_id,
            )
            .unwrap(),
        ])
    })
    .unwrap();

    std::fs::rename(&source, &destination).unwrap();
    transaction.rollback().unwrap();

    assert_eq!(list_ace_bytes(&root_path).unwrap(), root_before);
    assert_eq!(list_ace_bytes(&destination).unwrap(), source_before);
}
