use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fmt;

pub const PROTOCOL_VERSION: u32 = 1;
pub const MAX_MESSAGE_BYTES: usize = 4 * 1024 * 1024;
pub const HELPER_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, Clone, PartialEq)]
pub enum Request {
    Version,
    Diagnose,
    Setup,
    Run(RunRequest),
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunRequest {
    pub protocol_version: u32,
    pub operation: RunOperation,
    pub request_id: String,
    pub timeout_ms: u64,
    pub python_executable: String,
    pub workspace: String,
    pub grants: Vec<Grant>,
    pub worker_payload: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
pub enum RunOperation {
    #[serde(rename = "run")]
    Run,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    pub path: String,
    pub mode: AccessMode,
    pub kind: GrantKind,
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccessMode {
    Read,
    Write,
    Execute,
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantKind {
    File,
    Directory,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolError {
    pub code: &'static str,
    pub message: &'static str,
}

impl ProtocolError {
    const fn new(message: &'static str) -> Self {
        Self {
            code: "sandbox_protocol_error",
            message,
        }
    }
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.message)
    }
}

impl std::error::Error for ProtocolError {}

#[derive(Debug, Deserialize)]
struct OperationProbe {
    protocol_version: u32,
    operation: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ManagementRequest {
    protocol_version: u32,
    operation: ManagementOperation,
}

#[derive(Debug, Clone, Copy, Deserialize)]
enum ManagementOperation {
    #[serde(rename = "version")]
    Version,
    #[serde(rename = "diagnose")]
    Diagnose,
    #[serde(rename = "setup")]
    Setup,
}

pub fn parse_request(input: &[u8]) -> Result<Request, ProtocolError> {
    if input.is_empty() || input.len() > MAX_MESSAGE_BYTES + 1 {
        return Err(ProtocolError::new("请求为空或超过大小限制"));
    }
    if !input.ends_with(b"\n") || input.iter().filter(|byte| **byte == b'\n').count() != 1 {
        return Err(ProtocolError::new("请求必须是单行 JSON"));
    }
    let payload = &input[..input.len() - 1];
    let value: Value = serde_json::from_slice(payload)
        .map_err(|_| ProtocolError::new("请求不是有效 UTF-8 JSON"))?;
    let probe: OperationProbe = serde_json::from_value(value.clone())
        .map_err(|_| ProtocolError::new("请求字段不完整或包含未知字段"))?;
    validate_version(probe.protocol_version)?;

    match probe.operation.as_str() {
        "version" | "diagnose" | "setup" => {
            let management: ManagementRequest = serde_json::from_value(value)
                .map_err(|_| ProtocolError::new("管理请求字段无效"))?;
            validate_version(management.protocol_version)?;
            Ok(match management.operation {
                ManagementOperation::Version => Request::Version,
                ManagementOperation::Diagnose => Request::Diagnose,
                ManagementOperation::Setup => Request::Setup,
            })
        }
        "run" => {
            let run: RunRequest = serde_json::from_value(value)
                .map_err(|_| ProtocolError::new("运行请求字段无效"))?;
            validate_run_request(&run)?;
            Ok(Request::Run(run))
        }
        _ => Err(ProtocolError::new("未知操作")),
    }
}

fn validate_version(version: u32) -> Result<(), ProtocolError> {
    if version != PROTOCOL_VERSION {
        return Err(ProtocolError::new("协议版本不匹配"));
    }
    Ok(())
}

fn validate_run_request(request: &RunRequest) -> Result<(), ProtocolError> {
    validate_version(request.protocol_version)?;
    if request.request_id.is_empty()
        || request.request_id.len() > 256
        || request.request_id.contains('\0')
    {
        return Err(ProtocolError::new("request_id 非法"));
    }
    if request.timeout_ms == 0 || request.timeout_ms > 24 * 60 * 60 * 1000 {
        return Err(ProtocolError::new("timeout_ms 超出允许范围"));
    }
    validate_text_path(&request.python_executable)?;
    validate_text_path(&request.workspace)?;
    if !request.worker_payload.is_object() {
        return Err(ProtocolError::new("worker_payload 必须是对象"));
    }
    for grant in &request.grants {
        validate_text_path(&grant.path)?;
    }
    Ok(())
}

fn validate_text_path(path: &str) -> Result<(), ProtocolError> {
    if path.is_empty() || path.contains('\0') {
        return Err(ProtocolError::new("路径不能为空或包含 NUL"));
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct VersionResponse<'a> {
    pub protocol_version: u32,
    pub helper_version: &'a str,
}

#[derive(Debug, Serialize)]
pub struct DiagnosticResponse<'a> {
    pub protocol_version: u32,
    pub state: &'a str,
    pub backend: &'a str,
    pub code: &'a str,
    pub message: &'a str,
    pub remediation: &'a str,
    pub component_version: Option<&'a str>,
}

#[derive(Debug, Serialize)]
pub struct RunResponse<'a> {
    pub protocol_version: u32,
    pub request_id: &'a str,
    pub status: &'a str,
    pub worker_result: Option<Value>,
    pub error: Option<ErrorBody<'a>>,
}

#[derive(Debug, Serialize)]
pub struct ErrorBody<'a> {
    pub code: &'a str,
    pub message: &'a str,
}

#[derive(Debug, Serialize)]
pub struct ProtocolErrorResponse<'a> {
    pub protocol_version: u32,
    pub code: &'a str,
    pub message: &'a str,
}

pub fn to_json_line<T: Serialize>(value: &T) -> Result<Vec<u8>, ProtocolError> {
    let mut output = serde_json::to_vec(value).map_err(|_| ProtocolError::new("响应无法序列化"))?;
    if output.len() > MAX_MESSAGE_BYTES {
        return Err(ProtocolError::new("响应超过大小限制"));
    }
    output.push(b'\n');
    Ok(output)
}
