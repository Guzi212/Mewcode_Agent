use std::ffi::c_void;
use std::ptr::null_mut;
use windows_sys::Win32::Foundation::{
    CloseHandle, ERROR_INSUFFICIENT_BUFFER, GetLastError, HANDLE, LocalFree,
};
use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
use windows_sys::Win32::Security::{
    EqualSid, GetLengthSid, GetTokenInformation, PSID, TOKEN_QUERY, TOKEN_USER, TokenUser,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

#[derive(Clone, Debug)]
pub struct UserSid {
    bytes: Vec<u8>,
}

impl UserSid {
    pub fn current() -> Result<Self, SidError> {
        let mut token: HANDLE = null_mut();
        // SAFETY: GetCurrentProcess 返回伪句柄，token 指针指向有效可写 HANDLE。
        if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } == 0 {
            return Err(SidError::Windows("无法打开当前进程令牌"));
        }
        let token = OwnedHandle(token);
        let mut required = 0u32;
        // SAFETY: 首次调用按 Win32 契约仅查询所需缓冲区长度。
        let first =
            unsafe { GetTokenInformation(token.0, TokenUser, null_mut(), 0, &mut required) };
        // SAFETY: GetLastError 不要求额外前置条件。
        let error = unsafe { GetLastError() };
        if first != 0 || error != ERROR_INSUFFICIENT_BUFFER || required == 0 {
            return Err(SidError::Windows("无法查询当前用户 SID 长度"));
        }
        let mut token_buffer = vec![0u8; required as usize];
        // SAFETY: 缓冲区长度来自上一调用，指针在调用期间有效。
        if unsafe {
            GetTokenInformation(
                token.0,
                TokenUser,
                token_buffer.as_mut_ptr().cast(),
                required,
                &mut required,
            )
        } == 0
        {
            return Err(SidError::Windows("无法读取当前用户 SID"));
        }
        // SAFETY: TOKEN_USER 是 GetTokenInformation(TokenUser) 返回缓冲区的首部。
        let token_user = unsafe { &*(token_buffer.as_ptr().cast::<TOKEN_USER>()) };
        let sid = token_user.User.Sid;
        if sid.is_null() {
            return Err(SidError::Windows("当前用户 SID 为空"));
        }
        // SAFETY: sid 指向 token_buffer 内由 Windows 返回的有效 SID。
        let length = unsafe { GetLengthSid(sid) } as usize;
        if length == 0 || length > required as usize {
            return Err(SidError::Windows("当前用户 SID 长度无效"));
        }
        // SAFETY: SID 有效长度由 GetLengthSid 返回，立即复制到自有缓冲区。
        let bytes = unsafe { std::slice::from_raw_parts(sid.cast::<u8>(), length) }.to_vec();
        Ok(Self { bytes })
    }

    pub fn as_psid(&self) -> PSID {
        self.bytes.as_ptr().cast_mut().cast::<c_void>()
    }

    pub(crate) fn equals(&self, other: PSID) -> bool {
        if other.is_null() {
            return false;
        }
        // SAFETY: self 持有有效 SID；调用方只能传入 Windows 返回的 SID 指针。
        unsafe { EqualSid(self.as_psid(), other) != 0 }
    }

    pub fn to_sid_string(&self) -> Result<String, SidError> {
        let mut value = null_mut();
        // SAFETY: self SID 有效，value 接收 LocalAlloc 分配的 NUL 结尾字符串。
        if unsafe { ConvertSidToStringSidW(self.as_psid(), &mut value) } == 0 || value.is_null() {
            return Err(SidError::Windows("无法格式化当前用户 SID"));
        }
        let local = LocalString(value);
        let mut length = 0usize;
        // SAFETY: ConvertSidToStringSidW 保证返回 NUL 结尾字符串。
        while unsafe { *local.0.add(length) } != 0 {
            length += 1;
        }
        // SAFETY: 已扫描到 NUL，区间内均属于分配的字符串。
        let text = unsafe { std::slice::from_raw_parts(local.0, length) };
        String::from_utf16(text).map_err(|_| SidError::Windows("当前用户 SID 编码无效"))
    }
}

struct OwnedHandle(HANDLE);

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: 句柄由 OpenProcessToken 成功返回且只在此处关闭一次。
            unsafe { CloseHandle(self.0) };
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

#[derive(Debug)]
pub enum SidError {
    Windows(&'static str),
}

impl std::fmt::Display for SidError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Windows(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for SidError {}
