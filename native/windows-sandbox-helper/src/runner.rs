use crate::acl::{
    AclEntryRecord, AclTransaction, GrantMode, inheritance_for, recover_pending_transactions,
    rights_for,
};
use crate::appcontainer::AppContainerAttributes;
use crate::job::Job;
use crate::paths::{PathKind, ResolvedPath, paths_equal, resolve_local_ntfs};
use crate::profile::{ProfileStatus, profile_folder_path, query_profile};
use crate::protocol::{AccessMode, GrantKind, MAX_MESSAGE_BYTES, RunRequest};
use crate::state::{StatePaths, load_state};
use crate::user_sid::UserSid;
use serde_json::Value;
use std::collections::BTreeMap;
use std::ffi::c_void;
use std::fs::File;
use std::io::{Read, Write};
use std::mem::size_of;
use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::FromRawHandle;
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{
    CloseHandle, ERROR_ACCESS_DENIED, HANDLE, HANDLE_FLAG_INHERIT, INVALID_HANDLE_VALUE,
    SetHandleInformation, WAIT_OBJECT_0, WAIT_TIMEOUT,
};
use windows_sys::Win32::Security::{
    EqualSid, GetTokenInformation, PSID, SECURITY_ATTRIBUTES, TOKEN_APPCONTAINER_INFORMATION,
    TOKEN_QUERY, TokenAppContainerSid, TokenIsAppContainer,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW, TH32CS_SNAPPROCESS,
};
use windows_sys::Win32::System::Pipes::CreatePipe;
use windows_sys::Win32::System::Threading::{
    CREATE_NO_WINDOW, CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CreateProcessW,
    EXTENDED_STARTUPINFO_PRESENT, GetCurrentProcessId, GetExitCodeProcess, OpenProcess,
    OpenProcessToken, PROCESS_INFORMATION, ResumeThread, STARTF_USESTDHANDLES, STARTUPINFOEXW,
    STARTUPINFOW, TerminateProcess, WaitForMultipleObjects, WaitForSingleObject,
};

const PROCESS_POLL_INTERVAL_MS: u32 = 20;
const PROCESS_SYNCHRONIZE: u32 = 0x0010_0000;
const PROCESS_CLEANUP_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_ACL_TARGETS: usize = 4096;
const STDLIB_ROOT_FILES: &[&str] = &[
    "__future__.py",
    "_collections_abc.py",
    "_compression.py",
    "_sitebuiltins.py",
    "_weakrefset.py",
    "abc.py",
    "ast.py",
    "bisect.py",
    "bz2.py",
    "codecs.py",
    "contextlib.py",
    "copy.py",
    "copyreg.py",
    "dataclasses.py",
    "dis.py",
    "enum.py",
    "fnmatch.py",
    "functools.py",
    "genericpath.py",
    "glob.py",
    "inspect.py",
    "io.py",
    "ipaddress.py",
    "keyword.py",
    "linecache.py",
    "locale.py",
    "lzma.py",
    "ntpath.py",
    "opcode.py",
    "operator.py",
    "os.py",
    "pathlib.py",
    "platform.py",
    "posixpath.py",
    "random.py",
    "reprlib.py",
    "selectors.py",
    "shutil.py",
    "signal.py",
    "socket.py",
    "stat.py",
    "subprocess.py",
    "tempfile.py",
    "threading.py",
    "token.py",
    "tokenize.py",
    "types.py",
    "typing.py",
    "warnings.py",
    "weakref.py",
    "zipimport.py",
];
const STDLIB_PACKAGES: &[&str] = &[
    "collections",
    "encodings",
    "importlib",
    "json",
    "re",
    "urllib",
];
const STDLIB_EXTENSIONS: &[&str] = &[
    "_bz2.pyd",
    "_lzma.pyd",
    "_socket.pyd",
    "_wmi.pyd",
    "select.pyd",
];

#[derive(Debug)]
struct AclTarget {
    path: ResolvedPath,
    mode: GrantMode,
    inheritance: u8,
}

#[derive(Debug)]
pub struct RunnerResult {
    pub status: &'static str,
    pub worker_result: Option<Value>,
    pub error_code: Option<&'static str>,
    pub error_message: Option<&'static str>,
}

impl RunnerResult {
    fn completed(worker_result: Value) -> Self {
        Self {
            status: "completed",
            worker_result: Some(worker_result),
            error_code: None,
            error_message: None,
        }
    }

    fn failed(code: &'static str, message: &'static str) -> Self {
        Self {
            status: "failed",
            worker_result: None,
            error_code: Some(code),
            error_message: Some(message),
        }
    }

    fn timed_out() -> Self {
        Self {
            status: "timed_out",
            worker_result: None,
            error_code: Some("sandbox_timeout"),
            error_message: Some("Windows 沙箱工具执行超时"),
        }
    }

    fn host_disconnected() -> Self {
        Self {
            status: "failed",
            worker_result: None,
            error_code: Some("host_disconnected"),
            error_message: Some("MewCode 宿主进程已退出"),
        }
    }
}

pub fn run(request: &RunRequest) -> RunnerResult {
    match run_inner(request) {
        Ok(result) => result,
        Err(failure) => RunnerResult::failed(failure.code, failure.message),
    }
}

