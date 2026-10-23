//! Makes `rust-embed` see a changed asset tree, records the commit being
//! built, and installs the git hooks.
//!
//! The derive expands to one `include_bytes!` per file, and rustc records
//! those in dep-info, so *editing* an embedded asset already rebuilds. Files
//! that appear or disappear do not: they were never in the previous expansion,
//! so nothing names them and Cargo considers the crate fresh.
//!
//! The failure that costs a release: build once, run `scripts/fetch-vendor.sh`
//! for the first time, build again. Nothing recompiles, and the binary ships
//! without the 37 MB of vendored runtime. It boots, serves the page, and then
//! fails in the browser with a Python runtime that will not start.
//!
//! Cargo walks a directory given to `rerun-if-changed` in full, so the one
//! line covers the tree.

use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=web");
    set_build_commit();
    install_git_hooks();
}

/// The released binary must identify the source it was built from even when
/// the rolling `latest` tag has since moved. CI supplies its checkout SHA;
/// local builds use HEAD, and source archives without either say `unknown`.
/// Both are seven characters wide, because `--short` alone follows
/// `core.abbrev`, and a setting below seven would fail `hex_sha`.
fn set_build_commit() {
    println!("cargo:rerun-if-env-changed=GITHUB_SHA");
    let root = std::env::var_os("CARGO_MANIFEST_DIR")
        .map(std::path::PathBuf::from)
        .filter(|root| root.join(".git").exists());
    if let Some(root) = root.as_deref() {
        watch_git_head(root);
    }

    let commit = std::env::var("GITHUB_SHA")
        .ok()
        .and_then(|sha| hex_sha(&sha).map(|sha| sha[..7].to_ascii_lowercase()))
        .or_else(|| {
            root.as_deref()
                .and_then(|root| git_output(root, &["rev-parse", "--short=7", "HEAD"]))
                .and_then(|sha| hex_sha(&sha).map(str::to_ascii_lowercase))
        })
        .unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env=CODETRIAL_BUILD_COMMIT={commit}");
}

fn hex_sha(sha: &str) -> Option<&str> {
    let sha = sha.trim();
    (sha.len() >= 7 && sha.bytes().all(|byte| byte.is_ascii_hexdigit())).then_some(sha)
}

fn git_output(root: &std::path::Path, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(root)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout).ok()
}

/// HEAD holds the commit in a detached checkout and names a ref on a branch.
/// Watch that ref too, so the next local commit rebuilds the identifier even
/// when none of the source files changed.
fn watch_git_head(root: &std::path::Path) {
    let Some(head) = git_path(root, "HEAD") else {
        return;
    };
    println!("cargo:rerun-if-changed={}", head.display());
    if let Some(reference) = git_output(root, &["symbolic-ref", "-q", "HEAD"])
        && let Some(path) = git_path(root, reference.trim())
    {
        if path.is_file() {
            println!("cargo:rerun-if-changed={}", path.display());
        } else {
            // A packed or unborn branch has no loose ref yet. A commit creates
            // one without changing packed-refs, so watch the nearest existing
            // parent as well as packed-refs.
            if let Some(parent) = path.ancestors().skip(1).find(|parent| parent.is_dir()) {
                println!("cargo:rerun-if-changed={}", parent.display());
            }
            if let Some(packed) = git_path(root, "packed-refs")
                && packed.is_file()
            {
                println!("cargo:rerun-if-changed={}", packed.display());
            }
        }
    }
}

/// Joined onto `root` rather than asked for as absolute. `--path-format` needs
/// git 2.31, and an older one, such as the git in the Bullseye image the Linux
/// release builds in, echoes the unknown flag back as output and still exits 0.
/// The answer is relative to the working directory, which is `root`, or
/// already absolute, which `join` keeps as it is.
fn git_path(root: &std::path::Path, name: &str) -> Option<std::path::PathBuf> {
    git_output(root, &["rev-parse", "--git-path", name]).map(|path| root.join(path.trim()))
}

