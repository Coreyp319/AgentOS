//! Scope/cgroup reclaim for externally-launched, flatpak-scoped GPU holders (ADR-0022 Phase 1).
//!
//! The lease's normal reclaim is `sigkill_group(pid)` on an agentosd-*spawned* child (its own process
//! group, `lease.rs`). A Nimbus Blender **lane** is launched by `blender-mcp.sh` via `flatpak run`, and
//! flatpak reparents it into a transient **systemd user scope** (`app-flatpak-…​.scope`) whose processes
//! are NOT in the launcher's process group — so a negative-PID group SIGKILL can't reach them. cgroup v2
//! gives the robust per-lane primitive instead: writing `"1"` to the scope's `cgroup.kill` SIGKILLs the
//! whole subtree atomically. That file is owned + writable by the user (systemd delegates the user's
//! cgroup subtree), so the agentosd `--user` daemon needs **no privilege**.
//!
//! ## Hardening (resource-safety + security review, 2026-06-17 — see the design doc)
//!
//! The first cut guarded only the path *shape* (`.scope` leaf). That is **insufficient**: ordinary user
//! apps (`init.scope`, the editor's `app-code-*.scope`, `konsole`, Spotify) are ALSO `.scope` leaves with
//! a user-writable `cgroup.kill` — a shape-only guard would let a reclaim SIGKILL an app the user is in.
//! This module now enforces *identity*, not just shape:
//!
//!   1. **Allowlist (B1).** Only a flatpak Blender lane scope (`app-flatpak-org.blender.Blender-*.scope`)
//!      is reclaimable — [`is_lane_scope`] / [`resolve_lane_scope`]. The editor/terminal/browser can never
//!      be the target.
//!   2. **Daemon-derived (B1).** The lease resolves the scope ITSELF from a PID via [`resolve_lane_scope`]
//!      (`/proc/<pid>/cgroup`) — it never trusts a caller-supplied path string.
//!   3. **fd-pinning (B3).** [`open_scope_dir`] pins the scope's cgroup directory as a dir-fd at adopt
//!      time; [`kill_scope_at`] / [`scope_is_empty_at`] act via `openat` on that fd, so a recycled scope
//!      name (PID/instance-id reuse) can never redirect the kill — if the original cgroup was removed,
//!      `openat` returns `ENOENT` and we fail closed.
//!
//! The pure parse + the `.scope`-only guard ([`parse_scope_path`]) are unit-tested; the fd-backed IO is
//! integration-only (a THROWAWAY systemd scope, never the user's live authoring lanes — see the design
//! doc's test plan). The lease holder model + evict/supervise wiring is in `lease.rs`.

use std::ffi::CString;
use std::fs::File;
use std::io::{self, ErrorKind, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::path::PathBuf;

/// cgroup v2 mount root. The scope path from `/proc/<pid>/cgroup` is appended to this.
const CGROUP_ROOT: &str = "/sys/fs/cgroup";

/// The ONLY scope-unit prefixes a reclaim may target (review B1). The `.scope` shape guard alone is not
/// enough — a reclaim must be provably a forge lane, never an app the user is working in. A flatpak app
/// id is `app-flatpak-<app-id>-<instance>.scope`; the Nimbus forge runs `org.blender.Blender`. Adding a
/// new creative-app lane (ADR-0022 §8 Unreal, a future Cycles flatpak) is a new entry HERE — a fixed,
/// auditable allowlist, never an env knob (an env-tunable kill allowlist would defeat the guarantee).
const LANE_SCOPE_PREFIXES: &[&str] = &["app-flatpak-org.blender.Blender-"];

/// A resolved systemd-user scope: enough to derive the cgroup paths and to identify the lane. The actual
/// SIGKILL / liveness check go through a *pinned dir-fd* ([`open_scope_dir`]), not these paths, so a
/// recycled scope name can't redirect a kill (B3). The paths remain for logging + path derivation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopeHandle {
    /// The cgroup v2 path from `/proc/<pid>/cgroup` after `0::`, e.g.
    /// `/user.slice/user-1000.slice/user@1000.service/app.slice/app-flatpak-org.blender.Blender-NNNN.scope`.
    pub cgroup_path: String,
    /// The systemd scope unit (the path's last component), e.g. `app-flatpak-…-NNNN.scope`.
    pub scope_unit: String,
}