fn run_inner(request: &RunRequest) -> Result<RunnerResult, RunnerFailure> {
    let total_started = Instant::now();
    let host_process = parent_process_handle()?;
    let owner = UserSid::current().map_err(|_| RunnerFailure::unavailable())?;
    let state_paths =
        StatePaths::for_current_user(&owner).map_err(|_| RunnerFailure::unavailable())?;
    let state = load_state(&state_paths, &owner).map_err(|_| RunnerFailure::setup_required())?;
    match query_profile(Some(&state)).map_err(|_| RunnerFailure::unavailable())? {
        ProfileStatus::Matching { .. } => {}
        ProfileStatus::Missing | ProfileStatus::Mismatch => {
            return Err(RunnerFailure::setup_required());
        }
    }
    recover_pending_transactions(&state_paths).map_err(|_| RunnerFailure::recovery_failed())?;
    trace_phase("pending recovery complete", total_started);

    let target_started = Instant::now();
    let targets = resolve_targets(request)?;
    trace_phase(
        &format!("resolved {} ACL targets", targets.len()),
        target_started,
    );
    let sandbox_sid = state.appcontainer_sid.clone();
    let apply_started = Instant::now();
    let mut transaction =
        AclTransaction::begin(state_paths, request.request_id.clone(), |transaction_id| {
            targets
                .iter()
                .map(|target| {
                    AclEntryRecord::new(
                        &target.path,
                        sandbox_sid.clone(),
                        rights_for(target.mode, target.path.kind),
                        target.inheritance,
                        transaction_id,
                    )
                })
                .collect()
        })
        .map_err(|_| RunnerFailure::acl_failed())?;
    trace_phase("ACL transaction applied", apply_started);
    transaction
        .verify_targets_unchanged()
        .map_err(|_| RunnerFailure::path_changed())?;
    pause_for_smoke_trace();

    let sandbox_home =
        profile_folder_path(&state.appcontainer_sid).map_err(|_| RunnerFailure::unavailable())?;
    let worker_started = Instant::now();
    let result = match process_is_signaled(host_process.raw()) {
        Ok(true) => Ok(RunnerResult::host_disconnected()),
        Ok(false) => execute_worker(
            request,
            &state.appcontainer_profile_name,
            &sandbox_home,
            host_process.raw(),
        ),
        Err(failure) => Err(failure),
    };
    trace_phase("worker finished", worker_started);
    let rollback_started = Instant::now();
    if transaction.rollback().is_err() {
        return Err(RunnerFailure::cleanup_failed());
    }
    trace_phase("ACL transaction rolled back", rollback_started);
    trace_phase("run complete", total_started);
    result
}

fn trace_phase(message: &str, started: Instant) {
    if std::env::var_os("MEWCODE_HELPER_TRACE").is_some() {
        eprintln!("[mewcode-helper] {message}: {:?}", started.elapsed());
    }
}

fn pause_for_smoke_trace() {
    if std::env::var_os("MEWCODE_HELPER_TRACE").is_none() {
        return;
    }
    let milliseconds = std::env::var("MEWCODE_HELPER_PAUSE_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value <= 60_000)
        .unwrap_or(0);
    if milliseconds > 0 {
        std::thread::sleep(Duration::from_millis(milliseconds));
    }
}

fn resolve_targets(request: &RunRequest) -> Result<Vec<AclTarget>, RunnerFailure> {
    let workspace = resolve_local_ntfs(Path::new(&request.workspace))
        .map_err(|_| RunnerFailure::invalid_path())?;
    if workspace.kind != PathKind::Directory {
        return Err(RunnerFailure::invalid_path());
    }

    let python = resolve_local_ntfs(Path::new(&request.python_executable))
        .map_err(|_| RunnerFailure::invalid_python())?;
    if python.kind != PathKind::File || !is_python_executable(&python.canonical_path) {
        return Err(RunnerFailure::invalid_python());
    }

    let mut targets = Vec::new();
    merge_target(&mut targets, python.clone(), GrantMode::Execute, false);
    let mut base_python = python.clone();
    let venv_root = python
        .canonical_path
        .parent()
        .and_then(Path::parent)
        .filter(|path| path.join("pyvenv.cfg").is_file())
        .map(Path::to_path_buf);
    if let Some(venv_root) = &venv_root {
        let configuration = resolve_local_ntfs(&venv_root.join("pyvenv.cfg"))
            .map_err(|_| RunnerFailure::invalid_python())?;
        base_python = read_venv_base_python(&configuration)?;
        merge_target(&mut targets, configuration, GrantMode::Read, false);
    }
    merge_target(&mut targets, base_python.clone(), GrantMode::Execute, false);
    if let Some(runtime_root) = base_python.canonical_path.parent() {
        let runtime_root_dir = runtime_root.to_path_buf();
        let runtime_root =
            resolve_local_ntfs(runtime_root).map_err(|_| RunnerFailure::invalid_python())?;
        for name in [
            "python3.dll",
            "python312.dll",
            "vcruntime140.dll",
            "vcruntime140_1.dll",
        ] {
            let library = runtime_root_dir.join(name);
            if library.is_file() {
                add_path(&mut targets, &library, GrantMode::Execute, false)?;
            }
        }
        let standard_library = runtime_root_dir.join("Lib");
        for name in STDLIB_ROOT_FILES {
            add_path(
                &mut targets,
                &standard_library.join(name),
                GrantMode::Read,
                false,
            )?;
        }
        for name in STDLIB_PACKAGES {
            add_tree_explicit(
                &mut targets,
                &standard_library.join(name),
                GrantMode::Read,
                false,
                RunnerFailure::invalid_python,
            )?;
        }
        add_path(&mut targets, &standard_library, GrantMode::Execute, false)?;
        let extensions = runtime_root_dir.join("DLLs");
        for name in STDLIB_EXTENSIONS {
            add_path(
                &mut targets,
                &extensions.join(name),
                GrantMode::Execute,
                false,
            )?;
        }
        add_path(&mut targets, &extensions, GrantMode::Execute, false)?;
        let environment_root = venv_root.as_deref().unwrap_or(runtime_root_dir.as_path());
        let environment_lib = environment_root.join("Lib");
        let site_packages = environment_lib.join("site-packages");
        let package_root = resolve_local_ntfs(Path::new(&request.python_package_root))
            .map_err(|_| RunnerFailure::invalid_python())?;
        if package_root.kind != PathKind::Directory
            || !package_root
                .canonical_path
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.eq_ignore_ascii_case("mewcode"))
            || !package_root.canonical_path.join("tool_worker.py").is_file()
        {
            return Err(RunnerFailure::invalid_python());
        }
        // editable install 的源码包位于 workspace 内，后面的可继承 workspace
        // 授权已经覆盖它；避免再逐文件添加重叠 ACE。普通 wheel 安装仍需
        // 对 site-packages 中的包目录做窄化只读授权。
        if !package_root
            .canonical_path
            .starts_with(&workspace.canonical_path)
        {
            add_tree_explicit(
                &mut targets,
                &package_root.canonical_path,
                GrantMode::Read,
                false,
                RunnerFailure::invalid_python,
            )?;
        }
        add_path(&mut targets, &site_packages, GrantMode::Execute, false)?;
        add_path(&mut targets, &environment_lib, GrantMode::Execute, false)?;
        if venv_root.is_some() {
            add_path(&mut targets, environment_root, GrantMode::Execute, false)?;
        }
        merge_target(&mut targets, runtime_root, GrantMode::Execute, false);
    }
    add_tree_explicit(
        &mut targets,
        &workspace.canonical_path,
        GrantMode::Write,
        true,
        RunnerFailure::invalid_path,
    )?;
    for grant in &request.grants {
        let target = resolve_local_ntfs(Path::new(&grant.path))
            .map_err(|_| RunnerFailure::invalid_path())?;
        let expected_kind = match grant.kind {
            GrantKind::File => PathKind::File,
            GrantKind::Directory => PathKind::Directory,
        };
        if target.kind != expected_kind {
            return Err(RunnerFailure::invalid_path());
        }
        let mode = match grant.mode {
            AccessMode::Read => GrantMode::Read,
            AccessMode::Write => GrantMode::Write,
            AccessMode::Execute => GrantMode::Execute,
        };
        if target.kind == PathKind::Directory {
            add_tree_explicit(
                &mut targets,
                &target.canonical_path,
                mode,
                true,
                RunnerFailure::invalid_path,
            )?;
        } else {
            merge_target(&mut targets, target, mode, false);
        }
    }
    Ok(targets)
}

