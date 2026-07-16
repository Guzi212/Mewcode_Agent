#![cfg(windows)]

use mewcode_windows_sandbox::acl::{
    AclEntryRecord, AclTransactionJournal, JournalPath, JournalPhase, load_journal,
    write_journal_atomic,
};
use mewcode_windows_sandbox::paths::resolve_local_ntfs;
use mewcode_windows_sandbox::state::StatePaths;
use std::path::PathBuf;

fn fixture() -> (StatePaths, mewcode_windows_sandbox::paths::ResolvedPath) {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("acl-journal-tests")
        .join(std::process::id().to_string());
    let target = base.join("target.txt");
    std::fs::create_dir_all(&base).unwrap();
    std::fs::write(&target, b"content").unwrap();
    (
        StatePaths::under(base.join("state"), "test-install"),
        resolve_local_ntfs(&target).unwrap(),
    )
}

#[test]
fn journal_round_trips_and_phase_updates_atomically() {
    let (state, target) = fixture();
    let mut journal = AclTransactionJournal::new("request-1".to_owned()).unwrap();
    journal.entries.push(
        AclEntryRecord::new(
            &target,
            "S-1-15-2-123".to_owned(),
            0x120089,
            0,
            &journal.transaction_id,
        )
        .unwrap(),
    );
    let path = JournalPath::for_transaction(&state, &journal.transaction_id).unwrap();
    write_journal_atomic(&path, &journal).unwrap();
    assert_eq!(load_journal(&path).unwrap(), journal);

    journal.phase = JournalPhase::Applied;
    write_journal_atomic(&path, &journal).unwrap();
    assert_eq!(load_journal(&path).unwrap().phase, JournalPhase::Applied);
}

#[test]
fn rejects_unknown_schema_corruption_and_fingerprint_tampering() {
    let (state, target) = fixture();
    let mut journal = AclTransactionJournal::new("request-2".to_owned()).unwrap();
    journal.entries.push(
        AclEntryRecord::new(
            &target,
            "S-1-15-2-456".to_owned(),
            1,
            3,
            &journal.transaction_id,
        )
        .unwrap(),
    );
    let path = JournalPath::for_transaction(&state, &journal.transaction_id).unwrap();
    write_journal_atomic(&path, &journal).unwrap();

    let original = std::fs::read_to_string(&path.file).unwrap();
    std::fs::write(
        &path.file,
        original.replace("\"schema_version\":1", "\"schema_version\":99"),
    )
    .unwrap();
    assert!(load_journal(&path).is_err());

    write_journal_atomic(&path, &journal).unwrap();
    let original = std::fs::read_to_string(&path.file).unwrap();
    std::fs::write(&path.file, original.replace("\"rights\":1", "\"rights\":2")).unwrap();
    assert!(load_journal(&path).is_err());

    std::fs::write(&path.file, b"{broken").unwrap();
    assert!(load_journal(&path).is_err());
}
