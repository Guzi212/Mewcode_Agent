#![cfg(windows)]

use mewcode_windows_sandbox::acl::{
    AclEntryRecord, AclTransaction, AclTransactionJournal, GrantMode, JournalPath, apply_entry,
    inheritance_for, list_ace_bytes, recover_pending_transactions, rights_for, rollback_entry,
    write_journal_atomic,
};
use mewcode_windows_sandbox::paths::resolve_local_ntfs;
use mewcode_windows_sandbox::profile::derive_profile_sid;
use mewcode_windows_sandbox::state::StatePaths;
use std::path::PathBuf;

fn fixture(label: &str) -> (StatePaths, PathBuf, String) {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-recovery-tests")
        .join(format!("{label}-{}", std::process::id()));
    std::fs::create_dir_all(&base).unwrap();
    let file = base.join("target.txt");
    std::fs::write(&file, b"content").unwrap();
    let sid = derive_profile_sid("MewCode.Sandbox.0000000000000000")
        .unwrap()
        .to_sid_string()
        .unwrap();
    (
        StatePaths::under(base.join("state"), "acl-recovery-test"),
        file,
        sid,
    )
}

#[test]
#[ignore = "会在 D 盘测试文件上模拟崩溃并恢复带标签 ACE"]
fn prepared_journal_recovers_applied_ace_after_crash() {
    let (state, file, sid) = fixture("prepared");
    let before = list_ace_bytes(&file).unwrap();
    let target = resolve_local_ntfs(&file).unwrap();
    let mut journal = AclTransactionJournal::new("recovery-request".to_owned()).unwrap();
    let entry = AclEntryRecord::new(
        &target,
        sid,
        rights_for(GrantMode::Read, target.kind),
        inheritance_for(target.kind),
        &journal.transaction_id,
    )
    .unwrap();
    journal.entries.push(entry.clone());
    let path = JournalPath::for_transaction(&state, &journal.transaction_id).unwrap();
    write_journal_atomic(&path, &journal).unwrap();
    apply_entry(&entry).unwrap();

    if let Err(error) = recover_pending_transactions(&state) {
        let _ = rollback_entry(&entry);
        panic!("recovery failed: {error}");
    }
    assert_eq!(list_ace_bytes(&file).unwrap(), before);
    assert!(!path.file.exists());
}

#[test]
#[ignore = "会在 D 盘测试文件上执行完整 ACL 事务"]
fn transaction_finally_rolls_back_and_removes_journal() {
    let (state, file, sid) = fixture("transaction");
    let before = list_ace_bytes(&file).unwrap();
    let target = resolve_local_ntfs(&file).unwrap();
    let entry_target = target.clone();
    let entry_sid = sid.clone();
    let mut transaction = AclTransaction::begin(
        state.clone(),
        "transaction-request".to_owned(),
        move |transaction_id| {
            Ok(vec![AclEntryRecord::new(
                &entry_target,
                entry_sid,
                rights_for(GrantMode::Write, entry_target.kind),
                inheritance_for(entry_target.kind),
                transaction_id,
            )?])
        },
    )
    .unwrap();
    assert_eq!(list_ace_bytes(&file).unwrap().len(), before.len() + 3);
    transaction.rollback().unwrap();
    assert_eq!(list_ace_bytes(&file).unwrap(), before);
    assert_eq!(recover_pending_transactions(&state).unwrap(), 0);
}

#[test]
fn corrupt_journal_fails_closed_without_touching_dacl() {
    let (state, file, _sid) = fixture("corrupt");
    let before = list_ace_bytes(&file).unwrap();
    let directory = state.root.join("transactions");
    std::fs::create_dir_all(&directory).unwrap();
    std::fs::write(directory.join("broken.json"), b"{broken").unwrap();
    assert!(recover_pending_transactions(&state).is_err());
    assert_eq!(list_ace_bytes(&file).unwrap(), before);
}