fn read_venv_base_python(configuration: &ResolvedPath) -> Result<ResolvedPath, RunnerFailure> {
    let metadata = std::fs::metadata(&configuration.canonical_path)
        .map_err(|_| RunnerFailure::invalid_python())?;
    if metadata.len() > 64 * 1024 {
        return Err(RunnerFailure::invalid_python());
    }
    let content = std::fs::read_to_string(&configuration.canonical_path)
        .map_err(|_| RunnerFailure::invalid_python())?;
    let executable = content.lines().find_map(|line| {
        let (key, value) = line.split_once('=')?;
        key.trim()
            .eq_ignore_ascii_case("executable")
            .then(|| value.trim())
    });
    let executable = executable.ok_or_else(RunnerFailure::invalid_python)?;
    let resolved =
        resolve_local_ntfs(Path::new(executable)).map_err(|_| RunnerFailure::invalid_python())?;
    if resolved.kind != PathKind::File || !is_python_executable(&resolved.canonical_path) {
        return Err(RunnerFailure::invalid_python());
    }
    Ok(resolved)
}

fn add_path(
    targets: &mut Vec<AclTarget>,
    path: &Path,
    mode: GrantMode,
    inheritable: bool,
) -> Result<(), RunnerFailure> {
    let target = resolve_local_ntfs(path).map_err(|_| RunnerFailure::invalid_python())?;
    merge_target(targets, target, mode, inheritable);
    if targets.len() > MAX_ACL_TARGETS {
        return Err(RunnerFailure::too_many_targets());
    }
    Ok(())
}

fn add_tree_explicit(
    targets: &mut Vec<AclTarget>,
    root: &Path,
    mode: GrantMode,
    root_inheritable: bool,
    failure: fn() -> RunnerFailure,
) -> Result<(), RunnerFailure> {
    let root = resolve_local_ntfs(root).map_err(|_| failure())?;
    if root.kind != PathKind::Directory {
        return Err(failure());
    }
    if root_inheritable {
        merge_target(targets, root, mode, true);
        if targets.len() > MAX_ACL_TARGETS {
            return Err(RunnerFailure::too_many_targets());
        }
        return Ok(());
    }
    let mut pending = vec![root.clone()];
    let mut discovered = Vec::new();
    while let Some(directory) = pending.pop() {
        discovered.push(directory.clone());
        for entry in std::fs::read_dir(&directory.canonical_path).map_err(|_| failure())? {
            let path = entry.map_err(|_| failure())?.path();
            let resolved = resolve_local_ntfs(&path).map_err(|_| failure())?;
            if resolved.kind == PathKind::Directory {
                pending.push(resolved);
            } else {
                discovered.push(resolved);
            }
            if targets.len().saturating_add(discovered.len()) > MAX_ACL_TARGETS {
                return Err(RunnerFailure::too_many_targets());
            }
        }
    }
    discovered.sort_by(|left, right| {
        right
            .canonical_path
            .components()
            .count()
            .cmp(&left.canonical_path.components().count())
            .then_with(|| {
                left.canonical_path
                    .as_os_str()
                    .to_string_lossy()
                    .cmp(&right.canonical_path.as_os_str().to_string_lossy())
            })
    });
    for target in discovered {
        merge_target(targets, target, mode, false);
    }
    Ok(())
}

fn merge_target(
    targets: &mut Vec<AclTarget>,
    target: ResolvedPath,
    mode: GrantMode,
    inheritable: bool,
) {
    if let Some(existing) = targets
        .iter_mut()
        .find(|existing| paths_equal(&existing.path.canonical_path, &target.canonical_path))
    {
        if mode_rank(mode) > mode_rank(existing.mode) {
            existing.mode = mode;
        }
        if inheritable {
            existing.inheritance = inheritance_for(existing.path.kind);
        }
        return;
    }
    let inheritance = if inheritable {
        inheritance_for(target.kind)
    } else {
        0
    };
    targets.push(AclTarget {
        path: target,
        mode,
        inheritance,
    });
}

fn mode_rank(mode: GrantMode) -> u8 {
    match mode {
        GrantMode::Read => 0,
        GrantMode::Execute => 1,
        GrantMode::Write => 2,
    }
}

fn is_python_executable(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
        return false;
    };
    let lower = name.to_ascii_lowercase();
    lower == "python.exe"
        || (lower.starts_with("python3")
            && lower.ends_with(".exe")
            && lower[7..lower.len() - 4]
                .bytes()
                .all(|byte| byte.is_ascii_digit()))
}

