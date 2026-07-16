use crate::protocol::HELPER_VERSION;
use crate::user_sid::UserSid;
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use windows_sys::Win32::Security::{
    GetFileSecurityW, GetSecurityDescriptorOwner, OWNER_SECURITY_INFORMATION, PSID,
};
use windows_sys::Win32::Storage::FileSystem::{
    MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
};

pub const STATE_SCHEMA_VERSION: u32 = 1;
pub const PRODUCT_ID: &str = "dev.mewcode.windows-sandbox";
const MAX_STATE_BYTES: u64 = 64 * 1024;
static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SandboxSetupState {
    pub schema_version: u32,
    pub install_id: String,
    pub owner_user_sid: String,
    pub appcontainer_profile_name: String,
    pub appcontainer_sid: String,
    pub helper_version: String,
    pub ready: bool,
}

impl SandboxSetupState {
    pub fn prepared(owner: &UserSid, appcontainer_sid: String) -> Result<Self, StateError> {
        let owner_user_sid = owner
            .to_sid_string()
            .map_err(|_| StateError::Invalid("无法读取当前用户标识"))?;
        let install_id = derive_install_id(&owner_user_sid);
        Ok(Self {
            schema_version: STATE_SCHEMA_VERSION,
            appcontainer_profile_name: profile_name(&install_id),
            install_id,
            owner_user_sid,
            appcontainer_sid,
            helper_version: HELPER_VERSION.to_owned(),
            ready: true,
        })
    }
}

#[derive(Debug, Clone)]
pub struct StatePaths {
    pub root: PathBuf,
    pub state_file: PathBuf,
}

impl StatePaths {
    pub fn for_current_user(owner: &UserSid) -> Result<Self, StateError> {
        let local_app_data = std::env::var_os("LOCALAPPDATA")
            .filter(|value| !value.is_empty())
            .ok_or(StateError::Invalid("LOCALAPPDATA 不可用"))?;
        let owner_sid = owner
            .to_sid_string()
            .map_err(|_| StateError::Invalid("无法读取当前用户标识"))?;
        Ok(Self::under(
            PathBuf::from(local_app_data)
                .join("MewCode")
                .join("sandbox"),
            &derive_install_id(&owner_sid),
        ))
    }

    pub fn under(base: PathBuf, install_id: &str) -> Self {
        let root = base.join(install_id);
        let state_file = root.join("state.json");
        Self { root, state_file }
    }
}

pub fn prepare_state_root(paths: &StatePaths, owner: &UserSid) -> Result<(), StateError> {
    fs::create_dir_all(&paths.root).map_err(StateError::Io)?;
    verify_file_owner(&paths.root, owner)?;
    let suffix = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let probe = paths.root.join(format!(
        ".setup-probe.{}.{}.tmp",
        std::process::id(),
        suffix
    ));
    let result = (|| {
        let mut stream = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&probe)
            .map_err(StateError::Io)?;
        stream
            .write_all(b"mewcode-state-probe")
            .map_err(StateError::Io)?;
        stream.sync_all().map_err(StateError::Io)?;
        drop(stream);
        verify_file_owner(&probe, owner)?;
        fs::remove_file(&probe).map_err(StateError::Io)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&probe);
    }
    result
}

pub fn load_state(paths: &StatePaths, owner: &UserSid) -> Result<SandboxSetupState, StateError> {
    verify_file_owner(&paths.state_file, owner)?;
    let metadata = fs::metadata(&paths.state_file).map_err(StateError::Io)?;
    if !metadata.is_file() || metadata.len() > MAX_STATE_BYTES {
        return Err(StateError::Invalid("状态文件类型或大小无效"));
    }
    let bytes = fs::read(&paths.state_file).map_err(StateError::Io)?;
    let state: SandboxSetupState =
        serde_json::from_slice(&bytes).map_err(|_| StateError::Invalid("状态文件已损坏"))?;
    validate_state(&state, owner)?;
    Ok(state)
}

