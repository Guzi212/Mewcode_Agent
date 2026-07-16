use crate::profile::{OwnedSid, derive_profile_sid};
use std::ffi::c_void;
use std::mem::{size_of, size_of_val};
use std::ptr::{null, null_mut};
use windows_sys::Win32::Foundation::HANDLE;
use windows_sys::Win32::Security::{PSID, SECURITY_CAPABILITIES};
use windows_sys::Win32::System::Memory::{GetProcessHeap, HeapAlloc, HeapFree};
use windows_sys::Win32::System::Threading::{
    DeleteProcThreadAttributeList, InitializeProcThreadAttributeList, LPPROC_THREAD_ATTRIBUTE_LIST,
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
    UpdateProcThreadAttribute,
};

pub struct AppContainerAttributes {
    list: LPPROC_THREAD_ATTRIBUTE_LIST,
    heap: HANDLE,
    capabilities: Box<SECURITY_CAPABILITIES>,
    handles: Box<[HANDLE]>,
    _sid: OwnedSid,
}

impl AppContainerAttributes {
    pub fn new(
        profile_name: &str,
        inherited_handles: &[HANDLE],
    ) -> Result<Self, AppContainerError> {
        if inherited_handles.is_empty() || inherited_handles.iter().any(|handle| handle.is_null()) {
            return Err(AppContainerError("子进程句柄列表不能为空或包含空句柄"));
        }

        let sid = derive_profile_sid(profile_name)
            .map_err(|_| AppContainerError("无法解析 AppContainer SID"))?;
        let mut capabilities = Box::new(SECURITY_CAPABILITIES {
            AppContainerSid: sid.as_psid(),
            Capabilities: null_mut(),
            CapabilityCount: 0,
            Reserved: 0,
        });
        let mut handles = inherited_handles.to_vec().into_boxed_slice();

        let mut bytes = 0usize;
        // SAFETY: 第一次调用只查询两个属性所需的缓冲区长度。
        unsafe { InitializeProcThreadAttributeList(null_mut(), 2, 0, &mut bytes) };
        if bytes == 0 {
            return Err(AppContainerError("无法计算启动属性长度"));
        }

        // SAFETY: GetProcessHeap 返回当前进程的默认堆句柄。
        let heap = unsafe { GetProcessHeap() };
        if heap.is_null() {
            return Err(AppContainerError("无法访问进程堆"));
        }

        // SAFETY: 从进程堆分配已查询的字节数。
        let list: LPPROC_THREAD_ATTRIBUTE_LIST = unsafe { HeapAlloc(heap, 0, bytes) }.cast();
        if list.is_null() {
            return Err(AppContainerError("无法分配启动属性"));
        }

        // SAFETY: list 指向足够大的可写缓冲区。
        if unsafe { InitializeProcThreadAttributeList(list, 2, 0, &mut bytes) } == 0 {
            // SAFETY: list 来自同一进程堆。
            unsafe { HeapFree(heap, 0, list.cast()) };
            return Err(AppContainerError("无法初始化启动属性"));
        }

        // SAFETY: capabilities 位于 Box 中，在 Self 的生命周期内地址稳定。
        if unsafe {
            UpdateProcThreadAttribute(
                list,
                0,
                PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES as usize,
                (&mut *capabilities as *mut SECURITY_CAPABILITIES).cast::<c_void>(),
                size_of::<SECURITY_CAPABILITIES>(),
                null_mut(),
                null(),
            )
        } == 0
        {
            cleanup_attribute_list(heap, list);
            return Err(AppContainerError("无法写入 AppContainer 启动属性"));
        }

        // SAFETY: handles 位于 Box 中，在 Self 的生命周期内地址稳定；调用方负责确保句柄可继承。
        if unsafe {
            UpdateProcThreadAttribute(
                list,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                handles.as_mut_ptr().cast::<c_void>(),
                size_of_val(handles.as_ref()),
                null_mut(),
                null(),
            )
        } == 0
        {
            cleanup_attribute_list(heap, list);
            return Err(AppContainerError("无法写入子进程句柄列表"));
        }

        Ok(Self {
            list,
            heap,
            capabilities,
            handles,
            _sid: sid,
        })
    }

    pub fn as_ptr(&self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.list
    }

    pub fn appcontainer_sid(&self) -> PSID {
        self.capabilities.AppContainerSid
    }

    pub fn capability_count(&self) -> u32 {
        self.capabilities.CapabilityCount
    }

    pub fn inherited_handle_count(&self) -> usize {
        self.handles.len()
    }
}

fn cleanup_attribute_list(heap: HANDLE, list: LPPROC_THREAD_ATTRIBUTE_LIST) {
    // SAFETY: list 已成功初始化，并且来自指定进程堆。
    unsafe {
        DeleteProcThreadAttributeList(list);
        HeapFree(heap, 0, list.cast());
    }
}

impl Drop for AppContainerAttributes {
    fn drop(&mut self) {
        if !self.list.is_null() {
            cleanup_attribute_list(self.heap, self.list);
            self.list = null_mut();
        }
    }
}

#[derive(Debug)]
pub struct AppContainerError(&'static str);

impl std::fmt::Display for AppContainerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for AppContainerError {}
