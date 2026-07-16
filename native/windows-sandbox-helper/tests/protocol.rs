use mewcode_windows_sandbox::protocol::{
    MAX_MESSAGE_BYTES, PROTOCOL_VERSION, Request, parse_request,
};

fn run_request(extra: &str) -> Vec<u8> {
    format!(
        concat!(
            "{{\"protocol_version\":{protocol_version},\"operation\":\"run\",",
            "\"request_id\":\"call-1\",\"timeout_ms\":20000,",
            "\"python_executable\":\"D:\\\\Python\\\\python.exe\",",
            "\"python_package_root\":\"D:\\\\project\\\\mewcode\",",
            "\"workspace\":\"D:\\\\project\",\"grants\":[],",
            "\"worker_payload\":{{}}{extra}}}\n"
        ),
        protocol_version = PROTOCOL_VERSION,
        extra = extra
    )
    .into_bytes()
}

#[test]
fn parses_all_supported_operations() {
    for (operation, expected) in [
        ("version", Request::Version),
        ("diagnose", Request::Diagnose),
        ("setup", Request::Setup),
    ] {
        let input =
            format!("{{\"protocol_version\":{PROTOCOL_VERSION},\"operation\":\"{operation}\"}}\n");
        assert_eq!(parse_request(input.as_bytes()).unwrap(), expected);
    }
    assert!(matches!(
        parse_request(&run_request("")),
        Ok(Request::Run(_))
    ));
}

#[test]
fn rejects_unknown_fields_and_operations() {
    let extra = format!(
        "{{\"protocol_version\":{PROTOCOL_VERSION},\"operation\":\"version\",\"extra\":true}}\n"
    );
    assert_eq!(
        parse_request(extra.as_bytes()).unwrap_err().code,
        "sandbox_protocol_error"
    );

    let unknown =
        format!("{{\"protocol_version\":{PROTOCOL_VERSION},\"operation\":\"execute\"}}\n");
    assert_eq!(
        parse_request(unknown.as_bytes()).unwrap_err().message,
        "未知操作"
    );
}

#[test]
fn rejects_version_mismatch_and_invalid_run_bounds() {
    let version = format!(
        "{{\"protocol_version\":{},\"operation\":\"version\"}}\n",
        PROTOCOL_VERSION + 1
    );
    assert_eq!(
        parse_request(version.as_bytes()).unwrap_err().message,
        "协议版本不匹配"
    );

    let timeout = String::from_utf8(run_request(""))
        .unwrap()
        .replace("20000", "0");
    assert_eq!(
        parse_request(timeout.as_bytes()).unwrap_err().message,
        "timeout_ms 超出允许范围"
    );

    let empty_id = String::from_utf8(run_request(""))
        .unwrap()
        .replace("call-1", "");
    assert_eq!(
        parse_request(empty_id.as_bytes()).unwrap_err().message,
        "request_id 非法"
    );
}

#[test]
fn rejects_multiple_lines_truncation_and_oversize() {
    assert_eq!(
        parse_request(b"{}\n{}\n").unwrap_err().message,
        "请求必须是单行 JSON"
    );
    assert_eq!(
        parse_request(b"{}").unwrap_err().message,
        "请求必须是单行 JSON"
    );
    let oversized = vec![b'x'; MAX_MESSAGE_BYTES + 2];
    assert_eq!(
        parse_request(&oversized).unwrap_err().message,
        "请求为空或超过大小限制"
    );
}

#[test]
fn rejects_extra_run_field() {
    let request = run_request(",\"extra\":true");
    assert_eq!(
        parse_request(&request).unwrap_err().message,
        "运行请求字段无效"
    );
}
