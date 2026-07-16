#![cfg(windows)]

use mewcode_windows_sandbox::acl::{
    AclEntryRecord, AclTransactionJournal, GrantMode, apply_entry, inheritance_for, list_ace_bytes,
    rights_for, rollback_entry,
};
use mewcode_windows_sandbox::paths::resolve_local_ntfs;
use mewcode_windows_sandbox::profile::derive_profile_sid;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

static FIXTURE_COUNTER: AtomicU64 = AtomicU64::new(0);

struct RollbackGuard<'a> {
    entry: &'a AclEntryRecord,
    active: bool,
}

impl<'a> RollbackGuard<'a> {
    fn new(entry: &'a AclEntryRecord) -> Self {
        Self {
            entry,
            active: true,
        }
    }

    fn rollback(&mut self) {
        rollback_entry(self.entry).unwrap();
        self.active = false;
    }
}

impl Drop for RollbackGuard<'_> {
    fn drop(&mut self) {
        if self.active {
            let _ = rollback_entry(self.entry);
        }
    }
}

fn fixture() -> (
    PathBuf,
    mewcode_windows_sandbox::paths::ResolvedPath,
    String,
) {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-apply-tests")
        .join(format!(
            "{}-{}",
            std::process::id(),
            FIXTURE_COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
    std::fs::create_dir_all(&root).unwrap();
    let file = root.join("target.txt");
    std::fs::write(&file, b"content").unwrap();
    let target = resolve_local_ntfs(&file).unwrap();
    let sid = derive_profile_sid("MewCode.Sandbox.0000000000000000")
        .unwrap()
        .to_sid_string()
        .unwrap();
    (file, target, sid)
}

#[test]
#[ignore = "会在 D 盘测试文件上临时添加并精确回滚 ACE"]
fn tagged_ace_apply_and_rollback_preserve_original_dacl() {
    let (file, target, sid) = fixture();
    let before = list_ace_bytes(&file).unwrap();
    let journal = AclTransactionJournal::new("acl-apply-1".to_owned()).unwrap();
    let entry = AclEntryRecord::new(
        &target,
        sid,
        rights_for(GrantMode::Read, target.kind),
        inheritance_for(target.kind),
        &journal.transaction_id,
    )
    .unwrap();
    apply_entry(&entry).unwrap();
    let mut guard = RollbackGuard::new(&entry);
    let during = list_ace_bytes(&file).unwrap();
    assert_eq!(during.len(), before.len() + 3);
    assert!(
        during
            .iter()
            .any(|ace| { String::from_utf8_lossy(ace).contains(&entry.ace_tag) })
    );
    guard.rollback();
    assert_eq!(list_ace_bytes(&file).unwrap(), before);
}

#[test]
#[ignore = "会在 D 盘测试文件上临时添加并精确回滚并发 ACE"]
fn concurrent_transactions_remove_only_their_own_tagged_ace() {
    let (file, target, sid) = fixture();
    let before = list_ace_bytes(&file).unwrap();
    let first = AclTransactionJournal::new("acl-concurrent-1".to_owned()).unwrap();
    let second = AclTransactionJournal::new("acl-concurrent-2".to_owned()).unwrap();
    let make_entry = |transaction_id: &str| {
        AclEntryRecord::new(
            &target,
            sid.clone(),
            rights_for(GrantMode::Read, target.kind),
            inheritance_for(target.kind),
            transaction_id,
        )
        .unwrap()
    };
    let first_entry = make_entry(&first.transaction_id);
    let second_entry = make_entry(&second.transaction_id);
    apply_entry(&first_entry).unwrap();
    let mut first_guard = RollbackGuard::new(&first_entry);
    apply_entry(&second_entry).unwrap();
    let mut second_guard = RollbackGuard::new(&second_entry);
    first_guard.rollback();
    let remaining = list_ace_bytes(&file).unwrap();
    assert!(
        remaining
            .iter()
            .any(|ace| { String::from_utf8_lossy(ace).contains(&second_entry.ace_tag) })
    );
    assert!(
        !remaining
            .iter()
            .any(|ace| { String::from_utf8_lossy(ace).contains(&first_entry.ace_tag) })
    );
    second_guard.rollback();
    assert_eq!(list_ace_bytes(&file).unwrap(), before);
}
