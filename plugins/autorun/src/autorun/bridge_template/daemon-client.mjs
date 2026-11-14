// Shared in-process transport for JavaScript/TypeScript harness adapters.
//
// Decisions stay in Python: the daemon normally, the hook client when degraded.
// This module owns JSON transport, bounded fallback, and response extraction.

import { existsSync, readFileSync } from "node:fs";
import { createConnection } from "node:net";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";

const PROTOCOL_VERSION = 1;
// = autorun.config.HOOK_DEADLINE_PAYLOAD_KEY; test_client_fail_closed.py pins
// it. Epoch seconds: the daemon converts to its own monotonic clock, because
// Node's hrtime is not Python's monotonic clock on macOS after a sleep.
const HOOK_DEADLINE_PAYLOAD_KEY = "_autorun_hook_deadline_epoch";

function daemonTarget(socketPath, portFile) {
  try {
    if (socketPath && existsSync(socketPath)) return { path: socketPath };
  } catch {
    // The socket is not usable.
  }
  try {
    const port = Number(readFileSync(portFile, "utf8").trim());
    if (Number.isInteger(port) && port > 0 && port <= 65535) {
      // Loopback reaches every local process, so the daemon serves only a
      // client that first sends the token it published beside the port
      // (= ipc.connection_preamble(); test_ipc_tcp_auth.py).
      const token = readFileSync(join(dirname(portFile), "daemon.token"), "utf8").trim();
      return { host: "127.0.0.1", port, preamble: `${token}\n` };
    }
  } catch {
    // No valid loopback port file.
  }
  return null;
}

