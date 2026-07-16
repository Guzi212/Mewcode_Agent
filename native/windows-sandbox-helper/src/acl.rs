use crate::paths::{PathKind, ResolvedPath, paths_equal, resolve_local_ntfs};
use crate::state::StatePaths;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, WAIT_ABANDONED, WAIT_OBJECT_0};
use windows_sys::Win32::Security::Authorization::{
    ConvertStringSidToSidW, GetNamedSecurityInfoW, SE_FILE_OBJECT, SetNamedSecurityInfoW,
};
use windows_sys::Win32::Security::{
    ACE_HEADER, ACL, ACL_REVISION_DS, ACL_SIZE_INFORMATION, AclSizeInformation, AddAce,
    CONTAINER_INHERIT_ACE, DACL_SECURITY_INFORMATION, GetAce, GetAclInformation, GetLengthSid,
    INHERITED_ACE, InitializeAcl, InitializeSecurityDescriptor, OBJECT_INHERIT_ACE, PSID,
    SECURITY_DESCRIPTOR, SetFileSecurityW, SetSecurityDescriptorDacl,
};
use windows_sys::Win32::Storage::FileSystem::{
    DELETE, FILE_DELETE_CHILD, FILE_GENERIC_EXECUTE, FILE_GENERIC_READ, FILE_GENERIC_WRITE,
    FILE_WRITE_DATA, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
};
use windows_sys::Win32::System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject};

