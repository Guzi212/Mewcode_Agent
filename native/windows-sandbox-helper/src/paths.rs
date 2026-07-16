use std::mem::zeroed;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::fs::MetadataExt;
use std::path::{Component, Path, PathBuf, Prefix};
use std::ptr::{null, null_mut};
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::Storage::FileSystem::{
    BY_HANDLE_FILE_INFORMATION, CreateFileW, FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_SHARE_DELETE, FILE_SHARE_READ,
    FILE_SHARE_WRITE, GetDriveTypeW, GetFileInformationByHandle, GetFinalPathNameByHandleW,
    GetVolumeInformationW, GetVolumePathNameW, OPEN_EXISTING, VOLUME_NAME_DOS,
};
use windows_sys::Win32::System::WindowsProgramming::DRIVE_FIXED;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathKind {
    File,
    Directory,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileIdentity {
    pub volume_serial: u32,
    pub file_id: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedPath {
    pub canonical_path: PathBuf,
    pub kind: PathKind,
    pub identity: FileIdentity,
}

pub fn resolve_local_ntfs(path: &Path) -> Result<ResolvedPath, PathPolicyError> {
    validate_input_path(path)?;
    reject_reparse_chain(path)?;
    let handle = open_metadata_handle(path)?;
    let canonical_path = final_dos_path(handle.0)?;
    validate_input_path(&canonical_path)?;
    reject_reparse_chain(&canonical_path)?;
    validate_fixed_ntfs(&canonical_path)?;
    let metadata = std::fs::symlink_metadata(&canonical_path)
        .map_err(|_| PathPolicyError::Invalid("目标路径不存在"))?;
    let kind = if metadata.is_file() {
        PathKind::File
    } else if metadata.is_dir() {
        PathKind::Directory
    } else {
        return Err(PathPolicyError::Invalid("目标不是普通文件或目录"));
    };
    let identity = file_identity(handle.0)?;
    Ok(ResolvedPath {
        canonical_path,
        kind,
        identity,
    })
}

pub fn verify_unchanged(expected: &ResolvedPath) -> Result<(), PathPolicyError> {
    let current = resolve_local_ntfs(&expected.canonical_path)?;
    if current.identity != expected.identity || current.kind != expected.kind {
        return Err(PathPolicyError::Changed);
    }
    Ok(())
}

pub fn paths_equal(left: &Path, right: &Path) -> bool {
    left.as_os_str()
        .to_string_lossy()
        .eq_ignore_ascii_case(&right.as_os_str().to_string_lossy())
}

fn validate_input_path(path: &Path) -> Result<(), PathPolicyError> {
    if path.as_os_str().is_empty() || path.as_os_str().encode_wide().any(|value| value == 0) {
        return Err(PathPolicyError::Invalid("路径为空或包含 NUL"));
    }
    if !path.is_absolute() {
        return Err(PathPolicyError::Invalid("路径必须是绝对路径"));
    }
    let prefix = match path.components().next() {
        Some(Component::Prefix(prefix)) => prefix.kind(),
        _ => return Err(PathPolicyError::Invalid("路径缺少本地盘符")),
    };
    if !matches!(prefix, Prefix::Disk(_)) {
        return Err(PathPolicyError::Invalid("不允许 UNC、设备或网络路径"));
    }
    Ok(())
}

fn reject_reparse_chain(path: &Path) -> Result<(), PathPolicyError> {
    for ancestor in path.ancestors() {
        let metadata = std::fs::symlink_metadata(ancestor)
            .map_err(|_| PathPolicyError::Invalid("路径祖先不存在"))?;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(PathPolicyError::ReparsePoint);
        }
    }
    Ok(())
}

fn open_metadata_handle(path: &Path) -> Result<OwnedHandle, PathPolicyError> {
    let path = wide_null(path)?;
    // SAFETY: path 是有效 NUL 结尾字符串；返回句柄由 OwnedHandle 关闭。
    let handle = unsafe {
        CreateFileW(
            path.as_ptr(),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            null(),
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err(PathPolicyError::Invalid("无法打开目标路径"));
    }
    Ok(OwnedHandle(handle))
}

fn final_dos_path(handle: HANDLE) -> Result<PathBuf, PathPolicyError> {
    // SAFETY: 句柄有效，首次调用查询所需缓冲区长度。
    let required = unsafe { GetFinalPathNameByHandleW(handle, null_mut(), 0, VOLUME_NAME_DOS) };
    if required == 0 || required > 32768 {
        return Err(PathPolicyError::Windows("无法解析最终路径"));
    }
    let mut buffer = vec![0u16; required as usize + 1];
    // SAFETY: buffer 足以容纳查询结果。
    let written = unsafe {
        GetFinalPathNameByHandleW(
            handle,
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            VOLUME_NAME_DOS,
        )
    };
    if written == 0 || written as usize >= buffer.len() {
        return Err(PathPolicyError::Windows("无法读取最终路径"));
    }
    let mut value = String::from_utf16(&buffer[..written as usize])
        .map_err(|_| PathPolicyError::Windows("最终路径编码无效"))?;
    if let Some(stripped) = value.strip_prefix(r"\\?\") {
        value = stripped.to_owned();
    }
    Ok(PathBuf::from(value))
}

fn validate_fixed_ntfs(path: &Path) -> Result<(), PathPolicyError> {
    let path_wide = wide_null(path)?;
    let mut volume_root = vec![0u16; 32768];
    // SAFETY: 输入路径及输出缓冲区有效。
    if unsafe {
        GetVolumePathNameW(
            path_wide.as_ptr(),
            volume_root.as_mut_ptr(),
            volume_root.len() as u32,
        )
    } == 0
    {
        return Err(PathPolicyError::Windows("无法解析路径卷"));
    }
    let root_length = volume_root
        .iter()
        .position(|value| *value == 0)
        .ok_or(PathPolicyError::Windows("卷根路径无 NUL 终止符"))?;
    volume_root.truncate(root_length + 1);
    // SAFETY: volume_root 是 GetVolumePathNameW 返回的 NUL 结尾根路径。
    if unsafe { GetDriveTypeW(volume_root.as_ptr()) } != DRIVE_FIXED {
        return Err(PathPolicyError::Invalid("只允许本地固定磁盘"));
    }
    let mut filesystem = vec![0u16; 32];
    // SAFETY: 所有可选输出为 null，文件系统名缓冲区有效。
    if unsafe {
        GetVolumeInformationW(
            volume_root.as_ptr(),
            null_mut(),
            0,
            null_mut(),
            null_mut(),
            null_mut(),
            filesystem.as_mut_ptr(),
            filesystem.len() as u32,
        )
    } == 0
    {
        return Err(PathPolicyError::Windows("无法读取文件系统类型"));
    }
    let length = filesystem.iter().position(|value| *value == 0).unwrap_or(0);
    let filesystem = String::from_utf16_lossy(&filesystem[..length]);
    if !filesystem.eq_ignore_ascii_case("NTFS") {
        return Err(PathPolicyError::Invalid("只允许 NTFS 文件系统"));
    }
    Ok(())
}

fn file_identity(handle: HANDLE) -> Result<FileIdentity, PathPolicyError> {
    // SAFETY: 零初始化是 BY_HANDLE_FILE_INFORMATION 的有效初始状态。
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { zeroed() };
    // SAFETY: handle 有效，info 指针可写。
    if unsafe { GetFileInformationByHandle(handle, &mut info) } == 0 {
        return Err(PathPolicyError::Windows("无法读取文件标识"));
    }
    Ok(FileIdentity {
        volume_serial: info.dwVolumeSerialNumber,
        file_id: (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
    })
}

fn wide_null(path: &Path) -> Result<Vec<u16>, PathPolicyError> {
    let value: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
    if value.len() <= 1 || value[..value.len() - 1].contains(&0) {
        return Err(PathPolicyError::Invalid("路径编码无效"));
    }
    Ok(value)
}

struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if self.0 != INVALID_HANDLE_VALUE && !self.0.is_null() {
            // SAFETY: 句柄由 CreateFileW 成功返回且仅关闭一次。
            unsafe { CloseHandle(self.0) };
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum PathPolicyError {
    Invalid(&'static str),
    Windows(&'static str),
    ReparsePoint,
    Changed,
}

impl std::fmt::Display for PathPolicyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) | Self::Windows(message) => formatter.write_str(message),
            Self::ReparsePoint => formatter.write_str("路径包含重解析点"),
            Self::Changed => formatter.write_str("路径在授权后发生变化"),
        }
    }
}

impl std::error::Error for PathPolicyError {}