/// Installs a wrapper for each `scripts/git-*.sh` in the hooks directory of a
/// checkout, unless they are already in effect.
///
/// A hook nobody installed is a hook nobody runs, and the first person to find
/// that out is whoever reads a commit message the rules would have caught. A
/// build is the one step every contributor takes, so it is where installation
/// belongs; `make hooks` stays for anyone who wants it on its own. `cargo
/// check`, clippy and rust-analyzer share this script's run, so whichever comes
/// first in a fresh clone installs.
///
/// Never fails the build. A checkout can be read-only, a hooks directory can
/// belong to somebody else's tooling, and a Windows machine may have no `sh`.
/// None of that is a reason to refuse to compile, so a failure is a warning
/// and the build goes on.
fn install_git_hooks() {
    let root = std::env::var_os("CARGO_MANIFEST_DIR").map(std::path::PathBuf::from);
    let Some(root) = root else { return };

    // A worktree has a `.git` file rather than a directory, and both mean the
    // same thing here: this is a checkout, so it has hooks worth installing. A
    // packaged crate has neither.
    if !root.join(".git").exists() {
        return;
    }

    // Watched so that a changed installer, or a deleted hook, reruns this.
    // Without the second Cargo would keep the build script fresh and a removed
    // hook would stay removed until something else forced a rerun. The first
    // build after a fresh clone pays one extra rebuild, because installing the
    // hooks is itself a change to the directory being watched.
    //
    // Not the whole of scripts/, though adding a hook there is the one change
    // this does not notice. That directory holds every gate script in the
    // repository and each edit to one would rebuild the binary; `make hooks`
    // covers the rare case at no standing cost.
    //
    // A checkout without the installer has nothing to run, and a watch on a
    // path that does not exist would rerun this script on every build.
    if !root.join("scripts/install-git-hooks.sh").is_file() {
        return;
    }
    println!("cargo:rerun-if-changed=scripts/install-git-hooks.sh");
    run_hook_installer(&root);

    // Asked of git rather than assumed to be `.git/hooks`: a linked worktree
    // keeps its hooks with the common directory, and core.hooksPath moves them
    // anywhere. After the install rather than before, because a clone made with
    // an empty template has no hooks directory until the installer makes one,
    // and a directory that was not there to watch is never watched.
    if let Some(hooks) = git_path(&root, "hooks")
        && hooks.is_dir()
    {
        println!("cargo:rerun-if-changed={}", hooks.display());
    }
}

fn run_hook_installer(root: &std::path::Path) {
    let installer = root.join("scripts/install-git-hooks.sh");
    let run = |args: &[&str]| {
        Command::new("sh")
            .arg(&installer)
            .args(args)
            .current_dir(root)
            .output()
    };

    // Installed already, which is every build after the first: the check writes
    // nothing, so it leaves the watched directory alone and says nothing. The
    // installer stays the one judge of what installed means.
    //
    // No `sh` at all is the other quiet case, in practice a Git for Windows
    // install that did not put its shell on PATH. The release job builds a
    // Windows target on every push, and a warning nobody can act on teaches
    // people to skim them.
    match run(&["--check"]) {
        Ok(output) if output.status.success() => return,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
        _ => {}
    }

    // Cargo swallows a build script's stdout, which is right for the OK and
    // HOOK lines. The installer writes what needs a person to stderr instead: a
    // hook it refused to overwrite, a core.hooksPath shared with other
    // repositories, and how to fix each. That reaches the terminal line for
    // line or nothing does.
    let reason = match run(&[]) {
        Ok(output) if output.status.success() => return,
        Ok(output) => String::from_utf8_lossy(&output.stderr).into_owned(),
        Err(error) => error.to_string(),
    };
    for line in reason.lines().filter(|line| !line.trim().is_empty()) {
        println!("cargo::warning=git hooks: {line}");
    }
    println!("cargo::warning=git hooks are not all in effect; run make hooks");
}