pub const ACL_JOURNAL_SCHEMA_VERSION: u32 = 1;
pub const ACE_TAG_PREFIX: &str = "MEWCODE-TX:";
const ACCESS_OWNER_TAG_PREFIX: &str = "MEWCODE-ACCESS:";
const ACCESS_ALLOWED_ACE_TYPE: u8 = 0;
const ACCESS_ALLOWED_CALLBACK_ACE_TYPE: u8 = 9;
const MAX_JOURNAL_BYTES: u64 = 1024 * 1024;
static TRANSACTION_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantMode {
    Read,
    Write,
    Execute,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JournalPhase {
    Prepared,
    Applied,
    RollingBack,
    Broken,
    Completed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AclEntryRecord {
    pub canonical_path: String,
    pub volume_serial: u32,
    pub file_id: u64,
    pub sandbox_sid: String,
    pub rights: u32,
    pub inheritance: u8,
    pub ace_tag: String,
    pub ace_fingerprint: String,
}

impl AclEntryRecord {
    pub fn new(
        target: &ResolvedPath,
        sandbox_sid: String,
        rights: u32,
        inheritance: u8,
        transaction_id: &str,
    ) -> Result<Self, AclError> {
        validate_id(transaction_id)?;
        if sandbox_sid.is_empty() || sandbox_sid.contains('\0') {
            return Err(AclError::Invalid("沙箱 SID 无效"));
        }
        let canonical_path = target.canonical_path.to_string_lossy().into_owned();
        let ace_tag = format!("{ACE_TAG_PREFIX}{transaction_id}");
        let ace_fingerprint = entry_fingerprint(
            &canonical_path,
            target.identity.volume_serial,
            target.identity.file_id,
            &sandbox_sid,
            rights,
            inheritance,
            &ace_tag,
        );
        Ok(Self {
            canonical_path,
            volume_serial: target.identity.volume_serial,
            file_id: target.identity.file_id,
            sandbox_sid,
            rights,
            inheritance,
            ace_tag,
            ace_fingerprint,
        })
    }

    pub fn validate(&self, transaction_id: &str) -> Result<(), AclError> {
        if self.canonical_path.is_empty()
            || self.ace_tag != format!("{ACE_TAG_PREFIX}{transaction_id}")
            || self.ace_fingerprint
                != entry_fingerprint(
                    &self.canonical_path,
                    self.volume_serial,
                    self.file_id,
                    &self.sandbox_sid,
                    self.rights,
                    self.inheritance,
                    &self.ace_tag,
                )
        {
            return Err(AclError::Invalid("ACL 事务记录指纹不匹配"));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AclTransactionJournal {
    pub schema_version: u32,
    pub transaction_id: String,
    pub owner_pid: u32,
    pub request_id: String,
    pub phase: JournalPhase,
    pub entries: Vec<AclEntryRecord>,
}

impl AclTransactionJournal {
    pub fn new(request_id: String) -> Result<Self, AclError> {
        validate_id(&request_id)?;
        Ok(Self {
            schema_version: ACL_JOURNAL_SCHEMA_VERSION,
            transaction_id: new_transaction_id(),
            owner_pid: std::process::id(),
            request_id,
            phase: JournalPhase::Prepared,
            entries: Vec::new(),
        })
    }

    pub fn validate(&self) -> Result<(), AclError> {
        if self.schema_version != ACL_JOURNAL_SCHEMA_VERSION {
            return Err(AclError::Invalid("ACL 日志 schema 版本不受支持"));
        }
        validate_id(&self.transaction_id)?;
        validate_id(&self.request_id)?;
        if self.owner_pid == 0 {
            return Err(AclError::Invalid("ACL 日志 owner PID 无效"));
        }
        for entry in &self.entries {
            entry.validate(&self.transaction_id)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct JournalPath {
    pub file: PathBuf,
}

impl JournalPath {
    pub fn for_transaction(state: &StatePaths, transaction_id: &str) -> Result<Self, AclError> {
        validate_id(transaction_id)?;
        Ok(Self {
            file: state
                .root
                .join("transactions")
                .join(format!("{transaction_id}.json")),
        })
    }
}

pub fn write_journal_atomic(
    path: &JournalPath,
    journal: &AclTransactionJournal,
) -> Result<(), AclError> {
    journal.validate()?;
    let parent = path
        .file
        .parent()
        .ok_or(AclError::Invalid("ACL 日志路径无父目录"))?;
    fs::create_dir_all(parent).map_err(AclError::Io)?;
    let temporary = parent.join(format!(".{}.tmp", journal.transaction_id));
    let result = (|| {
        let bytes =
            serde_json::to_vec(journal).map_err(|_| AclError::Invalid("ACL 日志无法序列化"))?;
        if bytes.len() as u64 > MAX_JOURNAL_BYTES {
            return Err(AclError::Invalid("ACL 日志超过大小限制"));
        }
        let mut stream = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)
            .map_err(AclError::Io)?;
        stream.write_all(&bytes).map_err(AclError::Io)?;
        stream.sync_all().map_err(AclError::Io)?;
        drop(stream);
        move_replace(&temporary, &path.file)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub fn load_journal(path: &JournalPath) -> Result<AclTransactionJournal, AclError> {
    let metadata = fs::metadata(&path.file).map_err(AclError::Io)?;
    if !metadata.is_file() || metadata.len() > MAX_JOURNAL_BYTES {
        return Err(AclError::Invalid("ACL 日志类型或大小无效"));
    }
    let bytes = fs::read(&path.file).map_err(AclError::Io)?;
    let journal: AclTransactionJournal =
        serde_json::from_slice(&bytes).map_err(|_| AclError::Invalid("ACL 日志已损坏"))?;
    journal.validate()?;
    Ok(journal)
}

pub fn rights_for(mode: GrantMode, kind: PathKind) -> u32 {
    match mode {
        GrantMode::Read => FILE_GENERIC_READ,
        GrantMode::Execute => FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
        GrantMode::Write => {
            let base = FILE_GENERIC_READ | FILE_GENERIC_WRITE | DELETE;
            if kind == PathKind::Directory {
                base | FILE_DELETE_CHILD
            } else {
                base
            }
        }
    }
}

pub fn inheritance_for(kind: PathKind) -> u8 {
    if kind == PathKind::Directory {
        (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) as u8
    } else {
        0
    }
}

pub fn apply_entry(entry: &AclEntryRecord) -> Result<(), AclError> {
    let target = verify_record_identity(entry)?;
    let tagged_ace = build_tagged_ace(entry)?;
    let owner_ace = build_access_owner_ace(entry)?;
    let access_ace = build_access_ace(entry)?;
    let mut aces = read_aces(&target.canonical_path)?;
    let original = aces.clone();
    if count_matching_aces(&aces, entry)? != 0 {
        return Err(AclError::Invalid("ACL 事务 ACE 已存在"));
    }
    let access_count = count_access_aces(&aces, entry);
    let owner_count = count_access_owner_aces(&aces, entry);
    if owner_count > 1 || (owner_count == 1 && access_count != 1) {
        return Err(AclError::Invalid("ACL 共享授权状态不一致"));
    }
    let create_shared_access = access_count == 0;
    let insertion = aces
        .iter()
        .position(|ace| {
            ace.get(1)
                .is_some_and(|flags| flags & INHERITED_ACE as u8 != 0)
        })
        .unwrap_or(aces.len());
    aces.insert(insertion, tagged_ace);
    if create_shared_access {
        aces.insert(insertion + 1, owner_ace);
        aces.insert(insertion + 2, access_ace);
    }
    write_aces(&target.canonical_path, &aces, entry.inheritance != 0)?;
    let verified = read_aces(&target.canonical_path)?;
    let marker_count = count_matching_aces(&verified, entry)?;
    let verified_access_count = count_access_aces(&verified, entry);
    let verified_owner_count = count_access_owner_aces(&verified, entry);
    let expected_access_count = access_count + usize::from(create_shared_access);
    let expected_owner_count = owner_count + usize::from(create_shared_access);
    if marker_count != 1
        || verified_access_count != expected_access_count
        || verified_owner_count != expected_owner_count
    {
        if std::env::var_os("MEWCODE_HELPER_TRACE").is_some() {
            eprintln!(
                "[mewcode-helper] ACL verification mismatch: marker={marker_count}, access={verified_access_count}/{expected_access_count}, owner={verified_owner_count}/{expected_owner_count}"
            );
        }
        let _ = write_aces(&target.canonical_path, &original, entry.inheritance != 0);
        return Err(AclError::Invalid("ACL 事务 ACE 写入复检失败"));
    }
    Ok(())
}

pub fn rollback_entry(entry: &AclEntryRecord) -> Result<(), AclError> {
    let target = verify_record_identity(entry)?;
    if !rollback_entry_at_path(&target.canonical_path, entry)? {
        return Err(AclError::Invalid("ACL 事务 ACE 不存在"));
    }
    Ok(())
}

fn rollback_entry_if_present(entry: &AclEntryRecord) -> Result<bool, AclError> {
    let path = Path::new(&entry.canonical_path);
    let target = match resolve_local_ntfs(path) {
        Ok(target) => target,
        Err(_) if entry.rights & FILE_WRITE_DATA != 0 && !path.exists() => return Ok(false),
        Err(_) => return Err(AclError::Invalid("ACL 目标路径无效")),
    };
    if target.identity.volume_serial != entry.volume_serial
        || target.identity.file_id != entry.file_id
    {
        if entry.rights & FILE_WRITE_DATA != 0 {
            return Ok(false);
        }
        return Err(AclError::Invalid("只读 ACL 目标在授权后发生变化"));
    }
    rollback_entry_at_path(&target.canonical_path, entry)
}

fn rollback_entry_at_path(path: &Path, entry: &AclEntryRecord) -> Result<bool, AclError> {
    let mut aces = read_aces(path)?;
    if count_matching_aces(&aces, entry)? == 0 {
        return Ok(false);
    }
    remove_transaction_ace_pair(&mut aces, entry)?;
    write_aces(path, &aces, entry.inheritance != 0)?;
    if count_matching_aces(&read_aces(path)?, entry)? != 0 {
        return Err(AclError::Invalid("ACL 恢复复检失败"));
    }
    Ok(true)
}

pub struct AclTransaction {
    state: StatePaths,
    path: JournalPath,
    journal: AclTransactionJournal,
    finished: bool,
}

impl AclTransaction {
    pub fn begin(
        state: StatePaths,
        request_id: String,
        entries: impl FnOnce(&str) -> Result<Vec<AclEntryRecord>, AclError>,
    ) -> Result<Self, AclError> {
        let mut journal = AclTransactionJournal::new(request_id)?;
        journal.entries = entries(&journal.transaction_id)?;
        let path = JournalPath::for_transaction(&state, &journal.transaction_id)?;
        let _guard = AclMutex::acquire(&state)?;
        write_journal_atomic(&path, &journal)?;
        let mut applied = Vec::new();
        for entry in &journal.entries {
            if let Err(error) = apply_entry(entry) {
                let mut rollback_failed = false;
                for applied_entry in applied.iter().rev() {
                    if rollback_entry(applied_entry).is_err() {
                        rollback_failed = true;
                    }
                }
                if rollback_failed {
                    journal.phase = JournalPhase::Broken;
                    let _ = write_journal_atomic(&path, &journal);
                    return Err(error);
                }
                journal.phase = JournalPhase::Completed;
                let _ = write_journal_atomic(&path, &journal);
                let _ = fs::remove_file(&path.file);
                return Err(error);
            }
            applied.push(entry.clone());
        }
        journal.phase = JournalPhase::Applied;
        write_journal_atomic(&path, &journal)?;
        Ok(Self {
            state,
            path,
            journal,
            finished: false,
        })
    }

    pub fn rollback(&mut self) -> Result<(), AclError> {
        if self.finished {
            return Ok(());
        }
        let _guard = AclMutex::acquire(&self.state)?;
        self.journal.phase = JournalPhase::RollingBack;
        write_journal_atomic(&self.path, &self.journal)?;
        let cleanup_started = Instant::now();
        if let Err(error) = cleanup_new_descendants(&self.journal.entries) {
            self.journal.phase = JournalPhase::Broken;
            let _ = write_journal_atomic(&self.path, &self.journal);
            return Err(error);
        }
        trace_acl("new descendant cleanup complete", cleanup_started);
        let entries_started = Instant::now();
        for (index, entry) in self.journal.entries.iter().rev().enumerate() {
            if let Err(error) = rollback_entry_if_present(entry) {
                self.journal.phase = JournalPhase::Broken;
                let _ = write_journal_atomic(&self.path, &self.journal);
                return Err(error);
            }
            if (index + 1) % 50 == 0 {
                trace_acl(
                    &format!("rolled back {} ACL entries", index + 1),
                    entries_started,
                );
            }
        }
        trace_acl("ACL entry rollback complete", entries_started);
        self.journal.phase = JournalPhase::Completed;
        write_journal_atomic(&self.path, &self.journal)?;
        fs::remove_file(&self.path.file).map_err(AclError::Io)?;
        self.finished = true;
        Ok(())
    }

    pub fn verify_targets_unchanged(&self) -> Result<(), AclError> {
        for entry in &self.journal.entries {
            verify_record_identity(entry)?;
        }
        Ok(())
    }
}

fn trace_acl(message: &str, started: Instant) {
    if std::env::var_os("MEWCODE_HELPER_TRACE").is_some() {
        eprintln!("[mewcode-helper] {message}: {:?}", started.elapsed());
    }
}

impl Drop for AclTransaction {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.rollback();
        }
    }
}

pub fn recover_pending_transactions(state: &StatePaths) -> Result<usize, AclError> {
    let _guard = AclMutex::acquire(state)?;
    let directory = state.root.join("transactions");
    if !directory.exists() {
        return Ok(0);
    }
    let mut recovered = 0usize;
    for item in fs::read_dir(&directory).map_err(AclError::Io)? {
        let item = item.map_err(AclError::Io)?;
        let path = item.path();
        if path.extension().and_then(|value| value.to_str()) != Some("json") {
            return Err(AclError::Invalid("ACL 事务目录包含未知文件"));
        }
        let journal_path = JournalPath { file: path };
        let mut journal = load_journal(&journal_path)?;
        if journal.phase == JournalPhase::Broken {
            return Err(AclError::Invalid("存在 BROKEN ACL 事务"));
        }
        if journal.phase != JournalPhase::Completed {
            journal.phase = JournalPhase::RollingBack;
            write_journal_atomic(&journal_path, &journal)?;
            let cleanup_started = Instant::now();
            if let Err(error) = cleanup_new_descendants(&journal.entries) {
                journal.phase = JournalPhase::Broken;
                let _ = write_journal_atomic(&journal_path, &journal);
                return Err(error);
            }
            trace_acl("recovery descendant cleanup complete", cleanup_started);
            let entries_started = Instant::now();
            for (index, entry) in journal.entries.iter().rev().enumerate() {
                rollback_entry_if_present(entry)?;
                if (index + 1) % 50 == 0 {
                    trace_acl(
                        &format!("recovery checked {} ACL entries", index + 1),
                        entries_started,
                    );
                }
            }
            trace_acl("recovery ACL entries complete", entries_started);
        }
        journal.phase = JournalPhase::Completed;
        write_journal_atomic(&journal_path, &journal)?;
        fs::remove_file(&journal_path.file).map_err(AclError::Io)?;
        recovered += 1;
    }
    Ok(recovered)
}

fn cleanup_new_descendants(entries: &[AclEntryRecord]) -> Result<(), AclError> {
    let mut known: HashMap<(u32, u64), Vec<&AclEntryRecord>> = HashMap::new();
    for entry in entries {
        known
            .entry((entry.volume_serial, entry.file_id))
            .or_default()
            .push(entry);
    }
    let candidates: Vec<&AclEntryRecord> = entries
        .iter()
        .filter(|entry| entry.inheritance != 0 && entry.rights & FILE_WRITE_DATA != 0)
        .collect();
    for root_entry in candidates.iter().copied().filter(|candidate| {
        let candidate_path = Path::new(&candidate.canonical_path);
        !candidates.iter().copied().any(|other| {
            other.ace_fingerprint != candidate.ace_fingerprint
                && other.sandbox_sid == candidate.sandbox_sid
                && other.rights == candidate.rights
                && candidate_path.starts_with(Path::new(&other.canonical_path))
        })
    }) {
        let root_path = Path::new(&root_entry.canonical_path);
        let has_explicit_descendants = entries.iter().any(|entry| {
            entry.ace_fingerprint != root_entry.ace_fingerprint
                && entry.ace_tag == root_entry.ace_tag
                && Path::new(&entry.canonical_path).starts_with(root_path)
        });
        if !has_explicit_descendants {
            // 新事务只记录可继承根目录。SetNamedSecurityInfoW 会在根目录回滚时
            // 自动移除所有子项继承的 ACE，无需逐文件扫描。
            continue;
        }
        let root = verify_record_identity(root_entry)?;
        let root_aces = read_aces(&root.canonical_path)?;
        if count_access_owner_aces(&root_aces, root_entry) != 1
            || count_transaction_markers_for_access(&root_aces, root_entry) != 1
        {
            continue;
        }
        let mut pending = vec![root.canonical_path];
        while let Some(directory) = pending.pop() {
            for item in fs::read_dir(&directory).map_err(AclError::Io)? {
                let path = item.map_err(AclError::Io)?.path();
                let resolved = resolve_local_ntfs(&path)
                    .map_err(|_| AclError::Invalid("ACL 清理遇到不安全的新目标"))?;
                let identity = (resolved.identity.volume_serial, resolved.identity.file_id);
                if let Some(original_entries) = known.get(&identity) {
                    for original in original_entries {
                        if !paths_equal(
                            &resolved.canonical_path,
                            Path::new(&original.canonical_path),
                        ) {
                            rollback_entry_at_path(&resolved.canonical_path, original)?;
                        }
                    }
                } else {
                    remove_inherited_access_ace(&resolved.canonical_path, root_entry)?;
                }
                if resolved.kind == PathKind::Directory {
                    pending.push(resolved.canonical_path);
                }
            }
        }
    }
    Ok(())
}

fn remove_inherited_access_ace(path: &Path, entry: &AclEntryRecord) -> Result<bool, AclError> {
    let mut aces = read_aces(path)?;
    if aces
        .iter()
        .any(|ace| any_access_owner_ace_matches(ace, entry))
    {
        return Ok(false);
    }
    let matching: Vec<usize> = aces
        .iter()
        .enumerate()
        .filter_map(|(index, ace)| descendant_access_ace_matches(ace, entry).then_some(index))
        .collect();
    match matching.as_slice() {
        [] => Ok(false),
        _ => {
            for index in matching.iter().rev() {
                aces.remove(*index);
            }
            write_aces(path, &aces, false)?;
            if read_aces(path)?
                .iter()
                .any(|ace| descendant_access_ace_matches(ace, entry))
            {
                return Err(AclError::Invalid("新目标继承 ACE 清理复检失败"));
            }
            Ok(true)
        }
    }
}

fn descendant_access_ace_matches(ace: &[u8], entry: &AclEntryRecord) -> bool {
    let Some(flags) = ace.get(1).copied() else {
        return false;
    };
    matching_application_data(ace, entry, ACCESS_ALLOWED_ACE_TYPE, flags)
        .is_some_and(|application_data| application_data.iter().all(|byte| *byte == 0))
}

struct AclMutex {
    handle: windows_sys::Win32::Foundation::HANDLE,
}

impl AclMutex {
    fn acquire(state: &StatePaths) -> Result<Self, AclError> {
        let install_id = state
            .root
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or(AclError::Invalid("ACL 状态 install ID 无效"))?;
        validate_id(install_id)?;
        let name: Vec<u16> = format!("Local\\MewCodeAcl-{install_id}")
            .encode_utf16()
            .chain([0])
            .collect();
        // SAFETY: 创建当前会话命名 Mutex，名称合法且 NUL 结尾。
        let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if handle.is_null() {
            return Err(AclError::Invalid("无法创建 ACL 事务锁"));
        }
        // SAFETY: handle 是 Mutex；等待最多 30 秒。
        let wait = unsafe { WaitForSingleObject(handle, 30_000) };
        if wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED {
            // SAFETY: handle 由 CreateMutexW 返回。
            unsafe { CloseHandle(handle) };
            return Err(AclError::Invalid("ACL 事务锁等待超时"));
        }
        Ok(Self { handle })
    }
}

impl Drop for AclMutex {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            // SAFETY: 当前线程成功获取 Mutex；随后关闭句柄。
            unsafe {
                ReleaseMutex(self.handle);
                CloseHandle(self.handle);
            }
        }
    }
}

pub fn list_ace_bytes(path: &Path) -> Result<Vec<Vec<u8>>, AclError> {
    read_aces(path)
}

fn verify_record_identity(entry: &AclEntryRecord) -> Result<ResolvedPath, AclError> {
    let target = resolve_local_ntfs(Path::new(&entry.canonical_path))
        .map_err(|_| AclError::Invalid("ACL 目标路径无效"))?;
    if target.identity.volume_serial != entry.volume_serial
        || target.identity.file_id != entry.file_id
    {
        return Err(AclError::Invalid("ACL 目标在授权后发生变化"));
    }
    Ok(target)
}

fn build_tagged_ace(entry: &AclEntryRecord) -> Result<Vec<u8>, AclError> {
    build_allow_ace(entry, 0, Some(entry.ace_tag.as_bytes()))
}

fn build_access_owner_ace(entry: &AclEntryRecord) -> Result<Vec<u8>, AclError> {
    let tag = access_owner_tag(entry);
    build_allow_ace(entry, 0, Some(tag.as_bytes()))
}

fn build_access_ace(entry: &AclEntryRecord) -> Result<Vec<u8>, AclError> {
    build_allow_ace(entry, entry.inheritance, None)
}

fn build_allow_ace(
    entry: &AclEntryRecord,
    flags: u8,
    application_data: Option<&[u8]>,
) -> Result<Vec<u8>, AclError> {
    let sid = StringSid::parse(&entry.sandbox_sid)?;
    // SAFETY: sid 由 ConvertStringSidToSidW 返回。
    let sid_length = unsafe { GetLengthSid(sid.0) } as usize;
    if sid_length == 0 {
        return Err(AclError::Invalid("ACL SID 长度无效"));
    }
    let unpadded = 8usize
        .checked_add(sid_length)
        .and_then(|value| value.checked_add(application_data.map_or(0, <[u8]>::len)))
        .ok_or(AclError::Invalid("ACL ACE 大小溢出"))?;
    let size = (unpadded + 3) & !3;
    if size > u16::MAX as usize {
        return Err(AclError::Invalid("ACL ACE 超过大小限制"));
    }
    let mut ace = vec![0u8; size];
    ace[0] = if application_data.is_some() {
        ACCESS_ALLOWED_CALLBACK_ACE_TYPE
    } else {
        ACCESS_ALLOWED_ACE_TYPE
    };
    ace[1] = flags;
    ace[2..4].copy_from_slice(&(size as u16).to_le_bytes());
    ace[4..8].copy_from_slice(&entry.rights.to_le_bytes());
    // SAFETY: SID 长度已由 GetLengthSid 验证，立即复制到 ACE 缓冲区。
    let sid_bytes = unsafe { std::slice::from_raw_parts(sid.0.cast::<u8>(), sid_length) };
    ace[8..8 + sid_length].copy_from_slice(sid_bytes);
    if let Some(application_data) = application_data {
        ace[8 + sid_length..unpadded].copy_from_slice(application_data);
    }
    Ok(ace)
}

fn count_matching_aces(aces: &[Vec<u8>], entry: &AclEntryRecord) -> Result<usize, AclError> {
    Ok(aces.iter().filter(|ace| ace_matches(ace, entry)).count())
}

fn ace_matches(ace: &[u8], entry: &AclEntryRecord) -> bool {
    let Some(application_data) =
        matching_application_data(ace, entry, ACCESS_ALLOWED_CALLBACK_ACE_TYPE, 0)
    else {
        return false;
    };
    application_data_matches_tag(application_data, entry.ace_tag.as_bytes())
}

fn count_access_owner_aces(aces: &[Vec<u8>], entry: &AclEntryRecord) -> usize {
    aces.iter()
        .filter(|ace| access_owner_ace_matches(ace, entry))
        .count()
}

fn access_owner_ace_matches(ace: &[u8], entry: &AclEntryRecord) -> bool {
    let Some(application_data) =
        matching_application_data(ace, entry, ACCESS_ALLOWED_CALLBACK_ACE_TYPE, 0)
    else {
        return false;
    };
    application_data_matches_tag(application_data, access_owner_tag(entry).as_bytes())
}

fn any_access_owner_ace_matches(ace: &[u8], entry: &AclEntryRecord) -> bool {
    let Some(application_data) =
        matching_application_data(ace, entry, ACCESS_ALLOWED_CALLBACK_ACE_TYPE, 0)
    else {
        return false;
    };
    let content_length = application_data
        .iter()
        .rposition(|byte| *byte != 0)
        .map_or(0, |index| index + 1);
    application_data[..content_length].starts_with(ACCESS_OWNER_TAG_PREFIX.as_bytes())
}

fn count_transaction_markers_for_access(aces: &[Vec<u8>], entry: &AclEntryRecord) -> usize {
    aces.iter()
        .filter(|ace| transaction_marker_matches_access(ace, entry))
        .count()
}

fn transaction_marker_matches_access(ace: &[u8], entry: &AclEntryRecord) -> bool {
    let Some(application_data) =
        matching_application_data(ace, entry, ACCESS_ALLOWED_CALLBACK_ACE_TYPE, 0)
    else {
        return false;
    };
    let content_length = application_data
        .iter()
        .rposition(|byte| *byte != 0)
        .map_or(0, |index| index + 1);
    application_data[..content_length].starts_with(ACE_TAG_PREFIX.as_bytes())
}

fn application_data_matches_tag(application_data: &[u8], tag: &[u8]) -> bool {
    application_data.starts_with(tag) && application_data[tag.len()..].iter().all(|byte| *byte == 0)
}

fn count_access_aces(aces: &[Vec<u8>], entry: &AclEntryRecord) -> usize {
    aces.iter()
        .filter(|ace| access_ace_matches(ace, entry))
        .count()
}

fn access_ace_matches(ace: &[u8], entry: &AclEntryRecord) -> bool {
    matching_application_data(ace, entry, ACCESS_ALLOWED_ACE_TYPE, entry.inheritance)
        .is_some_and(|application_data| application_data.iter().all(|byte| *byte == 0))
}

fn matching_application_data<'a>(
    ace: &'a [u8],
    entry: &AclEntryRecord,
    expected_type: u8,
    expected_flags: u8,
) -> Option<&'a [u8]> {
    if ace.len() < 8 || ace[0] != expected_type || ace[1] != expected_flags {
        return None;
    }
    if u32::from_le_bytes(ace[4..8].try_into().ok()?) != entry.rights {
        return None;
    }
    let sid_pointer = ace[8..].as_ptr().cast_mut().cast();
    // SAFETY: 后续长度边界会与 ACE 长度交叉验证；无效 SID 返回长度 0。
    let sid_length = unsafe { GetLengthSid(sid_pointer) } as usize;
    if sid_length == 0 || 8 + sid_length > ace.len() {
        return None;
    }
    let Ok(expected_sid) = StringSid::parse(&entry.sandbox_sid) else {
        return None;
    };
    // SAFETY: 两个 SID 均已通过 GetLengthSid/ConvertStringSidToSidW。
    if unsafe { windows_sys::Win32::Security::EqualSid(sid_pointer, expected_sid.0) } == 0 {
        return None;
    }
    Some(&ace[8 + sid_length..])
}

fn remove_transaction_ace_pair(
    aces: &mut Vec<Vec<u8>>,
    entry: &AclEntryRecord,
) -> Result<(), AclError> {
    let markers: Vec<usize> = aces
        .iter()
        .enumerate()
        .filter_map(|(index, ace)| ace_matches(ace, entry).then_some(index))
        .collect();
    if markers.len() != 1 {
        return Err(AclError::Invalid("无法唯一确认 ACL 事务标记"));
    }
    let marker = markers[0];
    let owner_matches: Vec<usize> = aces
        .iter()
        .enumerate()
        .filter_map(|(index, ace)| access_owner_ace_matches(ace, entry).then_some(index))
        .collect();
    if owner_matches.len() > 1 {
        return Err(AclError::Invalid("ACL 共享授权归属标记不唯一"));
    }
    let transaction_count = count_transaction_markers_for_access(aces, entry);
    if transaction_count == 0 {
        return Err(AclError::Invalid("ACL 事务引用计数无效"));
    }
    let mut removals = vec![marker];
    if transaction_count == 1
        && let Some(owner) = owner_matches.first().copied()
    {
        let access = owner
            .checked_add(1)
            .filter(|index| {
                aces.get(*index)
                    .is_some_and(|ace| access_ace_matches(ace, entry))
            })
            .ok_or(AclError::Invalid("ACL 归属标记与共享授权 ACE 不相邻"))?;
        removals.push(owner);
        removals.push(access);
    }
    removals.sort_unstable_by(|left, right| right.cmp(left));
    for index in removals {
        aces.remove(index);
    }
    if aces.iter().any(|ace| ace_matches(ace, entry)) {
        return Err(AclError::Invalid("ACL 事务标记删除失败"));
    }
    Ok(())
}

fn access_owner_tag(entry: &AclEntryRecord) -> String {
    format!(
        "{ACCESS_OWNER_TAG_PREFIX}{}",
        fingerprint(&[
            entry.canonical_path.as_bytes(),
            &entry.volume_serial.to_le_bytes(),
            &entry.file_id.to_le_bytes(),
            entry.sandbox_sid.as_bytes(),
            &entry.rights.to_le_bytes(),
            &[entry.inheritance],
        ])
    )
}

fn read_aces(path: &Path) -> Result<Vec<Vec<u8>>, AclError> {
    let path = wide_null_path(path)?;
    let mut dacl: *mut ACL = std::ptr::null_mut();
    let mut descriptor = std::ptr::null_mut();
    // SAFETY: path 有效；dacl 和 descriptor 接收由 LocalFree 管理的安全描述符内部指针。
    let result = unsafe {
        GetNamedSecurityInfoW(
            path.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut dacl,
            std::ptr::null_mut(),
            &mut descriptor,
        )
    };
    if result != 0 || descriptor.is_null() || dacl.is_null() {
        if !descriptor.is_null() {
            // SAFETY: descriptor 由 GetNamedSecurityInfoW 分配。
            unsafe { LocalFree(descriptor.cast()) };
        }
        return Err(AclError::Invalid("无法读取目标 DACL"));
    }
    let descriptor = LocalDescriptor(descriptor);
    let mut info = ACL_SIZE_INFORMATION::default();
    // SAFETY: dacl 位于 descriptor 内且两者在调用期间有效。
    if unsafe {
        GetAclInformation(
            dacl,
            (&mut info as *mut ACL_SIZE_INFORMATION).cast(),
            std::mem::size_of::<ACL_SIZE_INFORMATION>() as u32,
            AclSizeInformation,
        )
    } == 0
    {
        return Err(AclError::Invalid("无法读取 DACL 大小信息"));
    }
    let mut aces = Vec::with_capacity(info.AceCount as usize);
    for index in 0..info.AceCount {
        let mut pointer = std::ptr::null_mut();
        // SAFETY: index 小于 AceCount，pointer 接收 descriptor 内 ACE 地址。
        if unsafe { GetAce(dacl, index, &mut pointer) } == 0 || pointer.is_null() {
            return Err(AclError::Invalid("无法读取 DACL ACE"));
        }
        // SAFETY: 所有 ACE 均以 ACE_HEADER 开始。
        let header = unsafe { &*(pointer.cast::<ACE_HEADER>()) };
        let size = header.AceSize as usize;
        if size < std::mem::size_of::<ACE_HEADER>() {
            return Err(AclError::Invalid("DACL ACE 大小无效"));
        }
        // SAFETY: AceSize 是安全描述符内 ACE 的完整长度。
        aces.push(unsafe { std::slice::from_raw_parts(pointer.cast::<u8>(), size) }.to_vec());
    }
    drop(descriptor);
    Ok(aces)
}

fn write_aces(path: &Path, aces: &[Vec<u8>], propagate: bool) -> Result<(), AclError> {
    let total = std::mem::size_of::<ACL>()
        + aces
            .iter()
            .try_fold(0usize, |sum, ace| sum.checked_add(ace.len()))
            .ok_or(AclError::Invalid("DACL 大小溢出"))?;
    if total > u16::MAX as usize {
        return Err(AclError::Invalid("DACL 超过大小限制"));
    }
    let mut buffer = vec![0u8; total];
    let acl = buffer.as_mut_ptr().cast::<ACL>();
    // SAFETY: buffer 足以容纳 ACL 头和全部 ACE。
    if unsafe { InitializeAcl(acl, total as u32, ACL_REVISION_DS) } == 0 {
        return Err(AclError::Invalid("无法初始化新 DACL"));
    }
    for ace in aces {
        // SAFETY: ace 是从现有 DACL 复制或由本模块构造的完整 ACE。
        if unsafe {
            AddAce(
                acl,
                ACL_REVISION_DS,
                u32::MAX,
                ace.as_ptr().cast(),
                ace.len() as u32,
            )
        } == 0
        {
            return Err(AclError::Invalid("无法复制 DACL ACE"));
        }
    }
    let path = wide_null_path(path)?;
    if propagate {
        // SetNamedSecurityInfoW 会把目录上的可继承 ACE 自动传播到现有子项；
        // SetFileSecurityW 明确不会传播，因此仅保留给非树形目标。
        let result = unsafe {
            SetNamedSecurityInfoW(
                path.as_ptr(),
                SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                acl,
                std::ptr::null(),
            )
        };
        if result != 0 {
            return Err(AclError::Invalid("无法传播更新目标 DACL"));
        }
        return Ok(());
    }
    let mut descriptor = SECURITY_DESCRIPTOR::default();
    // SAFETY: descriptor 指针有效，revision 1 是公开 Win32 契约。
    if unsafe {
        InitializeSecurityDescriptor((&mut descriptor as *mut SECURITY_DESCRIPTOR).cast(), 1)
    } == 0
    {
        return Err(AclError::Invalid("无法初始化安全描述符"));
    }
    // SAFETY: descriptor 和 acl 在 SetFileSecurityW 返回前保持有效。
    if unsafe {
        SetSecurityDescriptorDacl(
            (&mut descriptor as *mut SECURITY_DESCRIPTOR).cast(),
            1,
            acl,
            0,
        )
    } == 0
    {
        return Err(AclError::Invalid("无法设置安全描述符 DACL"));
    }
    // SAFETY: path 是 NUL 结尾文件路径，descriptor 包含完整 DACL。
    if unsafe {
        SetFileSecurityW(
            path.as_ptr(),
            DACL_SECURITY_INFORMATION,
            (&mut descriptor as *mut SECURITY_DESCRIPTOR).cast(),
        )
    } == 0
    {
        return Err(AclError::Invalid("无法更新目标 DACL"));
    }
    Ok(())
}

fn wide_null_path(path: &Path) -> Result<Vec<u16>, AclError> {
    use std::os::windows::ffi::OsStrExt;
    let value: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
    if value.len() <= 1 || value[..value.len() - 1].contains(&0) {
        return Err(AclError::Invalid("ACL 路径无效"));
    }
    Ok(value)
}

struct StringSid(PSID);

impl StringSid {
    fn parse(value: &str) -> Result<Self, AclError> {
        if value.is_empty() || value.contains('\0') {
            return Err(AclError::Invalid("ACL SID 无效"));
        }
        let value: Vec<u16> = value.encode_utf16().chain([0]).collect();
        let mut sid = std::ptr::null_mut();
        // SAFETY: value 是 NUL 结尾 SID 字符串，sid 接收 LocalAlloc 指针。
        if unsafe { ConvertStringSidToSidW(value.as_ptr(), &mut sid) } == 0 || sid.is_null() {
            return Err(AclError::Invalid("ACL SID 无法解析"));
        }
        Ok(Self(sid))
    }
}

impl Drop for StringSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: 指针由 ConvertStringSidToSidW 分配。
            unsafe { LocalFree(self.0.cast()) };
        }
    }
}

struct LocalDescriptor(windows_sys::Win32::Security::PSECURITY_DESCRIPTOR);

impl Drop for LocalDescriptor {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: 指针由 GetNamedSecurityInfoW 分配。
            unsafe { LocalFree(self.0.cast()) };
        }
    }
}

