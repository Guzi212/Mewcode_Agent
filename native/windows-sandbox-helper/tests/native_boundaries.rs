#![cfg(windows)]

use mewcode_windows_sandbox::appcontainer::AppContainerAttributes;
use mewcode_windows_sandbox::job::Job;
use std::ptr::null;
use windows_sys::Win32::Foundation::CloseHandle;
use windows_sys::Win32::System::Threading::CreateEventW;

#[test]
fn appcontainer_attributes_have_no_network_capabilities() {
    // SAFETY: 创建一个仅供属性列表边界测试使用的本地事件句柄。
    let handle = unsafe { CreateEventW(null(), 1, 0, null()) };
    assert!(!handle.is_null());
    let attributes =
        AppContainerAttributes::new("MewCode.Sandbox.0000000000000000", &[handle]).unwrap();
    assert!(!attributes.as_ptr().is_null());
    assert_eq!(attributes.capability_count(), 0);
    assert_eq!(attributes.inherited_handle_count(), 1);
    drop(attributes);
    // SAFETY: handle 由本测试创建且只关闭一次。
    unsafe { CloseHandle(handle) };
}

#[test]
fn empty_job_is_queryable_and_close_is_idempotent() {
    let mut job = Job::new().unwrap();
    assert_eq!(job.active_processes().unwrap(), 0);
    job.close();
    job.close();
}