fn execute_worker(
    request: &RunRequest,
    profile_name: &str,
    sandbox_home: &Path,
    host_process: HANDLE,
) -> Result<RunnerResult, RunnerFailure> {
    let worker_input = worker_input(request)?;
    let (stdin_child, mut stdin_parent) = create_pipe_pair(PipeDirection::ParentWrites)?;
    let (stdout_child, stdout_parent) = create_pipe_pair(PipeDirection::ParentReads)?;
    let (stderr_child, stderr_parent) = create_pipe_pair(PipeDirection::ParentReads)?;

    let attributes = AppContainerAttributes::new(
        profile_name,
        &[stdin_child.raw(), stdout_child.raw(), stderr_child.raw()],
    )
    .map_err(|_| RunnerFailure::start_failed())?;
    let mut startup = STARTUPINFOEXW::default();
    startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = stdin_child.raw();
    startup.StartupInfo.hStdOutput = stdout_child.raw();
    startup.StartupInfo.hStdError = stderr_child.raw();
    startup.lpAttributeList = attributes.as_ptr();

    let application = wide_null_path(Path::new(&request.python_executable))?;
    let mut command_line = command_line(Path::new(&request.python_executable));
    let current_directory = wide_null_path(Path::new(&request.workspace))?;
    let environment = minimal_environment(
        Path::new(&request.python_executable),
        Path::new(&request.python_package_root),
        Path::new(&request.workspace),
        sandbox_home,
    );
    let mut process_info = PROCESS_INFORMATION::default();
    // SAFETY: 所有指针在调用期间有效；命令行是可写 UTF-16；句柄列表只含显式可继承管道。
    let created = unsafe {
        CreateProcessW(
            application.as_ptr(),
            command_line.as_mut_ptr(),
            null(),
            null(),
            1,
            CREATE_SUSPENDED
                | CREATE_NO_WINDOW
                | CREATE_UNICODE_ENVIRONMENT
                | EXTENDED_STARTUPINFO_PRESENT,
            environment.as_ptr().cast::<c_void>(),
            current_directory.as_ptr(),
            (&startup as *const STARTUPINFOEXW).cast::<STARTUPINFOW>(),
            &mut process_info,
        )
    };
    if created == 0 {
        let error = std::io::Error::last_os_error();
        return Err(RunnerFailure::start_failed_for_error(error.raw_os_error()));
    }
    let process = OwnedHandle::new(process_info.hProcess);
    let thread = OwnedHandle::new(process_info.hThread);
    if verify_appcontainer_token(process.raw(), attributes.appcontainer_sid()).is_err() {
        terminate_unassigned_process(process.raw());
        return Err(RunnerFailure::token_mismatch());
    }
    drop(attributes);
    drop(stdin_child);
    drop(stdout_child);
    drop(stderr_child);

    let job = Job::new().map_err(|_| {
        terminate_unassigned_process(process.raw());
        RunnerFailure::start_failed()
    })?;
    if job.assign_suspended_process(process.raw()).is_err() {
        terminate_unassigned_process(process.raw());
        return Err(RunnerFailure::start_failed());
    }

    let output_exceeded = Arc::new(AtomicBool::new(false));
    let stdout_reader = spawn_capture(stdout_parent, Arc::clone(&output_exceeded));
    let stderr_reader = spawn_capture(stderr_parent, Arc::clone(&output_exceeded));

    // SAFETY: thread 是刚创建的 suspended worker 主线程，只恢复一次。
    let resumed = unsafe { ResumeThread(thread.raw()) };
    drop(thread);
    let mut preliminary = if resumed == u32::MAX {
        Some(RunnerResult::failed(
            "worker_start_error",
            "无法恢复 Windows 沙箱工作进程",
        ))
    } else if stdin_parent.write_all(&worker_input).is_err() {
        Some(RunnerResult::failed(
            "worker_io_error",
            "无法向 Windows 沙箱工作进程发送请求",
        ))
    } else {
        None
    };
    drop(stdin_parent);

    if preliminary.is_none() {
        preliminary = Some(wait_for_worker(
            process.raw(),
            host_process,
            request.timeout_ms,
            &output_exceeded,
        ));
    }

    let cleanup_ok = shutdown_job(&job);
    let stdout = join_capture(stdout_reader);
    let stderr = join_capture(stderr_reader);
    let exit_code = process_exit_code(process.raw());
    drop(process);
    drop(job);

    if !cleanup_ok || stdout.is_err() || stderr.is_err() || exit_code.is_err() {
        return Err(RunnerFailure::cleanup_failed());
    }
    let preliminary = preliminary.expect("worker 状态必须存在");
    if preliminary.status != "completed" {
        return Ok(preliminary);
    }
    if output_exceeded.load(Ordering::Acquire) {
        return Ok(RunnerResult::failed(
            "worker_output_limit",
            "Windows 沙箱工作进程输出超过限制",
        ));
    }
    let exit_code = exit_code.unwrap_or(1);
    if exit_code != 0 {
        return Ok(RunnerResult::failed(
            "worker_failed",
            "Windows 沙箱工作进程异常退出",
        ));
    }
    parse_worker_output(&stdout.unwrap_or_default(), &request.request_id)
}

fn wait_for_worker(
    process: HANDLE,
    host_process: HANDLE,
    timeout_ms: u64,
    output_exceeded: &AtomicBool,
) -> RunnerResult {
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    let handles = [process, host_process];
    loop {
        if output_exceeded.load(Ordering::Acquire) {
            return RunnerResult::failed("worker_output_limit", "Windows 沙箱工作进程输出超过限制");
        }
        let now = Instant::now();
        if now >= deadline {
            return RunnerResult::timed_out();
        }
        let remaining = deadline.saturating_duration_since(now).as_millis() as u64;
        let interval = remaining.min(u64::from(PROCESS_POLL_INTERVAL_MS)).max(1) as u32;
        // SAFETY: worker 与宿主进程句柄在等待期间均保持有效。
        match unsafe { WaitForMultipleObjects(handles.len() as u32, handles.as_ptr(), 0, interval) }
        {
            WAIT_OBJECT_0 => {
                return RunnerResult {
                    status: "completed",
                    worker_result: None,
                    error_code: None,
                    error_message: None,
                };
            }
            value if value == WAIT_OBJECT_0 + 1 => {
                return RunnerResult::host_disconnected();
            }
            WAIT_TIMEOUT => {}
            _ => {
                return RunnerResult::failed("worker_wait_error", "无法等待 Windows 沙箱工作进程");
            }
        }
    }
}

