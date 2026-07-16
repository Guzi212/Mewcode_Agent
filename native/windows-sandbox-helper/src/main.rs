use mewcode_windows_sandbox::protocol::{
    DiagnosticResponse, ErrorBody, HELPER_VERSION, PROTOCOL_VERSION, ProtocolErrorResponse,
    Request, RunResponse, parse_request, to_json_line,
};
#[cfg(windows)]
use mewcode_windows_sandbox::{diagnostics, runner};
use std::io::{self, Read, Write};

fn main() {
    let exit_code = match run() {
        Ok(()) => 0,
        Err(()) => 2,
    };
    std::process::exit(exit_code);
}

fn run() -> Result<(), ()> {
    let mut input = Vec::new();
    io::stdin()
        .take((4 * 1024 * 1024 + 2) as u64)
        .read_to_end(&mut input)
        .map_err(|_| ())?;
    let response = match parse_request(&input) {
        Ok(Request::Version) => to_json_line(&mewcode_windows_sandbox::protocol::VersionResponse {
            protocol_version: PROTOCOL_VERSION,
            helper_version: HELPER_VERSION,
        }),
        Ok(Request::Diagnose) => to_json_line(&diagnostic_response(diagnostics::diagnose())),
        Ok(Request::Setup) => to_json_line(&diagnostic_response(diagnostics::setup())),
        Ok(Request::Run(request)) => {
            let result = runner::run(&request);
            let error = result
                .error_code
                .zip(result.error_message)
                .map(|(code, message)| ErrorBody { code, message });
            to_json_line(&RunResponse {
                protocol_version: PROTOCOL_VERSION,
                request_id: &request.request_id,
                status: result.status,
                worker_result: result.worker_result,
                error,
            })
        }
        Err(error) => {
            let response = ProtocolErrorResponse {
                protocol_version: PROTOCOL_VERSION,
                code: error.code,
                message: error.message,
            };
            write_response(to_json_line(&response).map_err(|_| ())?)?;
            return Err(());
        }
    };
    write_response(response.map_err(|_| ())?)
}

#[cfg(windows)]
fn diagnostic_response(diagnostic: diagnostics::HelperDiagnostic) -> DiagnosticResponse<'static> {
    DiagnosticResponse {
        protocol_version: PROTOCOL_VERSION,
        state: diagnostic.state,
        backend: "windows-appcontainer",
        code: diagnostic.code,
        message: diagnostic.message,
        remediation: diagnostic.remediation,
        component_version: Some(HELPER_VERSION),
    }
}

fn write_response(response: Vec<u8>) -> Result<(), ()> {
    let mut stdout = io::stdout().lock();
    stdout.write_all(&response).map_err(|_| ())?;
    stdout.flush().map_err(|_| ())
}