impl ScopeHandle {
    /// `/sys/fs/cgroup<cgroup_path>` — the scope's cgroup v2 directory (pinned as a dir-fd for the kill).
    pub fn cgroup_dir_path(&self) -> PathBuf {
        PathBuf::from(format!("{CGROUP_ROOT}{}", self.cgroup_path))
    }

    /// `/sys/fs/cgroup<cgroup_path>/cgroup.kill` — for logging only; the kill writes via the pinned fd.
    pub fn cgroup_kill_path(&self) -> PathBuf {
        self.cgroup_dir_path().join("cgroup.kill")
    }
}

/// Is `unit` an allowlisted forge-lane scope (B1)? True only for a `.scope` whose name carries a
/// [`LANE_SCOPE_PREFIXES`] prefix — so the editor/terminal/browser, which are also `.scope` leaves, are
/// never reclaimable.
pub fn is_lane_scope(unit: &str) -> bool {
    unit.ends_with(".scope") && LANE_SCOPE_PREFIXES.iter().any(|p| unit.starts_with(p))
}

/// Parse a `/proc/<pid>/cgroup` body into a `ScopeHandle`, or `None`.
///
/// Requires a pure cgroup-v2 line (`0::/path`); hybrid/v1 lines (`N:ctrl:/path`) are ignored. The
/// resulting path must pass [`parse_scope_path`]'s `.scope`-only safety guard. NOTE: this does not apply
/// the lane allowlist — that's [`resolve_lane_scope`]'s job (so tests can pin the shape guard alone).
pub fn parse_proc_cgroup(body: &str) -> Option<ScopeHandle> {
    let path = body.lines().find_map(|l| l.strip_prefix("0::"))?;
    parse_scope_path(path)
}

/// Validate a cgroup path and split out the scope unit — the **shape** safety guard.
///
/// Returns `Some` ONLY for an absolute path whose leaf unit ends in `.scope` (and isn't the bare
/// `".scope"`), with no traversal (`/..`, `//`). This makes it impossible for a reclaim to target a
/// `.slice`, the cgroup root, or `user@…​.service` — so a reclaim can never SIGKILL the session tree.
/// The *identity* guard (only a Blender lane, not the editor) is [`is_lane_scope`], applied at adopt.
pub fn parse_scope_path(path: &str) -> Option<ScopeHandle> {
    let path = path.trim();
    if !path.starts_with('/') || path.contains("/..") || path.contains("//") {
        return None;
    }
    let unit = path.rsplit('/').next().unwrap_or_default();
    if unit == ".scope" || !unit.ends_with(".scope") {
        return None; // only a leaf systemd *scope* is reclaimable — never a slice/service/root
    }
    Some(ScopeHandle { cgroup_path: path.to_string(), scope_unit: unit.to_string() })
}

/// Resolve a PID to a reclaimable **lane** scope (B1): the daemon reads `/proc/<pid>/cgroup` ITSELF
/// (never a caller-supplied path), applies the shape guard, AND requires the allowlist prefix. `None`
/// for any non-lane PID — so `AdoptScope` of an arbitrary PID can never arm a kill on the editor.
pub fn resolve_lane_scope(pid: u32) -> Option<ScopeHandle> {
    let body = std::fs::read_to_string(format!("/proc/{pid}/cgroup")).ok()?;
    let handle = parse_proc_cgroup(&body)?;
    is_lane_scope(&handle.scope_unit).then_some(handle)
}