pub fn write_state_atomic(
    paths: &StatePaths,
    owner: &UserSid,
    state: &SandboxSetupState,
) -> Result<(), StateError> {
    validate_state(state, owner)?;
    fs::create_dir_all(&paths.root).map_err(StateError::Io)?;
    let suffix = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temporary = paths
        .root
        .join(format!(".state.{}.{}.tmp", std::process::id(), suffix));
    let result = (|| {
        let bytes = serde_json::to_vec(state).map_err(|_| StateError::Invalid("状态无法序列化"))?;
        let mut stream = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)
            .map_err(StateError::Io)?;
        stream.write_all(&bytes).map_err(StateError::Io)?;
        stream.sync_all().map_err(StateError::Io)?;
        drop(stream);
        move_replace(&temporary, &paths.state_file)?;
        verify_file_owner(&paths.state_file, owner)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

pub fn derive_install_id(owner_user_sid: &str) -> String {
    let mut hash = 0xcbf29ce484222325u64;
    for byte in PRODUCT_ID
        .bytes()
        .chain([0xff])
        .chain(owner_user_sid.bytes())
    {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

pub fn profile_name(install_id: &str) -> String {
    format!("MewCode.Sandbox.{install_id}")
}

fn validate_state(state: &SandboxSetupState, owner: &UserSid) -> Result<(), StateError> {
    if state.schema_version != STATE_SCHEMA_VERSION {
        return Err(StateError::Invalid("状态 schema 版本不受支持"));
    }
    let owner_sid = owner
        .to_sid_string()
        .map_err(|_| StateError::Invalid("无法读取当前用户标识"))?;
    let install_id = derive_install_id(&owner_sid);
    if state.owner_user_sid != owner_sid || state.install_id != install_id {
        return Err(StateError::Invalid("状态所有者不匹配"));
    }
    if state.appcontainer_profile_name != profile_name(&install_id)
        || state.appcontainer_sid.is_empty()
        || state.appcontainer_sid.contains('\0')
        || state.helper_version.is_empty()
        || !state.ready
    {
        return Err(StateError::Invalid("状态内容不一致"));
    }
    Ok(())
}

fn verify_file_owner(path: &Path, owner: &UserSid) -> Result<(), StateError> {
    let path = wide_null(path)?;
    let mut required = 0u32;
    // SAFETY: 第一次调用仅查询缓冲区长度。
    unsafe {
        GetFileSecurityW(
            path.as_ptr(),
            OWNER_SECURITY_INFORMATION,
            std::ptr::null_mut(),
            0,
            &mut required,
        )
    };
    if required == 0 {
        return Err(StateError::Invalid("无法读取状态文件所有者"));
    }
    let mut descriptor = vec![0u8; required as usize];
    // SAFETY: descriptor 长度来自 Win32 查询调用。
    if unsafe {
        GetFileSecurityW(
            path.as_ptr(),
            OWNER_SECURITY_INFORMATION,
            descriptor.as_mut_ptr().cast(),
            required,
            &mut required,
        )
    } == 0
    {
        return Err(StateError::Invalid("无法读取状态文件所有者"));
    }
    let mut file_owner: PSID = std::ptr::null_mut();
    let mut defaulted = 0;
    // SAFETY: descriptor 是成功读取的自相对安全描述符，输出指针仅在缓冲区存活期间使用。
    if unsafe {
        GetSecurityDescriptorOwner(
            descriptor.as_mut_ptr().cast(),
            &mut file_owner,
            &mut defaulted,
        )
    } == 0
        || !owner.equals(file_owner)
    {
        return Err(StateError::Invalid("状态文件所有者不匹配"));
    }
    Ok(())
}

fn move_replace(source: &Path, destination: &Path) -> Result<(), StateError> {
    let source = wide_null(source)?;
    let destination = wide_null(destination)?;
    // SAFETY: 两个字符串均为有效 NUL 结尾路径，临时文件与目标位于同一目录。
    if unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    } == 0
    {
        return Err(StateError::Io(std::io::Error::last_os_error()));
    }
    Ok(())
}

fn wide_null(path: &Path) -> Result<Vec<u16>, StateError> {
    use std::os::windows::ffi::OsStrExt;
    let value: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
    if value.len() <= 1 || value[..value.len() - 1].contains(&0) {
        return Err(StateError::Invalid("状态路径无效"));
    }
    Ok(value)
}

#[derive(Debug)]
pub enum StateError {
    Io(std::io::Error),
    Invalid(&'static str),
}

impl std::fmt::Display for StateError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(_) => formatter.write_str("状态文件 I/O 失败"),
            Self::Invalid(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for StateError {}