fn parent_process_handle() -> Result<OwnedHandle, RunnerFailure> {
    // SAFETY: 固定快照标志不使用进程 ID 参数。
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(RunnerFailure::start_failed());
    }
    let snapshot = OwnedHandle::new(snapshot);
    let current_pid = unsafe { GetCurrentProcessId() };
    let mut entry = PROCESSENTRY32W {
        dwSize: size_of::<PROCESSENTRY32W>() as u32,
        ..Default::default()
    };
    // SAFETY: snapshot 与 entry 缓冲区在枚举期间均有效。
    let mut has_entry = unsafe { Process32FirstW(snapshot.raw(), &mut entry) } != 0;
    let mut parent_pid = 0;
    while has_entry {
        if entry.th32ProcessID == current_pid {
            parent_pid = entry.th32ParentProcessID;
            break;
        }
        // SAFETY: 沿用同一有效快照和已初始化的固定大小结构。
        has_entry = unsafe { Process32NextW(snapshot.raw(), &mut entry) } != 0;
    }
    if parent_pid == 0 {
        return Err(RunnerFailure::start_failed());
    }
    // SAFETY: 只申请等待权限且不继承句柄；PID 来自系统进程快照。
    let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, parent_pid) };
    if handle.is_null() {
        return Err(RunnerFailure::start_failed());
    }
    Ok(OwnedHandle::new(handle))
}

fn process_is_signaled(process: HANDLE) -> Result<bool, RunnerFailure> {
    // SAFETY: 调用方持有带 SYNCHRONIZE 权限的有效进程句柄。
    match unsafe { WaitForSingleObject(process, 0) } {
        WAIT_OBJECT_0 => Ok(true),
        WAIT_TIMEOUT => Ok(false),
        _ => Err(RunnerFailure::start_failed()),
    }
}

fn shutdown_job(job: &Job) -> bool {
    match job.active_processes() {
        Ok(0) => true,
        Ok(_) => job.terminate_and_wait(PROCESS_CLEANUP_TIMEOUT).is_ok(),
        Err(_) => false,
    }
}

fn process_exit_code(process: HANDLE) -> Result<u32, RunnerFailure> {
    let mut exit_code = 0u32;
    // SAFETY: process 是有效进程句柄，且调用发生在等待/终止完成后。
    if unsafe { GetExitCodeProcess(process, &mut exit_code) } == 0 {
        return Err(RunnerFailure::cleanup_failed());
    }
    Ok(exit_code)
}

fn terminate_unassigned_process(process: HANDLE) {
    // SAFETY: 失败路径只针对尚未加入 Job 的 suspended 子进程；随后等待其退出。
    unsafe {
        TerminateProcess(process, 1);
        WaitForSingleObject(process, PROCESS_CLEANUP_TIMEOUT.as_millis() as u32);
    }
}

fn verify_appcontainer_token(process: HANDLE, expected_sid: PSID) -> Result<(), RunnerFailure> {
    let mut token_handle = null_mut();
    // SAFETY: process 是刚创建且仍挂起的有效进程句柄，输出指针指向本地句柄槽位。
    if unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token_handle) } == 0 {
        return Err(RunnerFailure::token_mismatch());
    }
    let token = OwnedHandle::new(token_handle);

    let mut is_appcontainer = 0u32;
    let mut returned = 0u32;
    // SAFETY: 缓冲区与长度匹配，token 在整个查询期间保持有效。
    if unsafe {
        GetTokenInformation(
            token.raw(),
            TokenIsAppContainer,
            (&mut is_appcontainer as *mut u32).cast::<c_void>(),
            size_of::<u32>() as u32,
            &mut returned,
        )
    } == 0
        || returned < size_of::<u32>() as u32
        || is_appcontainer != 1
    {
        return Err(RunnerFailure::token_mismatch());
    }

    let mut required = 0u32;
    // SAFETY: 第一次调用仅查询 TokenAppContainerSid 所需的缓冲区长度。
    unsafe {
        GetTokenInformation(
            token.raw(),
            TokenAppContainerSid,
            null_mut(),
            0,
            &mut required,
        )
    };
    if required < size_of::<TOKEN_APPCONTAINER_INFORMATION>() as u32 {
        return Err(RunnerFailure::token_mismatch());
    }
    let word_size = size_of::<usize>();
    let word_count = (required as usize).div_ceil(word_size);
    let mut buffer = vec![0usize; word_count];
    returned = 0;
    // SAFETY: usize 缓冲区满足结构体对齐要求，容量不少于 Windows 报告的长度。
    if unsafe {
        GetTokenInformation(
            token.raw(),
            TokenAppContainerSid,
            buffer.as_mut_ptr().cast::<c_void>(),
            required,
            &mut returned,
        )
    } == 0
        || returned > required
    {
        return Err(RunnerFailure::token_mismatch());
    }
    // SAFETY: 查询成功且返回长度至少容纳 TOKEN_APPCONTAINER_INFORMATION。
    let information = unsafe { &*buffer.as_ptr().cast::<TOKEN_APPCONTAINER_INFORMATION>() };
    if information.TokenAppContainer.is_null()
        || expected_sid.is_null()
        // SAFETY: 两个 SID 分别由查询缓冲区和启动属性持有，当前作用域内均有效。
        || unsafe { EqualSid(information.TokenAppContainer, expected_sid) } == 0
    {
        return Err(RunnerFailure::token_mismatch());
    }
    Ok(())
}

fn worker_input(request: &RunRequest) -> Result<Vec<u8>, RunnerFailure> {
    let mut input = serde_json::to_vec(&request.worker_payload)
        .map_err(|_| RunnerFailure::protocol_failed())?;
    if input.len() > MAX_MESSAGE_BYTES {
        return Err(RunnerFailure::protocol_failed());
    }
    input.push(b'\n');
    Ok(input)
}

