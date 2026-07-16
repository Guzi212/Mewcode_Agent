use crate::state::{
    SandboxSetupState, StatePaths, load_state, prepare_state_root, write_state_atomic,
};
use std::ptr::null_mut;
use windows_sys::Win32::Foundation::{E_ACCESSDENIED, LocalFree};
use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
use windows_sys::Win32::Security::Isolation::{
    CreateAppContainerProfile, DeleteAppContainerProfile,
    DeriveAppContainerSidFromAppContainerName, GetAppContainerFolderPath,
};
use windows_sys::Win32::Security::{EqualSid, FreeSid, PSID};
use windows_sys::Win32::System::Com::CoTaskMemFree;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProfileStatus {
    Missing,
    Matching { sid: String },
    Mismatch,
}

pub fn query_profile(state: Option<&SandboxSetupState>) -> Result<ProfileStatus, ProfileError> {
    let Some(state) = state else {
        return Ok(ProfileStatus::Missing);
    };
    let derived = derive_profile_sid(&state.appcontainer_profile_name)?;
    let derived_text = derived.to_sid_string()?;
    if derived_text != state.appcontainer_sid || !profile_folder_exists(&derived_text)? {
        return Ok(ProfileStatus::Mismatch);
    }
    Ok(ProfileStatus::Matching { sid: derived_text })
}

pub fn prepare_profile(
    owner: &crate::user_sid::UserSid,
    paths: &StatePaths,
) -> Result<SandboxSetupState, ProfileError> {
    if paths.state_file.exists() {
        let state = load_state(paths, owner).map_err(|_| ProfileError::StateInvalid)?;
        return match query_profile(Some(&state))? {
            ProfileStatus::Matching { .. } => Ok(state),
            ProfileStatus::Missing | ProfileStatus::Mismatch => Err(ProfileError::StateInvalid),
        };
    }
    prepare_state_root(paths, owner).map_err(|_| ProfileError::StateInvalid)?;

    let owner_text = owner
        .to_sid_string()
        .map_err(|_| ProfileError::Windows("无法读取当前用户标识"))?;
    let install_id = crate::state::derive_install_id(&owner_text);
    let name = crate::state::profile_name(&install_id);
    let name_wide = wide_null(&name)?;
    let display_name = wide_null("MewCode Sandbox")?;
    let description = wide_null("MewCode Windows tool sandbox")?;
    let mut created_sid = null_mut();
    // SAFETY: 所有字符串均合法且 NUL 结尾；不请求 capabilities；created_sid 由 FreeSid 释放。
    let result = unsafe {
        CreateAppContainerProfile(
            name_wide.as_ptr(),
            display_name.as_ptr(),
            description.as_ptr(),
            std::ptr::null(),
            0,
            &mut created_sid,
        )
    };
    const ALREADY_EXISTS: i32 = 0x8007_00b7u32 as i32;
    if result == ALREADY_EXISTS {
        return Err(ProfileError::ExistingWithoutState);
    }
    if result == E_ACCESSDENIED {
        return Err(ProfileError::AccessDenied);
    }
    if result < 0 || created_sid.is_null() {
        return Err(ProfileError::Windows("无法创建 AppContainer profile"));
    }
    let created = OwnedSid(created_sid);
    let derived = derive_profile_sid(&name)?;
    if !created.equals(&derived) {
        return Err(ProfileError::Windows("AppContainer SID 复检失败"));
    }
    let state = SandboxSetupState::prepared(owner, created.to_sid_string()?)
        .map_err(|_| ProfileError::StateInvalid)?;
    if write_state_atomic(paths, owner, &state).is_err() {
        // SAFETY: name_wide 是本函数刚创建的 profile 名；失败时只回滚本次创建。
        let rollback = unsafe { DeleteAppContainerProfile(name_wide.as_ptr()) };
        if rollback < 0 {
            return Err(ProfileError::RollbackFailed);
        }
        return Err(ProfileError::StateInvalid);
    }
    let verified = load_state(paths, owner).map_err(|_| ProfileError::StateInvalid)?;
    match query_profile(Some(&verified))? {
        ProfileStatus::Matching { .. } => Ok(verified),
        ProfileStatus::Missing | ProfileStatus::Mismatch => Err(ProfileError::StateInvalid),
    }
}

fn profile_folder_exists(sid: &str) -> Result<bool, ProfileError> {
    match profile_folder_path(sid) {
        Ok(path) => Ok(path.is_dir()),
        Err(ProfileError::NotFound) => Ok(false),
        Err(error) => Err(error),
    }
}