/// Pin the scope's cgroup directory as a dir-fd at adopt time (B3). All later reclaim/liveness checks go
/// through this fd, so a scope name recycled to a *different* cgroup between adopt and kill can't redirect
/// the action — `openat` on the pinned (now-removed) directory fails `ENOENT` and we fail closed.
/// `O_DIRECTORY` ensures we never pin a regular file by mistake.
pub fn open_scope_dir(handle: &ScopeHandle) -> io::Result<File> {
    let dir = handle.cgroup_dir_path();
    let c = CString::new(dir.as_os_str().as_encoded_bytes())
        .map_err(|_| io::Error::new(ErrorKind::InvalidInput, "cgroup path has interior NUL"))?;
    // SAFETY: a plain open(2) with constant flags; we adopt the returned fd into an OwnedFd via File.
    let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: `fd` is a fresh, owned, valid descriptor we just created; File takes sole ownership.
    Ok(unsafe { File::from_raw_fd(fd) })
}

/// SIGKILL the scope's whole cgroup subtree via the pinned dir-fd (`openat(dir, "cgroup.kill")` ← `"1"`).
///
/// Returns `Ok(true)` if the kill was written, `Ok(false)` if the scope is already gone (`ENOENT` —
/// idempotent: the lane exited or the cgroup was reaped, so there is nothing to kill). Any other error
/// (e.g. an older kernel without `cgroup.kill`) is surfaced so the caller can fail *open* and log, never
/// panic. Integration-only — never run against a live authoring lane.
pub fn kill_scope_at(dir: &File) -> io::Result<bool> {
    let mut kill = match openat_in(dir, "cgroup.kill", libc::O_WRONLY) {
        Ok(f) => f,
        Err(e) if is_gone(&e) => return Ok(false), // scope already gone/recycled → nothing to kill
        Err(e) => return Err(e),
    };
    kill.write_all(b"1")?;
    Ok(true)
}

/// True when the scope has no live processes (natural lane exit → auto-release), read via the pinned fd.
///
/// Error handling is asymmetric on purpose (review C2): only a *gone* scope (`ENOENT`/`ESTALE` on the
/// pinned fd) counts as empty; a transient read error (EINTR/EACCES flap) returns `false` — "assume
/// alive" — so a hiccup can never false-release a lease whose lane is still holding VRAM. The TTL backstop
/// (`lease.rs`) covers a genuinely hung lane.
pub fn scope_is_empty_at(dir: &File) -> bool {
    let mut procs = match openat_in(dir, "cgroup.procs", libc::O_RDONLY) {
        Ok(f) => f,
        Err(e) if is_gone(&e) => return true, // scope vanished == empty == lane exited
        Err(_) => return false,               // transient open error → assume alive
    };
    let mut s = String::new();
    match procs.read_to_string(&mut s) {
        Ok(_) => s.split_whitespace().next().is_none(),
        Err(e) if is_gone(&e) => true,
        Err(_) => false, // transient read error → assume alive
    }
}