fn parse_worker_output(output: &[u8], request_id: &str) -> Result<RunnerResult, RunnerFailure> {
    if output.is_empty()
        || output.len() > MAX_MESSAGE_BYTES + 1
        || !output.ends_with(b"\n")
        || output.iter().filter(|byte| **byte == b'\n').count() != 1
    {
        return Err(RunnerFailure::protocol_failed());
    }
    let value: Value = serde_json::from_slice(&output[..output.len() - 1])
        .map_err(|_| RunnerFailure::protocol_failed())?;
    let object = value
        .as_object()
        .ok_or_else(RunnerFailure::protocol_failed)?;
    if object.get("call_id").and_then(Value::as_str) != Some(request_id) {
        return Err(RunnerFailure::protocol_failed());
    }
    Ok(RunnerResult::completed(value))
}

enum PipeDirection {
    ParentReads,
    ParentWrites,
}

fn create_pipe_pair(direction: PipeDirection) -> Result<(OwnedHandle, File), RunnerFailure> {
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: null_mut(),
        bInheritHandle: 1,
    };
    let mut read = null_mut();
    let mut write = null_mut();
    // SAFETY: 输出句柄指针有效，安全属性只要求两个句柄初始可继承。
    if unsafe { CreatePipe(&mut read, &mut write, &attributes, 0) } == 0 {
        return Err(RunnerFailure::start_failed());
    }
    let read = OwnedHandle::new(read);
    let write = OwnedHandle::new(write);
    let (child, parent) = match direction {
        PipeDirection::ParentReads => (write, read),
        PipeDirection::ParentWrites => (read, write),
    };
    // SAFETY: parent 是本进程持有的有效管道端；显式清除其继承位。
    if unsafe { SetHandleInformation(parent.raw(), HANDLE_FLAG_INHERIT, 0) } == 0 {
        return Err(RunnerFailure::start_failed());
    }
    let raw = parent.into_raw();
    // SAFETY: raw 来自 OwnedHandle，所有权在此唯一转移给 File。
    let parent = unsafe { File::from_raw_handle(raw) };
    Ok((child, parent))
}

fn spawn_capture(file: File, exceeded: Arc<AtomicBool>) -> JoinHandle<std::io::Result<Vec<u8>>> {
    std::thread::spawn(move || {
        let mut file = file;
        let mut output = Vec::new();
        let mut buffer = [0u8; 8192];
        loop {
            let read = file.read(&mut buffer)?;
            if read == 0 {
                return Ok(output);
            }
            if output.len().saturating_add(read) > MAX_MESSAGE_BYTES + 1 {
                exceeded.store(true, Ordering::Release);
                return Ok(output);
            }
            output.extend_from_slice(&buffer[..read]);
        }
    })
}

fn join_capture(handle: JoinHandle<std::io::Result<Vec<u8>>>) -> Result<Vec<u8>, RunnerFailure> {
    handle
        .join()
        .map_err(|_| RunnerFailure::cleanup_failed())?
        .map_err(|_| RunnerFailure::cleanup_failed())
}

fn command_line(python: &Path) -> Vec<u16> {
    let executable = quote_windows_argument(&python.as_os_str().to_string_lossy());
    format!("{executable} -S -m mewcode.tool_worker")
        .encode_utf16()
        .chain([0])
        .collect()
}

fn quote_windows_argument(argument: &str) -> String {
    let mut output = String::from("\"");
    let mut backslashes = 0usize;
    for character in argument.chars() {
        match character {
            '\\' => backslashes += 1,
            '"' => {
                output.push_str(&"\\".repeat(backslashes * 2 + 1));
                output.push('"');
                backslashes = 0;
            }
            _ => {
                output.push_str(&"\\".repeat(backslashes));
                backslashes = 0;
                output.push(character);
            }
        }
    }
    output.push_str(&"\\".repeat(backslashes * 2));
    output.push('"');
    output
}

fn minimal_environment(
    python: &Path,
    python_package_root: &Path,
    workspace: &Path,
    sandbox_home: &Path,
) -> Vec<u16> {
    let system_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_owned());
    let python_dir = python.parent().unwrap_or_else(|| Path::new(""));
    let system32 = Path::new(&system_root).join("System32");
    let powershell = system32.join("WindowsPowerShell").join("v1.0");
    let sandbox_profile = sandbox_home.join("AC");
    let sandbox_temp = sandbox_profile.join("Temp");
    let mut values: BTreeMap<String, String> = BTreeMap::new();
    if let Some(drive) = workspace.to_string_lossy().get(..2) {
        values.insert(
            format!("={}", drive.to_ascii_uppercase()),
            workspace.to_string_lossy().into_owned(),
        );
    }
    values.insert(
        "ComSpec".to_owned(),
        system32.join("cmd.exe").to_string_lossy().into_owned(),
    );
    values.insert(
        "APPDATA".to_owned(),
        sandbox_profile.to_string_lossy().into_owned(),
    );
    let sandbox_profile_text = sandbox_profile.to_string_lossy();
    if let Some(drive) = sandbox_profile_text.get(..2) {
        values.insert("HOMEDRIVE".to_owned(), drive.to_owned());
        values.insert(
            "HOMEPATH".to_owned(),
            sandbox_profile_text.get(2..).unwrap_or("\\").to_owned(),
        );
    }
    values.insert(
        "LOCALAPPDATA".to_owned(),
        sandbox_profile.to_string_lossy().into_owned(),
    );
    values.insert(
        "MEWCODE_SANDBOX".to_owned(),
        "windows-appcontainer".to_owned(),
    );
    values.insert(
        "PATH".to_owned(),
        std::env::join_paths([python_dir, system32.as_path(), powershell.as_path()])
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned(),
    );
    values.insert("PATHEXT".to_owned(), ".COM;.EXE;.BAT;.CMD".to_owned());
    values.insert("PYTHONIOENCODING".to_owned(), "utf-8".to_owned());
    values.insert("PYTHONNOUSERSITE".to_owned(), "1".to_owned());
    values.insert("PYTHONUTF8".to_owned(), "1".to_owned());
    if is_python_executable(python) {
        let (python_home, _site_packages) = python_environment_paths(python);
        let module_root = python_package_root
            .parent()
            .unwrap_or_else(|| Path::new(""));
        values.insert(
            "PYTHONHOME".to_owned(),
            python_home.to_string_lossy().into_owned(),
        );
        values.insert(
            "PYTHONPATH".to_owned(),
            module_root.to_string_lossy().into_owned(),
        );
    }
    values.insert(
        "PSModulePath".to_owned(),
        powershell.join("Modules").to_string_lossy().into_owned(),
    );
    values.insert(
        "SystemDrive".to_owned(),
        system_root.chars().take(2).collect(),
    );
    values.insert("SystemRoot".to_owned(), system_root.clone());
    values.insert(
        "TEMP".to_owned(),
        sandbox_temp.to_string_lossy().into_owned(),
    );
    values.insert(
        "TMP".to_owned(),
        sandbox_temp.to_string_lossy().into_owned(),
    );
    values.insert(
        "USERPROFILE".to_owned(),
        sandbox_profile.to_string_lossy().into_owned(),
    );
    values.insert("WINDIR".to_owned(), system_root);
    for key in [
        "ALLUSERSPROFILE",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
    ] {
        if let Ok(value) = std::env::var(key)
            && !value.is_empty()
        {
            values.insert(key.to_owned(), value);
        }
    }
    environment_block(&values)
}

