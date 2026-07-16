use std::mem::{size_of, zeroed};
use std::ptr::{null, null_mut};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation,
    QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
};

pub struct Job {
    handle: HANDLE,
}

impl Job {
    pub fn new() -> Result<Self, JobError> {
        // SAFETY: 创建匿名 Job，不传安全属性。
        let handle = unsafe { CreateJobObjectW(null(), null()) };
        if handle.is_null() {
            return Err(JobError("无法创建 Job Object"));
        }
        // SAFETY: 零初始化是该 Win32 结构的有效初始状态。
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: handle 有效，limits 指针和长度匹配信息类。
        if unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
        {
            // SAFETY: handle 由 CreateJobObjectW 返回。
            unsafe { CloseHandle(handle) };
            return Err(JobError("无法设置 Job Object 终止策略"));
        }
        Ok(Self { handle })
    }

    pub(crate) fn assign_suspended_process(&self, process: HANDLE) -> Result<(), JobError> {
        if process.is_null() {
            return Err(JobError("进程句柄为空"));
        }
        // SAFETY: Job 与进程句柄均由调用方保持有效；调用必须发生在恢复主线程前。
        if unsafe { AssignProcessToJobObject(self.handle, process) } == 0 {
            return Err(JobError("无法把 suspended 进程加入 Job Object"));
        }
        Ok(())
    }

    pub fn active_processes(&self) -> Result<u32, JobError> {
        // SAFETY: 零初始化是该 Win32 结构的有效初始状态。
        let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { zeroed() };
        // SAFETY: info 指针和大小匹配信息类。
        if unsafe {
            QueryInformationJobObject(
                self.handle,
                JobObjectBasicAccountingInformation,
                (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                null_mut(),
            )
        } == 0
        {
            return Err(JobError("无法查询 Job Object"));
        }
        Ok(info.ActiveProcesses)
    }

    pub fn terminate_and_wait(&self, timeout: Duration) -> Result<(), JobError> {
        // SAFETY: handle 是有效 Job；退出码固定且不含外部输入。
        if unsafe { TerminateJobObject(self.handle, 1) } == 0 {
            return Err(JobError("无法终止 Job Object"));
        }
        let deadline = Instant::now() + timeout;
        loop {
            if self.active_processes()? == 0 {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(JobError("Job Object 进程树未及时退出"));
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    pub fn close(&mut self) {
        if !self.handle.is_null() {
            // SAFETY: handle 由 CreateJobObjectW 返回且仅关闭一次；KILL_ON_JOB_CLOSE 生效。
            unsafe { CloseHandle(self.handle) };
            self.handle = null_mut();
        }
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        self.close();
    }
}

#[derive(Debug)]
pub struct JobError(&'static str);

impl std::fmt::Display for JobError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for JobError {}