fn entry_fingerprint(
    canonical_path: &str,
    volume_serial: u32,
    file_id: u64,
    sandbox_sid: &str,
    rights: u32,
    inheritance: u8,
    ace_tag: &str,
) -> String {
    fingerprint(&[
        canonical_path.as_bytes(),
        &volume_serial.to_le_bytes(),
        &file_id.to_le_bytes(),
        sandbox_sid.as_bytes(),
        &rights.to_le_bytes(),
        &[inheritance],
        ace_tag.as_bytes(),
    ])
}

fn new_transaction_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let counter = TRANSACTION_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("{:x}-{:x}-{:x}", std::process::id(), nanos, counter)
}

fn validate_id(value: &str) -> Result<(), AclError> {
    if value.is_empty()
        || value.len() > 256
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(AclError::Invalid("ACL 事务标识无效"));
    }
    Ok(())
}

fn fingerprint(parts: &[&[u8]]) -> String {
    let mut hash = 0xcbf29ce484222325u64;
    for part in parts {
        for byte in part.iter().copied().chain([0xff]) {
            hash ^= u64::from(byte);
            hash = hash.wrapping_mul(0x100000001b3);
        }
    }
    format!("{hash:016x}")
}

fn move_replace(source: &Path, destination: &Path) -> Result<(), AclError> {
    use std::os::windows::ffi::OsStrExt;
    let source: Vec<u16> = source.as_os_str().encode_wide().chain([0]).collect();
    let destination: Vec<u16> = destination.as_os_str().encode_wide().chain([0]).collect();
    // SAFETY: 两个路径均 NUL 结尾，临时文件与目标在同一目录。
    if unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    } == 0
    {
        return Err(AclError::Io(std::io::Error::last_os_error()));
    }
    Ok(())
}

#[derive(Debug)]
pub enum AclError {
    Io(std::io::Error),
    Invalid(&'static str),
}

impl std::fmt::Display for AclError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(_) => formatter.write_str("ACL 事务日志 I/O 失败"),
            Self::Invalid(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for AclError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn write_rights_support_atomic_replacement_without_full_control() {
        let file_rights = rights_for(GrantMode::Write, PathKind::File);
        let directory_rights = rights_for(GrantMode::Write, PathKind::Directory);

        assert_eq!(file_rights & DELETE, DELETE);
        assert_eq!(directory_rights & DELETE, DELETE);
        assert_eq!(directory_rights & FILE_DELETE_CHILD, FILE_DELETE_CHILD);
        assert_eq!(file_rights & FILE_DELETE_CHILD, 0);
    }
}