fn python_environment_paths(python: &Path) -> (PathBuf, PathBuf) {
    let executable_root = python.parent().unwrap_or_else(|| Path::new(""));
    let venv_root = executable_root
        .parent()
        .filter(|root| root.join("pyvenv.cfg").is_file());
    if let Some(venv_root) = venv_root {
        let configuration =
            std::fs::read_to_string(venv_root.join("pyvenv.cfg")).unwrap_or_default();
        if let Some(base_executable) = configuration.lines().find_map(|line| {
            let (key, value) = line.split_once('=')?;
            key.trim()
                .eq_ignore_ascii_case("executable")
                .then(|| PathBuf::from(value.trim()))
        }) && let Some(base_root) = base_executable.parent()
        {
            return (
                base_root.to_path_buf(),
                venv_root.join("Lib").join("site-packages"),
            );
        }
    }
    (
        executable_root.to_path_buf(),
        executable_root.join("Lib").join("site-packages"),
    )
}

fn environment_block(values: &BTreeMap<String, String>) -> Vec<u16> {
    let mut block = Vec::new();
    let mut entries: Vec<_> = values.iter().collect();
    entries.sort_by(|(left, _), (right, _)| {
        left.to_ascii_lowercase().cmp(&right.to_ascii_lowercase())
    });
    for (key, value) in entries {
        block.extend(format!("{key}={value}").encode_utf16());
        block.push(0);
    }
    block.push(0);
    block
}

fn wide_null_path(path: &Path) -> Result<Vec<u16>, RunnerFailure> {
    let value: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
    if value.len() <= 1 || value[..value.len() - 1].contains(&0) {
        return Err(RunnerFailure::invalid_path());
    }
    Ok(value)
}

struct OwnedHandle(HANDLE);

impl OwnedHandle {
    fn new(handle: HANDLE) -> Self {
        Self(handle)
    }

    fn raw(&self) -> HANDLE {
        self.0
    }

    fn into_raw(mut self) -> *mut c_void {
        let handle = self.0;
        self.0 = null_mut();
        handle
    }
}

impl Drop for OwnedHandle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: 句柄由当前对象唯一持有且只关闭一次。
            unsafe { CloseHandle(self.0) };
            self.0 = null_mut();
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct RunnerFailure {
    code: &'static str,
    message: &'static str,
}

impl RunnerFailure {
    const fn new(code: &'static str, message: &'static str) -> Self {
        Self { code, message }
    }

    const fn unavailable() -> Self {
        Self::new("sandbox_unavailable", "Windows 沙箱不可用")
    }

    const fn setup_required() -> Self {
        Self::new("setup_required", "Windows 沙箱尚未准备")
    }

    const fn recovery_failed() -> Self {
        Self::new("acl_recovery_failed", "Windows 沙箱临时授权恢复失败")
    }

    const fn acl_failed() -> Self {
        Self::new("acl_apply_failed", "Windows 沙箱临时授权失败")
    }

    const fn path_changed() -> Self {
        Self::new("path_changed", "授权目标在启动前发生变化")
    }

    const fn invalid_path() -> Self {
        Self::new("invalid_path", "Windows 沙箱路径不受支持")
    }

    const fn invalid_python() -> Self {
        Self::new("invalid_python", "Python 运行时路径无效")
    }

    const fn too_many_targets() -> Self {
        Self::new("too_many_acl_targets", "Windows 沙箱授权目标过多")
    }

    const fn start_failed() -> Self {
        Self::new("worker_start_error", "无法启动 Windows 沙箱工作进程")
    }

    const fn token_mismatch() -> Self {
        Self::new("worker_token_mismatch", "Windows 沙箱工作进程身份校验失败")
    }

    fn start_failed_for_error(error: Option<i32>) -> Self {
        if error == Some(ERROR_ACCESS_DENIED as i32) {
            return Self::new(
                "worker_runtime_access_denied",
                "Windows 沙箱无法读取 Python 运行时文件",
            );
        }
        Self::start_failed()
    }

    const fn protocol_failed() -> Self {
        Self::new("sandbox_protocol_error", "Windows 沙箱工作进程响应无效")
    }

    const fn cleanup_failed() -> Self {
        Self::new("sandbox_cleanup_failed", "Windows 沙箱清理失败")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::{StatePaths, load_state};
    use crate::user_sid::UserSid;

    #[test]
    fn quotes_windows_arguments_with_spaces_and_trailing_slashes() {
        assert_eq!(
            quote_windows_argument(r"D:\Python Dir\python.exe"),
            r#""D:\Python Dir\python.exe""#
        );
        assert_eq!(quote_windows_argument("D:\\folder\\"), "\"D:\\folder\\\\\"");
    }

    #[test]
    fn environment_block_has_double_null_and_no_secret_names() {
        let block = minimal_environment(
            Path::new(r"D:\Python\python.exe"),
            Path::new(r"D:\workspace\mewcode"),
            Path::new(r"D:\workspace"),
            Path::new(r"C:\SandboxProfile"),
        );
        assert!(block.ends_with(&[0, 0]));
        let text = String::from_utf16_lossy(&block);
        for forbidden in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HTTP_PROXY"] {
            assert!(!text.contains(forbidden));
        }
        assert!(text.contains("MEWCODE_SANDBOX=windows-appcontainer"));
        assert!(text.contains(r"PYTHONPATH=D:\workspace"));
        assert!(text.contains(r"USERPROFILE=C:\SandboxProfile\AC"));
    }

