use crate::acl::recover_pending_transactions;
use crate::profile::{ProfileStatus, prepare_profile, query_profile};
use crate::protocol::HELPER_VERSION;
use crate::state::{StatePaths, load_state};
use crate::user_sid::UserSid;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HelperDiagnostic {
    pub state: &'static str,
    pub code: &'static str,
    pub message: &'static str,
    pub remediation: &'static str,
}

impl HelperDiagnostic {
    pub const fn ready() -> Self {
        Self {
            state: "ready",
            code: "ready",
            message: "Windows 原生沙箱已准备",
            remediation: "",
        }
    }

    pub const fn setup_required() -> Self {
        Self {
            state: "setup_required",
            code: "setup_required",
            message: "Windows 原生沙箱尚未准备",
            remediation: "运行 mewcode sandbox setup",
        }
    }

    pub const fn broken(message: &'static str) -> Self {
        Self {
            state: "broken",
            code: "setup_failed",
            message,
            remediation: "运行 mewcode sandbox diagnose 并检查系统策略",
        }
    }
}

pub fn diagnose() -> HelperDiagnostic {
    let owner = match UserSid::current() {
        Ok(owner) => owner,
        Err(_) => return HelperDiagnostic::broken("无法验证当前 Windows 用户"),
    };
    let paths = match StatePaths::for_current_user(&owner) {
        Ok(paths) => paths,
        Err(_) => return HelperDiagnostic::broken("无法定位 Windows 沙箱状态"),
    };
    diagnose_at(&owner, &paths)
}

pub fn setup() -> HelperDiagnostic {
    let owner = match UserSid::current() {
        Ok(owner) => owner,
        Err(_) => return HelperDiagnostic::broken("无法验证当前 Windows 用户"),
    };
    let paths = match StatePaths::for_current_user(&owner) {
        Ok(paths) => paths,
        Err(_) => return HelperDiagnostic::broken("无法定位 Windows 沙箱状态"),
    };
    if prepare_profile(&owner, &paths).is_err() {
        return HelperDiagnostic::broken("Windows AppContainer profile 准备失败");
    }
    diagnose_at(&owner, &paths)
}

pub fn diagnose_at(owner: &UserSid, paths: &StatePaths) -> HelperDiagnostic {
    if !paths.state_file.exists() {
        return HelperDiagnostic::setup_required();
    }
    let state = match load_state(paths, owner) {
        Ok(state) => state,
        Err(_) => return HelperDiagnostic::broken("Windows 沙箱状态无效"),
    };
    if state.helper_version != HELPER_VERSION {
        return HelperDiagnostic::broken("Windows 沙箱状态版本不匹配");
    }
    if recover_pending_transactions(paths).is_err() {
        return HelperDiagnostic::broken("Windows 沙箱 ACL 事务恢复失败");
    }
    match query_profile(Some(&state)) {
        Ok(ProfileStatus::Matching { .. }) => HelperDiagnostic::ready(),
        Ok(ProfileStatus::Missing | ProfileStatus::Mismatch) | Err(_) => {
            HelperDiagnostic::broken("Windows AppContainer profile 状态不一致")
        }
    }
}