// AUTORUN_DISABLE stands autorun down, with the same truthy set as
// hooks/hook_entry.py. Read here because these bridges ask the daemon first and
// never reach hook_entry while it is up, so a denying daemon could not be
// escaped through the exit every fail-closed reason names. Exported so a
// harness adapter can tell a stood-down bridge from an unreachable daemon.
export function autorunDisabled() {
  const value = String(process.env.AUTORUN_DISABLE ?? "").trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

// Denial-only copy of autorun.client.RESPONSE_TIMEOUT_GUIDANCE, pinned by tests.
const RESPONSE_TIMEOUT_GUIDANCE = "Blocking this tool call because the autorun daemon took the request but did not answer in time and a local decision is unavailable or overload_policy=block is selected. Wait and retry when autorun answers again; do not restart the daemon. If this persists, tell the user; AUTORUN_DISABLE=1 at harness launch stands autorun down.";

export function denialReason(response) {
  if (!response) return null;
  const specific = response.hookSpecificOutput ?? {};
  const decision = specific.permissionDecision ?? response.decision;
  if (decision !== "deny" && decision !== "block") return null;
  return specific.permissionDecisionReason || response.reason || "blocked by autorun";
}

export function responseMessage(response) {
  return (
    response?.systemMessage ??
    response?.reason ??
    response?.hookSpecificOutput?.additionalContext ??
    response?.hookSpecificOutput?.permissionDecisionReason ??
    ""
  );
}

export function degradedNotice(response) {
  const message = responseMessage(response);
  return !denialReason(response) && message.includes("[AR_EVENT_V1:degraded_decision]") ? message : "";
}

// Data derived from config.DEFAULT_INTEGRATIONS, pinned by test_review_bridge_screen_data_pinned.
// [guard name, literal tokens, screen regex or null] = config._critical_guards_by_name().
const CRITICAL_GUARDS = [["rm", ["rm"], null], ["rm -rf", ["rm", "-rf"], null], ["git reset --hard", ["git", "reset", "--hard"], null], ["git checkout .", ["git", "checkout", "."], null], ["git checkout --", ["git", "checkout", "--"], null], ["git checkout", ["git", "checkout"], null], ["git restore", ["git", "restore"], null], ["git stash drop", ["git", "stash", "drop"], null], ["git stash clear", ["git", "stash", "clear"], null], ["git clean -f", ["git", "clean", "-f"], "(^|[^A-Za-z0-9-])(-\\w*f|--force)"], ["git reset HEAD~", ["git", "reset", "HEAD~"], null], ["git add -A", ["git", "add", "-A"], "(^|[^A-Za-z0-9-])(-\\w*A|--all)"], ["git add .", ["git", "add", "."], "(^|[^A-Za-z0-9./-])\\.(?![\\w./-])"], ["dd if=", ["dd", "if="], null], ["mkfs", ["mkfs"], null], ["fdisk", ["fdisk"], null], ["time-machine-safety", ["tmutil"], "addexclusion|associatedisk|delete|disable|inheritbackup|removedestination|setdestination|setquota|stopbackup|thinlocalsnapshots"], ["shell-runs-downloaded-script", ["bash"], "curl|wget|(\\b(bash|dash|fish|ksh|sh|su|zsh)\\b[\\s\\S]*?){4}"], ["shell-runs-downloaded-script", ["dash"], "curl|wget|(\\b(bash|dash|fish|ksh|sh|su|zsh)\\b[\\s\\S]*?){4}"], ["shell-runs-downloaded-script", ["fish"], "curl|wget|(\\b(bash|dash|fish|ksh|sh|su|zsh)\\b[\\s\\S]*?){4}"], ["shell-runs-downloaded-script", ["ksh"], "curl|wget|(\\b(bash|dash|fish|ksh|sh|su|zsh)\\b[\\s\\S]*?){4}"], ["shell-runs-downloaded-script", ["sh"], "curl|wget|(\\b(bash|dash|fish|ksh|sh|su|zsh)\\b[\\s\\S]*?){4}"], ["shell-runs-downloaded-script", ["su"], "curl|wget|(\\b(bash|dash|fish|ksh|sh|su|zsh)\\b[\\s\\S]*?){4}"], ["shell-runs-downloaded-script", ["zsh"], "curl|wget|(\\b(bash|dash|fish|ksh|sh|su|zsh)\\b[\\s\\S]*?){4}"], ["sed", ["sed"], "(^|[^A-Za-z0-9-])(-\\w*i|--in-place)"], ["awk", ["awk"], null], ["find", ["find"], "-delete|-fprint|-fls"], ["git filter-repo", ["git", "filter-repo"], null], ["git filter-branch", ["git", "filter-branch"], null], ["bfg", ["bfg"], null], ["git rebase -i", ["git", "rebase", "-i"], "(^|[^A-Za-z0-9-])-\\w*i"], ["git rebase --interactive", ["git", "rebase", "--interactive"], null], ["git push --force", ["git", "push", "--force"], null], ["git push -f", ["git", "push", "-f"], null], ["git push", ["git", "push"], null], ["gh pr create", ["gh", "pr", "create"], null], ["gh pr merge --squash", ["gh", "pr", "merge", "--squash"], null], ["gh pr merge", ["gh", "pr", "merge"], null], ["gh release create", ["gh", "release", "create"], null], ["gh repo create", ["gh", "repo", "create"], null], ["gh issue edit", ["gh", "issue", "edit"], null], ["gh pr edit", ["gh", "pr", "edit"], null], ["gh repo edit", ["gh", "repo", "edit"], null], ["gh pr comment", ["gh", "pr", "comment"], null], ["gh issue comment", ["gh", "issue", "comment"], null], ["gh issue create", ["gh", "issue", "create"], null], ["version-bump-command", ["npm", "version"], null], ["version-bump-command", ["yarn", "version"], null], ["version-bump-command", ["pnpm", "version"], null], ["version-bump-command", ["cargo", "set-version"], null], ["version-bump-command", ["poetry", "version"], null], ["version-bump-command", ["uv", "version"], null], ["version-bump-command", ["hatch", "version"], null], ["version-bump-command", ["bump2version"], null], ["version-bump-command", ["bumpversion"], null], ["version-bump-command", ["mvn", "versions:set"], null], ["publish-command", ["npm", "publish"], null], ["publish-command", ["yarn", "publish"], null], ["publish-command", ["pnpm", "publish"], null], ["publish-command", ["cargo", "publish"], null], ["publish-command", ["poetry", "publish"], null], ["publish-command", ["uv", "publish"], null], ["publish-command", ["hatch", "publish"], null], ["publish-command", ["flit", "publish"], null], ["publish-command", ["twine", "upload"], null], ["publish-command", ["gem", "push"], null], ["publish-command", ["mvn", "deploy"], null], ["publish-command", ["gradle", "publish"], null], ["publish-command", ["dotnet", "nuget", "push"], null], ["publish-command", ["docker", "push"], null]];
// Each guard's own text = client.guard_guidance(name, "pi"); OpenCode spells /ar as /ar-.
const CRITICAL_GUARD_GUIDANCE = {"rm": "Use the 'trash' CLI command instead for safe file deletion.\n\nExample:\n  Instead of: rm /path/to/file\n  Use: trash /path/to/file\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or uv tool install trash-cli (Linux)\n\nTo allow (default 1 use): /ar ok rm\nScope: [N|5m|permanent] (default 1 use)", "rm -rf": "Use the 'trash' CLI command instead - rm -rf is permanently destructive.\n\nExample:\n  Instead of: rm -rf /path/to/dir\n  Use: trash /path/to/dir\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or uv tool install trash-cli (Linux)\n\nTo allow (default 1 use): /ar ok 'rm -rf'\nScope: [N|5m|permanent] (default 1 use)", "git reset --hard": "DANGEROUS: 'git reset --hard' permanently discards all uncommitted changes.\n\n**SAFER ALTERNATIVES (in order of preference):**\n\n1. **Stash changes** (RECOMMENDED - preserves work, easily recoverable):\n   git stash push -m \"WIP: brief description of changes\"\n   # Later: git stash list, git stash pop, or git stash apply\n\n2. **Create backup branch** (if stash isn't suitable):\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint before reset\"\n   git checkout -  # return to original branch\n\n3. **Selective stash** (to save specific files only):\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git status && git diff\n\nTo allow (default 1 use): /ar ok 'git reset --hard'\nScope: [N|5m|permanent] (default 1 use)", "git checkout .": "DANGEROUS: 'git checkout .' discards ALL uncommitted changes in working directory.\n\n**SAFER ALTERNATIVES:**\n\n1. **Stash changes** (RECOMMENDED):\n   git stash push -m \"WIP: saving changes before checkout\"\n\n2. **Create backup branch**:\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint\"\n   git checkout -\n\n3. **Selective stash** (save specific files only):\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git diff\n\nTo allow (default 1 use): /ar ok 'git checkout .'\nScope: [N|5m|permanent] (default 1 use)", "git checkout --": "CAUTION: 'git checkout -- <file>' discards unstaged changes to specific file.\n\n**SAFER ALTERNATIVE:**\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git diff <file>\n\nTo allow (default 1 use): /ar ok 'git checkout --'\nScope: [N|5m|permanent] (default 1 use)", "git checkout": "CAUTION: 'git checkout <file>' discards unstaged changes to specific file.\n\n**SAFER ALTERNATIVES:**\n\n1. **Stash changes** (RECOMMENDED):\n   git stash push <file> -m 'WIP: <file>'\n\n2. **Switch branches** (if not targeting a file):\n   git switch <branch>  # switch branches (Git 2.23+)\n\n**View what would be lost:**\n   git diff <file>\n\nNote: 'git checkout <name>' cannot be told apart from a path without guessing, so it is blocked while the repo has uncommitted changes. Creating a branch ('git checkout -b <new-branch>') and 'git switch <branch>' are always allowed.\n\nTo allow (default 1 use): /ar ok 'git checkout'\nScope: [N|5m|permanent] (default 1 use)", "git restore": "CAUTION: 'git restore <file>' permanently discards unstaged changes with no recovery.\n\n**SAFER ALTERNATIVE (RECOMMENDED):**\n   git stash push <file> -m 'WIP: <file>'\n\nNote: 'git restore --staged <file>' (unstage only) is safe and allowed.\n\n**View what would be lost:**\n   git diff <file>\n\nTo allow (default 1 use): /ar ok 'git restore'\nScope: [N|5m|permanent] (default 1 use)", "git stash drop": "CAUTION: 'git stash drop' permanently deletes stashed changes.\n\n**SAFER ALTERNATIVES:**\n\n1. **Apply stash instead** (keeps changes):\n   git stash pop    # apply and remove from stash\n   git stash apply  # apply and keep in stash\n\n2. **View stash contents first**:\n   git stash show -p  # see what's in the stash\n\nTo allow (default 1 use): /ar ok 'git stash drop'\nScope: [N|5m|permanent] (default 1 use)", "git stash clear": "DANGEROUS: 'git stash clear' permanently deletes ALL stashed changes at once.\n\n**SAFER ALTERNATIVES:**\n\n1. **Apply the stashes you still need** (keeps changes):\n   git stash list   # see every stash\n   git stash apply stash@{N}\n\n2. **View stash contents first**:\n   git stash show -p stash@{N}\n\nTo allow (default 1 use): /ar ok 'git stash clear'\nScope: [N|5m|permanent] (default 1 use)", "git clean -f": "DANGEROUS: 'git clean -f' permanently deletes untracked files.\n\n**SAFER ALTERNATIVES:**\n\n1. **Preview first** (ALWAYS do this):\n   git clean -n   # dry-run, shows what would be deleted\n\n2. **Stash untracked files**:\n   git stash push -u -m \"WIP: stashing untracked files\"\n\n3. **Move to backup** (manual safety):\n   mkdir -p ../backup-untracked && git clean -n | xargs -I{} mv {} ../backup-untracked/\n\n4. **Interactive mode**:\n   git clean -i  # prompts for each file\n\nTo allow (default 1 use): /ar ok 'git clean -f'\nScope: [N|5m|permanent] (default 1 use)", "git reset HEAD~": "CAUTION: 'git reset HEAD~' undoes commits (mixed reset by default).\n\n**SAFER ALTERNATIVES:**\n\n1. **Soft reset** (keeps all changes staged):\n   git reset --soft HEAD~1\n\n2. **Create backup branch first**:\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-before-reset\n   git checkout -\n   git reset HEAD~1\n\n3. **Revert instead** (creates new commit, preserves history):\n   git revert HEAD\n\n**Recovery if you already reset:**\n   git reflog  # find the commit hash\n   git reset --hard <hash>  # restore to that point\n\nTo allow (default 1 use): /ar ok 'git reset HEAD~'\nScope: [N|5m|permanent] (default 1 use)", "git add -A": "CAUTION: 'git add -A' stages ALL changes including untracked files, which may accidentally include sensitive files (.env, credentials) or large binaries.\n\n**SAFER ALTERNATIVE:**\n   git add <file1> <file2> ...  # stage specific files by name\n\n**Preview what would be staged:**\n   git status  # review untracked and modified files first\n\nTo allow (default 1 use): /ar ok 'git add -A'\nScope: [N|5m|permanent] (default 1 use)", "git add .": "CAUTION: 'git add .' stages ALL changes in the current directory, which may accidentally include sensitive files (.env, credentials) or large binaries.\n\n**SAFER ALTERNATIVE:**\n   git add <file1> <file2> ...  # stage specific files by name\n\n**Preview what would be staged:**\n   git status  # review untracked and modified files first\n\nTo allow (default 1 use): /ar ok 'git add .'\nScope: [N|5m|permanent] (default 1 use)", "dd if=": "Avoid direct disk writes - use proper backup tools. Consider rsync, ddrescue, or backup utilities instead.\n\nTo allow (default 1 use): /ar ok 'dd if='\nScope: [N|5m|permanent] (default 1 use)", "mkfs": "Filesystem creation is dangerous - backup data first and use partition managers like GNOME Disks or gparted.\n\nTo allow (default 1 use): /ar ok mkfs\nScope: [N|5m|permanent] (default 1 use)", "fdisk": "Partition modification is dangerous - backup data first. Use GUI tools like GNOME Disks or gparted for safer operations.\n\nTo allow (default 1 use): /ar ok fdisk\nScope: [N|5m|permanent] (default 1 use)", "time-machine-safety": "Time Machine history and protection changes require explicit user permission. This command can permanently remove backup history or weaken backup protection; tool-host or automatic approval is not user consent.\n\nInspect first with read-only commands such as:\n  tmutil listlocalsnapshots /\n  tmutil listbackups\n  tmutil destinationinfo\n\nAsk the user to approve the exact operation. After approval, allow only that operation (default 1 use):\n  /ar ok 'tmutil thinlocalsnapshots'\n  Codex: ar:ok 'tmutil thinlocalsnapshots'\nScope: [N|5m|permanent] (default 1 use)", "shell-runs-downloaded-script": "Blocked: this shell would run a script autorun cannot read: one straight from a network download (curl or wget), or a shell nested more than 3 levels deep. autorun checks each command before it runs, but it cannot read the commands inside a script it never sees, so none of its guards can check what this script does.\n\n**Instead:** download the file, read it, then run it:\n  curl -fsSL -o /tmp/install.sh <url>\n  # read /tmp/install.sh first (read)\n  sh /tmp/install.sh\nFor nested shells, run the inner command directly.\n\nTo allow (default 1 use): /ar ok <shell>, the shell the download is fed to, e.g. /ar ok sh or /ar ok bash\nScope: [N|5m|permanent] (default 1 use)", "sed": "Use the edit tool instead of sed for file modifications.\n\n**Why:**\n- edit tool is safer (validates exact string matches)\n- Better error messages\n- Integrates with your AI coding assistant's file tracking\n\n**Example:**\nInstead of: sed -i 's/old/new/g' file.txt\nUse: edit tool with old_string='old' and new_string='new'\n\n**Commands:**\n- Allow (default 1 use): /ar ok sed\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar no --global sed", "awk": "Use Python or the read tool instead of awk for text processing.\n\n**Why:**\n- read tool loads file contents directly\n- Python provides more robust text processing\n- Better error handling and debugging\n\n**Example:**\nInstead of: awk '{print $1}' file.txt\nUse: read tool + Python string processing\n\n**Commands:**\n- Allow (default 1 use): /ar ok awk\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar no --global awk", "find": "Blocked: this find deletes files (-delete) or overwrites the file it names (-fprint, -fprint0, -fprintf, -fls), and neither can be undone.\n\n**Instead:** list what matches first with the same expression, then move it to the trash:\n  find . -name '*.tmp'                       # check the list\n  find . -name '*.tmp' -exec trash {} +      # recoverable delete\nTo save find's output, redirect to a new file: find . -name '*.tmp' > list.txt\n\nTo allow (default 1 use): /ar ok find\nScope: [N|5m|permanent] (default 1 use)", "git filter-repo": "BLOCKED: 'git filter-repo' permanently rewrites repository history. User permission required.\n\nAll commit hashes change \u2014 collaborators must re-clone after rewrite.\n\nBackup first: git clone --mirror . ../backup-$(date +%Y%m%d).git\n\nTo allow (default 1 use): /ar ok 'git filter-repo'\nScope: [N|5m|permanent] (default 1 use)", "git filter-branch": "BLOCKED: 'git filter-branch' is deprecated. Use git-filter-repo instead:\n  pip install git-filter-repo\n\ngit filter-branch is slow, error-prone, and creates backup refs.\n\nTo allow (default 1 use): /ar ok 'git filter-branch'\nScope: [N|5m|permanent] (default 1 use)", "bfg": "BLOCKED: BFG Repo-Cleaner permanently rewrites git history.\n\nConsider git-filter-repo instead (Python, no Java dependency):\n  pip install git-filter-repo\n\nAll collaborators must re-clone after any history rewrite.\n\nTo allow (default 1 use): /ar ok bfg\nScope: [N|5m|permanent] (default 1 use)", "git rebase -i": "BLOCKED: 'git rebase -i' rewrites commit history and requires an interactive terminal.\n\nAlternatives: git commit --fixup <hash>, git rebase main (non-interactive)\n\nTo allow (default 1 use): /ar ok 'git rebase -i'\nScope: [N|5m|permanent] (default 1 use)", "git rebase --interactive": "BLOCKED: 'git rebase --interactive' rewrites commit history. See 'git rebase -i' for alternatives.\n\nTo allow (default 1 use): /ar ok 'git rebase --interactive'\nScope: [N|5m|permanent] (default 1 use)", "git push --force": "BLOCKED: 'git push --force' overwrites remote history. Use --force-with-lease instead.\n\nTo allow (default 1 use): /ar ok 'git push --force'\nScope: [N|5m|permanent] (default 1 use)", "git push -f": "BLOCKED: 'git push -f' overwrites remote history. Use --force-with-lease instead.\n\nTo allow (default 1 use): /ar ok 'git push -f'\nScope: [N|5m|permanent] (default 1 use)", "git push": "Blocked: git push requires explicit user permission.\nContinue with local tasks. Ask the user when ready to push.\n\nTo allow (default 1 use): /ar ok 'git push'\nScope: [N|5m|permanent] (default 1 use)", "gh pr create": "Blocked: gh pr create requires explicit user permission.\nContinue with local tasks. Ask the user when ready to create PR.\n\nTo allow (default 1 use): /ar ok 'gh pr create'\nScope: [N|5m|permanent] (default 1 use)", "gh pr merge --squash": "BLOCKED: '--squash' destroys individual commit history by combining all commits into one.\n\nUse a regular merge to preserve commit history: gh pr merge\n\nTo allow (default 1 use): /ar ok 'gh pr merge --squash'\nScope: [N|5m|permanent] (default 1 use)", "gh pr merge": "BLOCKED: User permission required before merging pull requests.\n\nInform the user the PR is ready to merge and ask for permission.\n\nTo allow (default 1 use): /ar ok 'gh pr merge'\nScope: [N|5m|permanent] (default 1 use)", "gh release create": "Command blocked: gh release create\n\nThe user requires explicit permission before creating releases.\n\nInform the user the release is ready and ask for permission.\n\nTo allow (default 1 use): /ar ok 'gh release create'\nScope: [N|5m|permanent] (default 1 use)", "gh repo create": "Command blocked: gh repo create\n\nThe user requires explicit permission before creating remote repositories.\n\nAsk the user for permission before proceeding.\n\nTo allow (default 1 use): /ar ok 'gh repo create'\nScope: [N|5m|permanent] (default 1 use)", "gh issue edit": "BLOCKED: 'gh issue edit' modifies a public GitHub issue (title, body, labels, assignees).\n\nUser permission required before editing shared resources.\n\nTo allow (default 1 use): /ar ok 'gh issue edit'\nScope: [N|5m|permanent] (default 1 use)", "gh pr edit": "BLOCKED: 'gh pr edit' modifies a public pull request (title, body, labels, reviewers).\n\nUser permission required before editing shared resources.\n\nTo allow (default 1 use): /ar ok 'gh pr edit'\nScope: [N|5m|permanent] (default 1 use)", "gh repo edit": "BLOCKED: 'gh repo edit' modifies repository settings (description, visibility, homepage).\n\nUser permission required before editing shared resources.\n\nTo allow (default 1 use): /ar ok 'gh repo edit'\nScope: [N|5m|permanent] (default 1 use)", "gh pr comment": "BLOCKED: 'gh pr comment' posts a publicly visible comment on a pull request.\n\nUser permission required before posting public comments.\n\nTo allow (default 1 use): /ar ok 'gh pr comment'\nScope: [N|5m|permanent] (default 1 use)", "gh issue comment": "BLOCKED: 'gh issue comment' posts a publicly visible comment on a GitHub issue.\n\nUser permission required before posting public comments.\n\nTo allow (default 1 use): /ar ok 'gh issue comment'\nScope: [N|5m|permanent] (default 1 use)", "gh issue create": "BLOCKED: 'gh issue create' creates a new public GitHub issue.\n\nUser permission required before creating public issues.\n\nTo allow (default 1 use): /ar ok 'gh issue create'\nScope: [N|5m|permanent] (default 1 use)", "version-bump-command": "Blocked: bumping the package version requires explicit user consent. The user has not asked for a version bump.\n\nKeep going on your other tasks \u2014 do NOT stop or get stuck on this. When a release is actually needed, tell the user the change is ready and ask which part to bump (major/minor/patch).\n\nIf the user consents, grant permission with /ar ok, e.g.:\n  /ar ok 'cargo set-version'   \u2014 allow that exact command (quote commands with spaces)\n  /ar ok 'Cargo.toml'          \u2014 allow edits to that manifest\n  /ar ok regex:version|Cargo.toml|package.json|pyproject.toml perm   \u2014 allow ALL version bumps this session (leave the regex UNQUOTED)\nAdd a scope: N (uses) | 5m (time window) | permanent. Use /ar ok --global to apply the grant to every session.\nDisable the guard entirely: set AUTORUN_VERSION_BUMP_GUARD_ENABLED=false and restart the daemon (autorun --restart-daemon).", "publish-command": "Blocked: publishing a package/release to a registry requires explicit user consent. The user has not asked to publish.\n\nKeep going on your other tasks \u2014 do NOT stop or get stuck on this. Publishing is public and usually irreversible (a released version cannot be unpublished). When a release is actually wanted, tell the user it is ready and ask them to confirm.\n\nIf the user consents, grant permission with /ar ok, e.g.:\n  /ar ok 'cargo publish'   \u2014 allow that exact command (quote commands with spaces)\n  /ar ok regex:publish|upload|gem push|docker push|nuget push|mvn deploy perm   \u2014 allow ALL publishes this session (leave the regex UNQUOTED)\nAdd a scope: N (uses) | 5m (time window) | permanent. Use /ar ok --global to apply the grant to every session.\nDisable the guard entirely: set AUTORUN_PUBLISH_GUARD_ENABLED=false and restart the daemon (autorun --restart-daemon)."};
const SHELL_NAME_HIDING_FEATURES = ["$'", "$\"", "eval", "base64", "xxd", "printf", "<(", ">("];
const SHELL_TOOL_NAMES = ["bash", "exec_command", "execute_command", "local_shell", "run_command", "run_shell_command", "run_terminal_cmd", "run_terminal_command", "sh", "shell", "shell_command", "terminal_exec", "zsh"];
const BASH_TOOLS = ["Bash", "bash", "bash_command", "exec_command", "functions.exec_command", "run_command", "run_shell_command", "shell"];

function screenedGuard(command) {
  // Mirror of config.screened_guard: {guard} or {syntax} that needs the full
  // check, or null. Alphabetic tokens match whole words; a guard's screen, when
  // it has one, decides in place of its option tokens (git clean -df).
  const plain = command.replace(/\\\n|['"\\]/g, "");
  const words = new Set((plain + " " + command).match(/[A-Za-z]+/g) ?? []);
  const guard = CRITICAL_GUARDS.find(([, tokens, screen]) => tokens.every(
    token => /^[A-Za-z]+$/.test(token) ? words.has(token) : Boolean(screen) || plain.includes(token))
    && (!screen || new RegExp(screen).test(plain)));
  if (guard) return { guard: guard[0] };
  const syntax = SHELL_NAME_HIDING_FEATURES.find(feature => command.includes(feature));
  return syntax ? { syntax } : null;
}
const DEGRADED_FAILURE_CODES = new Set([
  "daemon_dispatch_timeout", "daemon_dispatch_contained", "daemon_dispatch_stalled",
  "state_lock_contended", "state_evaluation_failed", "daemon_failure",
  "daemon_response_timeout", "daemon_unavailable_or_timeout",
]);

function failureCode(response) {
  const code = response?._autorun_failure_code;
  return DEGRADED_FAILURE_CODES.has(code) ? code : null;
}

export function createDaemonBridge({
  cliType,
  socketPath,
  portFile,
  hookEntryCommand = [],
  timeoutMs = 5000,
  inprocessCapabilities = [],
}) {
  // { response, connected }: connected is true once the frame was handed to a
  // daemon, after which asking again (here or through the hook entry) would
  // send the same request twice.
  async function exchange(payload, deadline) {
    const target = daemonTarget(socketPath, portFile);
    if (target === null) return { response: null, connected: false };
    const frame =
      JSON.stringify({
        ...payload,
        cli_type: cliType,
        protocol_version: PROTOCOL_VERSION,
        ...(inprocessCapabilities.length > 0
          ? { inprocess_capabilities: [...inprocessCapabilities] }
          : {}),
        _pid: process.pid,
        [HOOK_DEADLINE_PAYLOAD_KEY]: (Date.now() + Math.max(0, deadline - performance.now())) / 1000,
      }) + "\n";

    return new Promise((resolve) => {
      let settled = false;
      let connected = false;
      let text = "";
      const finish = (response) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        socket.destroy();
        resolve({ response, connected });
      };
      const socket = createConnection(target);
      const timer = setTimeout(() => finish(null), Math.max(deadline - performance.now(), 0));
      socket.setEncoding("utf8");
      socket.on("connect", () => {
        connected = true;
        socket.write((target.preamble ?? "") + frame);
      });
      socket.on("data", (chunk) => {
        text += chunk;
        const newline = text.indexOf("\n");
        if (newline < 0) return;
        try {
          finish(JSON.parse(text.slice(0, newline)));
        } catch {
          finish(null);
        }
      });
      socket.on("error", () => finish(null));
      socket.on("close", () => finish(null));
    });
  }

  async function askDaemon(payload) {
    if (autorunDisabled()) return null;
    const { response } = await exchange(payload, performance.now() + timeoutMs);
    if (!response) return response;
    const { _autorun_failure_code, ...verdict } = response;
    return verdict;
  }

  function lastResort(payload, failureCode) {
    // Python owns full guards; the literal screen also lets ordinary shells proceed.
    // No config-file reader lives in this bridge: this last resort follows
    // AUTORUN_OVERLOAD_POLICY only; the Python route also reads the config file.
    const policy = process.env.AUTORUN_OVERLOAD_POLICY ?? "critical-guards";
    const name = payload.tool_name ?? payload.toolName ?? payload.toolCall?.name ?? "";
    const shell = BASH_TOOLS.includes(name) || SHELL_TOOL_NAMES.includes(name.split(/__|[./:]/).at(-1).toLowerCase());
    const matched = !shell ? null : [payload.tool_input, payload.toolInput, payload.toolCall?.args].map(inputs => {
      for (const key of ["command", "cmd", "CommandLine"]) {
        const value = inputs?.[key];
        const command = typeof value === "string" ? value :
          Array.isArray(value) ? value.flat(Infinity).map(String).join(" ") : "";
        if (command) return screenedGuard(command);
      }
      return null;
    }).find(Boolean) ?? null;
    const needsCheck = matched !== null;
    const deny = policy === "block" || (policy !== "allow" && needsCheck);
    return { hookSpecificOutput: {
      permissionDecision: deny ? "deny" : "allow",
      permissionDecisionReason: `[autorun] [AR_EVENT_V1:${failureCode}] [AR_EVENT_V1:degraded_decision] ` +
        (policy === "allow"
          ? "Autorun could not answer and overload_policy=allow permits this call without built-in destructive-command guards, /ar:no rules, /ar:ok grants, the file policy or task and plan gating until autorun answers again; Do not restart the daemon."
          : policy === "block"
          ? "Autorun could not answer and overload_policy=block denies this tool call; wait and retry when it recovers; do not restart the daemon."
          : needsCheck
          ? "Autorun is degraded and its guard check did not answer in time. This command is blocked as a precaution because it " +
            (matched.guard
              ? `names \`${matched.guard}\`, which a built-in guard covers; the full check might have allowed it, so wait and retry when autorun recovers; do not restart the daemon.\n\nThe \`${matched.guard}\` guard says:\n` +
                (CRITICAL_GUARD_GUIDANCE[matched.guard] ?? "").replaceAll("/ar ", cliType === "opencode" ? "/ar-" : "/ar ")
              : `uses \`${matched.syntax}\`, which can run a guarded command such as rm under another spelling. Write the command without \`${matched.syntax}\`, or wait and retry when autorun recovers; do not restart the daemon.`) +
            (failureCode === "daemon_response_timeout" ? " " + RESPONSE_TIMEOUT_GUIDANCE : "")
          : "Autorun could not answer, so the quick screen found no possible built-in destructive-command guard while /ar:no rules, /ar:ok grants, the file policy and task and plan gating are not checked until autorun answers again; Do not restart the daemon.").replaceAll("/ar:", cliType === "opencode" ? "/ar-" : "/ar "),
    }};
  }

  async function askHookEntry(payload, deadline, failureCode = "daemon_unavailable_or_timeout") {
    const failed = () => lastResort(payload, failureCode);
    if (!Array.isArray(hookEntryCommand) || hookEntryCommand.length === 0) {
      return failed();
    }
    return new Promise((resolve) => {
      let stdout = "";
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        child.kill();
        resolve(value);
      };
      let child;
      try {
        child = spawn(hookEntryCommand[0], hookEntryCommand.slice(1), {
          stdio: ["pipe", "pipe", "ignore"],
        });
      } catch {
        resolve(failed());
        return;
      }
      const timer = setTimeout(
        // A timeout is usually a cold start, as in hook_entry's own timeout.
        () => finish(failed()),
        Math.max(deadline - performance.now(), 0),
      );
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
      child.on("error", () =>
        finish(failed()),
      );
      child.on("close", () => {
        // Local-only mode always emits a verdict, including an allow notice.
        // Silence cannot prove the guards ran; use the bounded last resort.
        if (stdout.trim() === "") {
          finish(failed());
          return;
        }
        try {
          const parsed = JSON.parse(stdout);
          const decision = parsed?.hookSpecificOutput?.permissionDecision ?? parsed?.decision;
          const { _autorun_failure_code, ...verdict } = parsed ?? {};
          finish(["allow", "approve", "deny", "block"].includes(decision) ? verdict : failed());
        } catch {
          // Output that is present but unparseable is a real malfunction: a
          // permission gate that cannot read its own verdict must not fail open.
          finish(failed());
        }
      });
      // A hook entry may exit before reading stdin (an allow needs no input).
      // Writing to its closed pipe then raises EPIPE on child.stdin, and an
      // unhandled stream error takes down the whole extension process: Bun on
      // Linux did so right after this fallback had answered correctly. The
      // verdict comes from the exit status and stdout in "close" above, so a
      // failed write carries no information of its own.
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify({ ...payload, cli_type: cliType,
        _autorun_degraded_check: true,
        _autorun_failure_code: failureCode,
        [HOOK_DEADLINE_PAYLOAD_KEY]: (Date.now() + Math.max(0, deadline - performance.now())) / 1000,
      }) + "\n");
    });
  }

  async function askToolGate(payload) {
    if (autorunDisabled()) return null;
    // One deadline for both routes, so a tool call waits at most timeoutMs.
    const deadline = performance.now() + timeoutMs;
    // Reserve interpreter startup and a bounded check inside the same budget.
    const { response, connected } = await exchange(payload, deadline - Math.min(500, timeoutMs / 2));
    const code = failureCode(response);
    if (response && !code) {
      const { _autorun_failure_code, ...verdict } = response;
      return verdict;
    }
    return askHookEntry(payload, deadline, code ?? (connected
      ? "daemon_response_timeout" : "daemon_unavailable_or_timeout"));
  }

  // promptAuthor is who wrote the command: "user", "model" or "automation"
  // (= autorun.core.PROMPT_AUTHORS). Commands that change permissions run only
  // for "user", so a caller that cannot say is taken as "automation".
  async function runCommandResponse(command, cwd, sessionId, metadata = {}, promptAuthor = "automation") {
    // Strip any spelling of the ar prefix, including a bare "/ar" (Pi's
    // registered command with no arguments), so every form reaches the
    // dispatcher as "ar:<name>" and an empty name gets help, not silence.
    const name = String(command ?? "").trim().replace(/^\/?ar(?:[:\- ]\s*|$)/i, "");
    const response = await askDaemon({
      ...metadata,
      hook_event_name: "UserPromptSubmit",
      session_id: sessionId,
      prompt: "ar:" + name,
      prompt_author: promptAuthor,
      cwd: String(cwd ?? ""),
    });
    if (response) return response;
    if (autorunDisabled()) {
      return { systemMessage: "autorun is stood down by AUTORUN_DISABLE; unset it to use /ar commands." };
    }
    return {
      systemMessage:
        "autorun daemon unreachable; command dispatch is fail-open. " +
        "Run `autorun --restart-daemon`, then retry.",
    };
  }

  async function runCommand(command, cwd, sessionId, metadata = {}, promptAuthor = "automation") {
    return responseMessage(await runCommandResponse(command, cwd, sessionId, metadata, promptAuthor));
  }

  return { askDaemon, askToolGate, runCommand, runCommandResponse };
}