    #[test]
    fn worker_output_requires_one_line_and_matching_id() {
        let output = b"{\"call_id\":\"call-1\",\"ok\":true}\n";
        assert_eq!(
            parse_worker_output(output, "call-1").unwrap().status,
            "completed"
        );
        assert!(parse_worker_output(output, "other").is_err());
        assert!(parse_worker_output(b"{}\n{}\n", "call-1").is_err());
    }

    #[test]
    fn only_expected_python_names_are_accepted() {
        assert!(is_python_executable(Path::new(r"D:\venv\python.exe")));
        assert!(is_python_executable(Path::new(r"D:\Python\python311.exe")));
        assert!(!is_python_executable(Path::new(r"D:\venv\pythonw.exe")));
        assert!(!is_python_executable(Path::new(r"D:\venv\other.exe")));
    }

    #[test]
    fn inheritable_write_tree_uses_one_root_propagation_target() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("write-tree-target-{}", std::process::id()));
        let child = root.join("existing");
        std::fs::create_dir_all(&child).unwrap();
        std::fs::write(child.join("existing.txt"), b"existing").unwrap();

        let mut targets = Vec::new();
        add_tree_explicit(
            &mut targets,
            &root,
            GrantMode::Write,
            true,
            RunnerFailure::invalid_path,
        )
        .unwrap();

        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].path.kind, PathKind::Directory);
        assert!(paths_equal(&targets[0].path.canonical_path, &root));
        assert_eq!(targets[0].inheritance, inheritance_for(PathKind::Directory));
    }

    #[test]
    fn current_parent_process_is_openable_and_running() {
        let parent = parent_process_handle().unwrap();
        assert!(!process_is_signaled(parent.raw()).unwrap());
    }

    fn native_launch_smoke() {
        let owner = UserSid::current().unwrap();
        let state_paths = StatePaths::for_current_user(&owner).unwrap();
        let state = load_state(&state_paths, &owner).unwrap();
        let system_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_owned());
        let system32 = Path::new(&system_root).join("System32");
        let executable = system32.join("cmd.exe");
        let (stdin_child, stdin_parent) = create_pipe_pair(PipeDirection::ParentWrites).unwrap();
        let (stdout_child, stdout_parent) = create_pipe_pair(PipeDirection::ParentReads).unwrap();
        let (stderr_child, stderr_parent) = create_pipe_pair(PipeDirection::ParentReads).unwrap();
        let attributes = AppContainerAttributes::new(
            &state.appcontainer_profile_name,
            &[stdin_child.raw(), stdout_child.raw(), stderr_child.raw()],
        )
        .unwrap();
        let mut startup = STARTUPINFOEXW::default();
        startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = stdin_child.raw();
        startup.StartupInfo.hStdOutput = stdout_child.raw();
        startup.StartupInfo.hStdError = stderr_child.raw();
        startup.lpAttributeList = attributes.as_ptr();
        let application = wide_null_path(&executable).unwrap();
        let mut command: Vec<u16> = format!(
            "{} /d /c exit 0",
            quote_windows_argument(&executable.to_string_lossy())
        )
        .encode_utf16()
        .chain([0])
        .collect();
        let directory = wide_null_path(&system32).unwrap();
        let sandbox_home = profile_folder_path(&state.appcontainer_sid).unwrap();
        let environment = minimal_environment(
            &executable,
            &system32.join("mewcode"),
            &system32,
            &sandbox_home,
        );
        let environment_pointer = environment.as_ptr().cast::<c_void>();
        let mut process_info = PROCESS_INFORMATION::default();
        // SAFETY: 此 ignored 原生测试只启动固定系统命令并保持所有参数存活。
        let created = unsafe {
            CreateProcessW(
                application.as_ptr(),
                command.as_mut_ptr(),
                null(),
                null(),
                1,
                CREATE_SUSPENDED
                    | CREATE_NO_WINDOW
                    | CREATE_UNICODE_ENVIRONMENT
                    | EXTENDED_STARTUPINFO_PRESENT,
                environment_pointer,
                directory.as_ptr(),
                (&startup as *const STARTUPINFOEXW).cast::<STARTUPINFOW>(),
                &mut process_info,
            )
        };
        assert_ne!(
            created,
            0,
            "CreateProcessW failed: {}",
            std::io::Error::last_os_error()
        );
        let process = OwnedHandle::new(process_info.hProcess);
        let thread = OwnedHandle::new(process_info.hThread);
        verify_appcontainer_token(process.raw(), attributes.appcontainer_sid()).unwrap();
        drop(attributes);
        drop(stdin_child);
        drop(stdout_child);
        drop(stderr_child);
        drop(stdin_parent);
        let job = Job::new().unwrap();
        job.assign_suspended_process(process.raw()).unwrap();
        // SAFETY: 测试只恢复刚创建的 suspended 主线程。
        assert_ne!(unsafe { ResumeThread(thread.raw()) }, u32::MAX);
        drop(thread);
        // SAFETY: process 在测试等待期间有效。
        assert_eq!(
            unsafe { WaitForSingleObject(process.raw(), 5000) },
            WAIT_OBJECT_0
        );
        drop(stdout_parent);
        drop(stderr_parent);
        assert!(shutdown_job(&job));
        assert_eq!(process_exit_code(process.raw()).unwrap(), 0);
    }

    #[test]
    #[ignore = "启动真实的固定 AppContainer 系统进程和最小环境块"]
    fn appcontainer_starts_with_minimal_environment() {
        native_launch_smoke();
    }
}
