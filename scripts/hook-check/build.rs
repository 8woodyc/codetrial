//! Holds every `cargo build` of a checkout to its git hooks being in effect,
//! installing them when they are not and failing the build when that does not
//! work.
//!
//! A crate of its own, which the server does not depend on, so that this script
//! can rerun on every build: Cargo notices a change by a file's mtime, and
//! neither a hook losing its executable bit nor a `core.hooksPath` set in a
//! global config touches one. Rerunning a build script recompiles its package,
//! which for this empty one costs a fraction of a second and for the server
//! would cost a full rebuild.

use std::path::PathBuf;
use std::process::{Command, exit};

fn main() {
    // A path that never exists is stale on every build, which is the point.
    println!("cargo:rerun-if-changed=.rerun-on-every-build");

    // For CI, which commits nothing and replays every message anyway, and for a
    // checkout that cannot take hooks at all, such as a read-only mount. Only
    // `1`, as documented: a `0` or `false` meant to keep the check would
    // otherwise turn it off.
    if std::env::var_os("CODETRIAL_SKIP_HOOKS").is_some_and(|value| value == "1") {
        return;
    }
    let Some(root) = checkout() else { return };

    // The installer is the one judge of what in effect means: present, current,
    // ours and executable, wherever core.hooksPath points today. It rewrites
    // only what is missing or stale, so a healthy checkout costs a read. Cargo
    // swallows a build script's stdout, which is right for the OK lines, and
    // shows its stderr when the script fails: there the installer says what it
    // refused, a hook of the user's or a shared hooks directory, and the fix.
    let installer = root.join("scripts/install-git-hooks.sh");
    let reason = match Command::new("sh")
        .arg(&installer)
        .current_dir(&root)
        .output()
    {
        Ok(output) if output.status.success() => return,
        Ok(output) => String::from_utf8_lossy(&output.stderr).into_owned(),
        Err(error) => format!("cannot run {}: {error}\n", installer.display()),
    };
    eprint!("{reason}");
    eprintln!("git hooks are not all in effect; settle the above and build again.");
    eprintln!("Where hooks cannot be installed, CODETRIAL_SKIP_HOOKS=1 skips this.");
    exit(1);
}

/// The checkout this crate sits in, two levels up, when git agrees it is the
/// top of a work tree. git skips a stray or half-copied `.git` and keeps
/// searching upward, and a tree unpacked inside some other repository must not
/// write hooks into that one, so an empty prefix is the test. A source archive
/// has no repository, and git refuses. So does a git that is missing, refused
/// by safe.directory or facing a broken `.git`, and that is a skip too: hooks
/// guard commits, and a git that cannot run here makes none.
fn checkout() -> Option<PathBuf> {
    let manifest = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR")?);
    let root = manifest.parent()?.parent()?.to_path_buf();
    let output = Command::new("git")
        .args(["rev-parse", "--show-prefix"])
        .current_dir(&root)
        .output()
        .ok()?;
    (output.status.success() && output.stdout.trim_ascii().is_empty()).then_some(root)
}