pub fn profile_folder_path(sid: &str) -> Result<std::path::PathBuf, ProfileError> {
    use std::os::windows::ffi::OsStringExt;
    let sid = wide_null(sid)?;
    let mut path = null_mut();
    // SAFETY: sid 是有效 NUL 结尾字符串，path 接收由 CoTaskMemFree 释放的字符串。
    let result = unsafe { GetAppContainerFolderPath(sid.as_ptr(), &mut path) };
    const HRESULT_FILE_NOT_FOUND: i32 = 0x80070002_u32 as i32;
    if result == HRESULT_FILE_NOT_FOUND {
        return Err(ProfileError::NotFound);
    }
    if result < 0 || path.is_null() {
        return Err(ProfileError::Windows("无法定位 AppContainer profile 目录"));
    }
    let path = CoTaskString(path);
    let mut length = 0usize;
    // SAFETY: Windows 保证成功结果为 NUL 结尾字符串。
    while unsafe { *path.0.add(length) } != 0 {
        length += 1;
    }
    // SAFETY: 已找到 NUL，切片位于返回的分配块内。
    let value = unsafe { std::slice::from_raw_parts(path.0, length) };
    Ok(std::path::PathBuf::from(std::ffi::OsString::from_wide(
        value,
    )))
}

pub fn derive_profile_sid(profile_name: &str) -> Result<OwnedSid, ProfileError> {
    validate_profile_name(profile_name)?;
    let name = wide_null(profile_name)?;
    let mut sid = null_mut();
    // SAFETY: name 是合法 NUL 结尾字符串，sid 接收由 FreeSid 释放的指针。
    let result = unsafe { DeriveAppContainerSidFromAppContainerName(name.as_ptr(), &mut sid) };
    if result < 0 || sid.is_null() {
        return Err(ProfileError::Windows("无法查询 AppContainer profile SID"));
    }
    Ok(OwnedSid(sid))
}

fn validate_profile_name(name: &str) -> Result<(), ProfileError> {
    if name.is_empty()
        || name.len() > 64
        || !name
            .chars()
            .all(|value| value.is_ascii_alphanumeric() || "-_. ".contains(value))
    {
        return Err(ProfileError::Invalid("AppContainer profile 名称无效"));
    }
    Ok(())
}

fn wide_null(value: &str) -> Result<Vec<u16>, ProfileError> {
    if value.contains('\0') {
        return Err(ProfileError::Invalid("AppContainer profile 名称包含 NUL"));
    }
    Ok(value.encode_utf16().chain([0]).collect())
}

#[derive(Debug)]
pub struct OwnedSid(PSID);

impl OwnedSid {
    pub fn as_psid(&self) -> PSID {
        self.0
    }

    fn equals(&self, other: &Self) -> bool {
        // SAFETY: 两个 SID 都由受检查的 Windows API 返回并由各自对象持有。
        unsafe { EqualSid(self.0, other.0) != 0 }
    }

    pub fn to_sid_string(&self) -> Result<String, ProfileError> {
        let mut value = null_mut();
        // SAFETY: self 持有有效 SID，value 接收 LocalAlloc 分配的字符串。
        if unsafe { ConvertSidToStringSidW(self.0, &mut value) } == 0 || value.is_null() {
            return Err(ProfileError::Windows("无法格式化 AppContainer SID"));
        }
        let local = LocalString(value);
        let mut length = 0usize;
        // SAFETY: Windows 保证返回 NUL 结尾字符串。
        while unsafe { *local.0.add(length) } != 0 {
            length += 1;
        }
        // SAFETY: 已定位 NUL，切片位于分配的字符串内。
        let text = unsafe { std::slice::from_raw_parts(local.0, length) };
        String::from_utf16(text).map_err(|_| ProfileError::Windows("AppContainer SID 编码无效"))
    }
}

impl Drop for OwnedSid {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: 指针由 Derive/Create AppContainer API 返回且仅释放一次。
            unsafe { FreeSid(self.0) };
        }
    }
}

struct LocalString(*mut u16);

impl Drop for LocalString {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: 指针由 ConvertSidToStringSidW 使用 LocalAlloc 分配。
            unsafe { LocalFree(self.0.cast()) };
        }
    }
}

struct CoTaskString(*mut u16);

impl Drop for CoTaskString {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: 指针由 GetAppContainerFolderPath 使用 COM 分配器返回。
            unsafe { CoTaskMemFree(self.0.cast()) };
        }
    }
}

#[derive(Debug)]
pub enum ProfileError {
    Invalid(&'static str),
    Windows(&'static str),
    NotFound,
    AccessDenied,
    ExistingWithoutState,
    RollbackFailed,
    StateInvalid,
}

impl std::fmt::Display for ProfileError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) | Self::Windows(message) => formatter.write_str(message),
            Self::NotFound => formatter.write_str("AppContainer profile 不存在"),
            Self::AccessDenied => formatter.write_str("系统策略拒绝创建 AppContainer profile"),
            Self::RollbackFailed => formatter.write_str("AppContainer profile 回滚失败"),
            Self::ExistingWithoutState => {
                formatter.write_str("存在无法确认所有权的同名 AppContainer profile")
            }
            Self::StateInvalid => formatter.write_str("AppContainer 准备状态无效"),
        }
    }
}

impl std::error::Error for ProfileError {}