/// `openat(dirfd, name, flags)` → an owned `File`. The kill/empty checks go through the pinned dir-fd so a
/// recycled scope name can't redirect them (B3).
fn openat_in(dir: &File, name: &str, flags: i32) -> io::Result<File> {
    let c = CString::new(name).expect("constant cgroup attr name has no NUL");
    // SAFETY: `dir` is a live dir-fd we own; `name` is a constant NUL-terminated attr name; the returned
    // fd is fresh + owned and adopted into a File.
    let fd = unsafe { libc::openat(dir.as_raw_fd(), c.as_ptr(), flags | libc::O_CLOEXEC) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

/// Does this error mean "the pinned cgroup is gone" (lane exited / scope reaped or recycled)? Such an
/// error is the SAFE signal (empty / nothing-to-kill); everything else is treated as "still alive".
fn is_gone(e: &io::Error) -> bool {
    matches!(e.kind(), ErrorKind::NotFound)
        || e.raw_os_error() == Some(libc::ESTALE)
        || e.raw_os_error() == Some(libc::ENODEV)
}

#[cfg(test)]
mod tests {
    use super::*;

    // The real lane scope observed on this box (ADR-0022 v2 discovery).
    const BLENDER_CG: &str =
        "/user.slice/user-1000.slice/user@1000.service/app.slice/app-flatpak-org.blender.Blender-3630677696.scope";

    #[test]
    fn parses_a_real_flatpak_blender_scope_and_derives_the_paths() {
        let body = format!("0::{BLENDER_CG}\n");
        let h = parse_proc_cgroup(&body).expect("a flatpak .scope must parse");
        assert_eq!(h.scope_unit, "app-flatpak-org.blender.Blender-3630677696.scope");
        assert_eq!(h.cgroup_path, BLENDER_CG);
        assert_eq!(h.cgroup_dir_path().to_str().unwrap(), format!("/sys/fs/cgroup{BLENDER_CG}"));
        assert_eq!(
            h.cgroup_kill_path().to_str().unwrap(),
            format!("/sys/fs/cgroup{BLENDER_CG}/cgroup.kill")
        );
    }

    #[test]
    fn the_shape_guard_refuses_anything_that_is_not_a_leaf_scope() {
        // A reclaim can NEVER target a slice/service/root and kill the session.
        assert!(parse_scope_path("/user.slice").is_none(), "a slice is not reclaimable");
        assert!(parse_scope_path("/user.slice/user-1000.slice").is_none());
        assert!(parse_scope_path("/user.slice/user-1000.slice/user@1000.service").is_none());
        assert!(parse_scope_path("/").is_none(), "the cgroup root is not reclaimable");
        assert!(parse_scope_path("").is_none());
        assert!(parse_scope_path("relative/foo.scope").is_none(), "must be absolute");
        assert!(parse_scope_path(".scope").is_none(), "bare .scope is not a path");
        assert!(parse_scope_path("/x.scope/").is_none(), "trailing slash → empty leaf");
    }

    #[test]
    fn the_shape_guard_rejects_path_traversal() {
        assert!(parse_scope_path("/a/../../etc/foo.scope").is_none());
        assert!(parse_scope_path("/a//b.scope").is_none());
    }

    #[test]
    fn only_pure_cgroup_v2_lines_are_accepted() {
        // Hybrid/v1 layout: no `0::` line → not reclaimable (we don't guess controllers).
        assert!(parse_proc_cgroup("12:pids:/user.slice/app-foo.scope\n3:cpu:/\n").is_none());
        // A v2 line that is a slice is still refused by the leaf guard.
        assert!(parse_proc_cgroup("0::/user.slice\n").is_none());
        // Trailing whitespace/newlines are tolerated.
        let h = parse_proc_cgroup("0::/app.slice/x-1.scope\n").unwrap();
        assert_eq!(h.scope_unit, "x-1.scope");
    }

    #[test]
    fn the_lane_allowlist_admits_only_the_blender_flatpak_scope() {
        // The load-bearing identity guard (B1): the editor/terminal/browser are `.scope` leaves too, and
        // their cgroup.kill is user-writable — but they are NOT lanes, so they can never be adopted/killed.
        assert!(is_lane_scope("app-flatpak-org.blender.Blender-3630677696.scope"));
        assert!(!is_lane_scope("app-code-2882.scope"), "the editor is not a lane");
        assert!(!is_lane_scope("app-org.kde.konsole-2632.scope"), "the terminal is not a lane");
        assert!(!is_lane_scope("app-flatpak-com.spotify.Client-1234.scope"), "another flatpak is not a lane");
        assert!(!is_lane_scope("init.scope"), "init is not a lane");
        assert!(!is_lane_scope("app-flatpak-org.blender.Blender-1.slice"), "must be a .scope");
        // A passing shape guard but a non-lane unit ⇒ resolve_lane_scope would reject it.
        let editor = parse_proc_cgroup("0::/user.slice/app.slice/app-code-2882.scope\n").unwrap();
        assert!(!is_lane_scope(&editor.scope_unit));
        let lane = parse_proc_cgroup(&format!("0::{BLENDER_CG}\n")).unwrap();
        assert!(is_lane_scope(&lane.scope_unit));
    }

    #[test]
    fn is_gone_classifies_only_gone_errors_as_safe_empty() {
        assert!(is_gone(&io::Error::from(ErrorKind::NotFound)));
        assert!(is_gone(&io::Error::from_raw_os_error(libc::ESTALE)));
        // A permission/interrupt flap is NOT "gone" — it must read as still-alive (C2).
        assert!(!is_gone(&io::Error::from(ErrorKind::PermissionDenied)));
        assert!(!is_gone(&io::Error::from(ErrorKind::Interrupted)));
    }

    /// Integration test for the DESTRUCTIVE reclaim primitive, against a THROWAWAY systemd `--user` scope
    /// wrapping a harmless `sleep` — NEVER the user's live authoring lanes (ADR-0022 Phase 1 test plan).
    /// The scope is named with the lane allowlist prefix so the real `resolve_lane_scope` path is
    /// exercised, but the body is just `sleep`. `#[ignore]` because it needs a user systemd + cgroup v2;
    /// run explicitly:
    ///   cargo test -p agentosd --bins -- --ignored reclaim_primitive
    #[test]
    #[ignore = "spawns a throwaway systemd --user scope; needs cgroup v2 + user systemd"]
    fn reclaim_primitive_kills_a_throwaway_blender_named_scope_via_pinned_fd() {
        use std::process::Command;
        use std::thread::sleep;
        use std::time::Duration;

        let unit = format!("app-flatpak-org.blender.Blender-test{}.scope", std::process::id());
        let mut runner = Command::new("systemd-run")
            .args(["--user", "--scope", &format!("--unit={unit}"), "--quiet", "sleep", "600"])
            .spawn()
            .expect("systemd-run --user must be available");

        // Resolve the scope's pid via its ControlGroup (robust — no PID guessing).
        let mut lane_pid = 0u32;
        for _ in 0..50 {
            let out = Command::new("systemctl")
                .args(["--user", "show", &unit, "-p", "ControlGroup", "--value"])
                .output();
            if let Ok(o) = out {
                let cg = String::from_utf8_lossy(&o.stdout).trim().to_string();
                if !cg.is_empty() {
                    if let Ok(body) = std::fs::read_to_string(format!("/sys/fs/cgroup{cg}/cgroup.procs")) {
                        if let Some(p) = body.split_whitespace().next().and_then(|s| s.parse().ok()) {
                            lane_pid = p;
                            break;
                        }
                    }
                }
            }
            sleep(Duration::from_millis(100));
        }
        assert!(lane_pid != 0, "could not resolve the throwaway scope's pid");

        // Exercise the REAL adopt path: resolve+allowlist → pin dir-fd → cgroup.kill → confirm empty.
        let handle = resolve_lane_scope(lane_pid)
            .expect("a Blender-named .scope must resolve AND pass the allowlist");
        assert!(is_lane_scope(&handle.scope_unit));
        let dir = open_scope_dir(&handle).expect("pin the scope cgroup dir");
        assert!(!scope_is_empty_at(&dir), "the lane should be running before the kill");
        assert!(kill_scope_at(&dir).expect("cgroup.kill must succeed"), "kill writes (scope was live)");

        // cgroup.kill SIGKILLs the whole subtree atomically → the scope empties.
        let mut emptied = false;
        for _ in 0..50 {
            if scope_is_empty_at(&dir) {
                emptied = true;
                break;
            }
            sleep(Duration::from_millis(100));
        }
        assert!(emptied, "the throwaway scope must empty after cgroup.kill");

        // A second kill on the now-gone scope is idempotent via the pinned fd (Ok(false), nothing to
        // kill) — never an error, and never able to hit a recycled scope at the same name (B3).
        assert_eq!(kill_scope_at(&dir).ok(), Some(false), "kill on a gone scope is a no-op");
        assert!(scope_is_empty_at(&dir), "a gone scope reads empty via the pinned fd");

        let _ = Command::new("systemctl").args(["--user", "stop", &unit]).output(); // belt-and-suspenders
        let _ = runner.wait();
    }
}
