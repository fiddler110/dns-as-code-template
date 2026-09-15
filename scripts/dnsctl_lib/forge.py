"""Forge abstraction: PR lifecycle operations (create/list/diff/comments/view/
approve/merge) against whichever code-hosting platform this repo's `origin`
remote points at - GitHub, Azure DevOps, or a Forgejo/Gitea instance.

Every Forge implementation normalizes its platform's data into the exact
shape `gh --json ...` already returns (statusCheckRollup entries with
name/conclusion, reviews with author.login/state/submittedAt, etc.), so
dnsctl_lib.cli_utils.split_pr_checks/find_non_author_approval and every call
site in dnsctl.py work unchanged regardless of which Forge is active - only
dnsctl.py's command handlers know a Forge exists at all.

CI-run watching (`dnsctl validate`/`merge --wait` waiting for a workflow run
to finish) stays GitHub-only for now (gh's `run list`/`run watch`) - Azure
Pipelines and Forgejo Actions run-watching isn't implemented until there's a
real org/instance to test against. Every other PR-lifecycle operation
(submit/status/review/approve/merge/begin/rollback) works across all three.
"""

from __future__ import annotations

import abc
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import procutil
from .cli_utils import eprint

# Every call below goes through the `procutil` module object (procutil.git(),
# procutil.gh(), etc.) rather than importing those names directly - tests
# monkeypatch attributes on the shared dnsctl_lib.procutil module (the same
# object dnsctl.py sees as `mod.procutil`), and that only takes effect on
# calls resolved through the module at call time, not on a name bound once
# at import time.

# ---------------------------------------------------------------------------
# Shared result type
# ---------------------------------------------------------------------------


@dataclass
class ForgeResult:
    """Uniform return value for every Forge method - mirrors just enough of
    subprocess.CompletedProcess (returncode) plus already-parsed `data` (a
    str/dict/list, gh-JSON-shaped where applicable) so call sites don't
    care which Forge produced it. `error` is a ready-to-print message.
    `self_approval_blocked` flags the one error condition dnsctl.py's
    `approve` command handles specially (all three platforms refuse to let
    the PR author approve their own PR, with different wording)."""

    returncode: int
    data: object = None
    error: str = ""
    self_approval_blocked: bool = False


def _ok(data: object = None) -> ForgeResult:
    return ForgeResult(0, data=data)


def _err(message: str, returncode: int = 1) -> ForgeResult:
    return ForgeResult(returncode, error=message)


# ---------------------------------------------------------------------------
# Forge interface
# ---------------------------------------------------------------------------


class Forge(abc.ABC):
    """A code-hosting platform's PR lifecycle, in the shape dnsctl.py needs."""

    name: str
    display_name: str

    @abc.abstractmethod
    def available(self) -> bool: ...

    @abc.abstractmethod
    def unavailable_message(self) -> str: ...

    def require(self) -> bool:
        if self.available():
            return True
        eprint(self.unavailable_message())
        return False

    @abc.abstractmethod
    def pr_create(self, title: str, body: str, base: str, head: str) -> ForgeResult:
        """On success, `data` is the new PR's URL."""

    @abc.abstractmethod
    def pr_list_open(self) -> ForgeResult:
        """On success, `data` is a list of dicts shaped like gh's
        `pr list --json number,title,headRefName,url,isDraft,statusCheckRollup`."""

    @abc.abstractmethod
    def pr_diff(self, number: str) -> ForgeResult:
        """On success, `data` is the unified diff text."""

    @abc.abstractmethod
    def pr_comments(self, number: str) -> ForgeResult:
        """On success, `data` is a list of {"body": ...} dicts."""

    @abc.abstractmethod
    def pr_view(self, number: str) -> ForgeResult:
        """On success, `data` is a dict shaped like gh's
        `pr view --json state,statusCheckRollup,title,author,reviews,mergeCommit`
        (state one of "OPEN"/"MERGED"/"CLOSED")."""

    @abc.abstractmethod
    def pr_approve(self, number: str, body: str) -> ForgeResult:
        """On success, `data` is a short confirmation message."""

    @abc.abstractmethod
    def pr_merge(self, number: str) -> ForgeResult:
        """A true merge commit (not squash/rebase) with the source branch
        deleted afterward - matches gh's `pr merge --merge --delete-branch`,
        today's only merge behavior in dnsctl.py."""


# ---------------------------------------------------------------------------
# GitHub (wraps the existing gh CLI - unchanged behavior from before)
# ---------------------------------------------------------------------------


class GithubForge(Forge):
    name = "github"
    display_name = "GitHub"

    def available(self) -> bool:
        return procutil.have_gh()

    def unavailable_message(self) -> str:
        return "error: GitHub CLI ('gh') not found on PATH. Install it: https://cli.github.com/"

    def pr_create(self, title, body, base, head) -> ForgeResult:
        result = procutil.gh([
            "pr", "create", "--title", title, "--body", body or "",
            "--base", base, "--head", head,
        ])
        if result.returncode != 0:
            return _err(result.stderr.strip() or result.stdout.strip(), result.returncode)
        return _ok(result.stdout.strip())

    def pr_list_open(self) -> ForgeResult:
        result = procutil.gh([
            "pr", "list", "--state", "open",
            "--json", "number,title,headRefName,url,isDraft,statusCheckRollup",
        ])
        if result.returncode != 0:
            return _err(result.stderr.strip(), result.returncode)
        return _ok(json.loads(result.stdout))

    def pr_diff(self, number) -> ForgeResult:
        result = procutil.gh(["pr", "diff", number])
        if result.returncode != 0:
            return _err(result.stderr.strip(), result.returncode)
        return _ok(result.stdout)

    def pr_comments(self, number) -> ForgeResult:
        result = procutil.gh(["pr", "view", number, "--json", "comments"])
        if result.returncode != 0:
            return _err(result.stderr.strip(), result.returncode)
        return _ok(json.loads(result.stdout).get("comments", []))

    def pr_view(self, number) -> ForgeResult:
        result = procutil.gh([
            "pr", "view", number,
            "--json", "state,statusCheckRollup,title,author,reviews,mergeCommit",
        ])
        if result.returncode != 0:
            return _err(result.stderr.strip() or result.stdout.strip(), result.returncode)
        return _ok(json.loads(result.stdout))

    def pr_approve(self, number, body) -> ForgeResult:
        result = procutil.gh(["pr", "review", number, "--approve", "--body", body or ""])
        if result.returncode != 0:
            stderr = result.stderr.strip()
            blocked = "own pull request" in stderr.lower()
            return ForgeResult(result.returncode, error=stderr or result.stdout.strip(), self_approval_blocked=blocked)
        return _ok(result.stdout.strip())

    def pr_merge(self, number) -> ForgeResult:
        result = procutil.gh(["pr", "merge", number, "--merge", "--delete-branch"], capture=False)
        return ForgeResult(result.returncode)


# ---------------------------------------------------------------------------
# Remote-URL parsing shared by the non-GitHub forges
# ---------------------------------------------------------------------------


def _strip_git_suffix(text: str) -> str:
    return text[:-4] if text.endswith(".git") else text


def get_origin_url() -> str:
    return procutil.git_output(["remote", "get-url", "origin"])


@dataclass
class AzureDevOpsRepoRef:
    org_url: str
    project: str
    repository: str


def parse_azure_devops_remote(url: str) -> AzureDevOpsRepoRef | None:
    """Handles the SSH, modern HTTPS, and legacy *.visualstudio.com forms
    `git remote get-url origin` can return for an Azure Repos remote."""
    m = re.match(
        r"^[\w.-]+@(?:ssh\.dev\.azure\.com|vs-ssh\.visualstudio\.com):v3/([^/]+)/([^/]+)/(.+)$",
        url,
    )
    if m:
        org, project, repo = m.groups()
        return AzureDevOpsRepoRef(f"https://dev.azure.com/{org}", project, _strip_git_suffix(repo))
    m = re.match(r"^https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/(.+)$", url)
    if m:
        org, project, repo = m.groups()
        return AzureDevOpsRepoRef(f"https://dev.azure.com/{org}", project, _strip_git_suffix(repo))
    m = re.match(r"^https://([^./]+)\.visualstudio\.com/([^/]+)/_git/(.+)$", url)
    if m:
        org, project, repo = m.groups()
        return AzureDevOpsRepoRef(f"https://dev.azure.com/{org}", project, _strip_git_suffix(repo))
    return None


@dataclass
class ForgejoRepoRef:
    base_url: str
    owner: str
    repo: str


def parse_forgejo_remote(url: str) -> ForgejoRepoRef | None:
    """Generic `host:owner/repo` (SSH) or `https://host/owner/repo` (HTTPS)
    parsing for a self-hosted Forgejo/Gitea remote."""
    m = re.match(r"^(?:ssh://)?[\w.-]+@([^:/]+)[:/](.+)$", url)
    if m:
        host, path = m.groups()
        parts = _strip_git_suffix(path).strip("/").split("/")
        if len(parts) >= 2:
            return ForgejoRepoRef(f"https://{host}", parts[-2], parts[-1])
    m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+)$", url)
    if m:
        host, path = m.groups()
        parts = _strip_git_suffix(path).strip("/").split("/")
        if len(parts) >= 2:
            return ForgejoRepoRef(f"https://{host}", parts[-2], parts[-1])
    return None


def _local_pr_diff(base_ref: str, head_ref: str) -> ForgeResult:
    """Diff computed from the local git repo rather than a platform API -
    used by forges whose CLI/REST surface doesn't expose a raw unified diff
    for a PR directly. Requires the two branches to exist on `origin`."""
    fetch = procutil.git(["fetch", "origin", base_ref, head_ref])
    if fetch.returncode != 0:
        return _err(
            f"error: could not fetch '{base_ref}'/'{head_ref}' from origin to compute the diff.",
            fetch.returncode,
        )
    diff = procutil.git(["diff", f"origin/{base_ref}...origin/{head_ref}"], capture=True)
    if diff.returncode != 0:
        return _err(diff.stderr.strip(), diff.returncode)
    return _ok(diff.stdout)


# ---------------------------------------------------------------------------
# Azure DevOps (az CLI + azure-devops extension)
# ---------------------------------------------------------------------------

# GitStatusState / policy evaluation status -> the conclusion vocabulary
# split_pr_checks (cli_utils.py) already knows how to read.
_AZDEVOPS_POLICY_STATUS_MAP = {
    "approved": "SUCCESS",
    "rejected": "FAILURE",
    "broken": "FAILURE",
    "queued": "PENDING",
    "running": "PENDING",
    "pending": "PENDING",
    "notapplicable": "SKIPPED",
}

# az repos pr status -> gh-style PR state
_AZDEVOPS_PR_STATE_MAP = {"active": "OPEN", "completed": "MERGED", "abandoned": "CLOSED"}


class AzureDevOpsForge(Forge):
    """Wraps `az repos pr ...` (the azure-devops CLI extension, installed
    automatically on first use) against a git remote pointing at Azure Repos.

    Check status is read from PR branch policies (`az repos pr policy list`)
    rather than commit statuses - an Azure Pipelines build-validation policy
    must be configured with display name "DNS Preview" for the merge gate
    to recognize it, the Azure DevOps equivalent of naming the GitHub
    Actions workflow "DNS Preview". PR review-thread comments aren't
    surfaced (`review` will report none found) - there's no documented
    `az repos pr` subcommand for listing them yet."""

    name = "azuredevops"
    display_name = "Azure DevOps"

    def __init__(self, local_env: dict | None = None):
        self._local_env = local_env or {}
        self._ref = None  # lazily resolved AzureDevOpsRepoRef | False

    def _repo_ref(self) -> AzureDevOpsRepoRef | None:
        if self._ref is None:
            self._ref = parse_azure_devops_remote(get_origin_url()) or False
        return self._ref or None

    def available(self) -> bool:
        return shutil.which("az") is not None

    def unavailable_message(self) -> str:
        return (
            "error: Azure CLI ('az') not found on PATH - needed for Azure DevOps PR "
            "operations (the 'azure-devops' extension installs automatically on first "
            "use). Install it: https://learn.microsoft.com/cli/azure/install-azure-cli"
        )

    def _invoke(self, args: list[str], capture: bool = True) -> ForgeResult:
        ref = self._repo_ref()
        if not ref:
            return _err(
                "error: could not parse an Azure DevOps org/project/repository from "
                f"origin's URL ({get_origin_url() or '(no origin remote)'})."
            )
        full = ["az", *args, "--organization", ref.org_url, "--output", "json"]
        try:
            proc = subprocess.run(
                full, capture_output=capture, text=capture, check=False, timeout=procutil.GH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return _err(f"error: '{' '.join(full)}' timed out after {procutil.GH_TIMEOUT}s.", 124)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip() if capture else ""
            return ForgeResult(proc.returncode, error=stderr or "az command failed - see output above.")
        return _ok(json.loads(proc.stdout) if capture and proc.stdout else None)

    def _checks_for(self, pr_id: str) -> list[dict]:
        result = self._invoke(["repos", "pr", "policy", "list", "--id", pr_id])
        if result.returncode != 0 or not result.data:
            return []
        checks = []
        for evaluation in result.data:
            config = evaluation.get("configuration") or {}
            settings = config.get("settings") or {}
            type_name = (config.get("type") or {}).get("displayName") or "policy"
            display_name = settings.get("displayName") or type_name
            status = (evaluation.get("status") or "").lower()
            checks.append({
                "name": display_name,
                "conclusion": _AZDEVOPS_POLICY_STATUS_MAP.get(status),
            })
        return checks

    def _reviews_for(self, pr: dict) -> list[dict]:
        reviews = []
        for reviewer in pr.get("reviewers") or []:
            vote = reviewer.get("vote") or 0
            if vote == 0:
                continue  # hasn't voted - no opinion to report
            state = "APPROVED" if vote > 0 else (
                "CHANGES_REQUESTED" if vote == -10 else "COMMENTED"
            )
            login = reviewer.get("uniqueName") or reviewer.get("displayName") or ""
            reviews.append({"author": {"login": login}, "state": state, "submittedAt": ""})
        return reviews

    def pr_create(self, title, body, base, head) -> ForgeResult:
        ref = self._repo_ref()
        if not ref:
            return _err(
                "error: could not parse an Azure DevOps org/project/repository from "
                f"origin's URL ({get_origin_url() or '(no origin remote)'})."
            )
        result = self._invoke([
            "repos", "pr", "create",
            "--project", ref.project, "--repository", ref.repository,
            "--source-branch", head, "--target-branch", base,
            "--title", title, "--description", body or "",
        ])
        if result.returncode != 0:
            return result
        pr_id = result.data.get("pullRequestId")
        url = f"{ref.org_url}/{ref.project}/_git/{ref.repository}/pullrequest/{pr_id}"
        return _ok(url)

    def pr_list_open(self) -> ForgeResult:
        ref = self._repo_ref()
        if not ref:
            return _err(
                "error: could not parse an Azure DevOps org/project/repository from "
                f"origin's URL ({get_origin_url() or '(no origin remote)'})."
            )
        result = self._invoke([
            "repos", "pr", "list",
            "--project", ref.project, "--repository", ref.repository, "--status", "active",
        ])
        if result.returncode != 0:
            return result
        prs = []
        for pr in result.data or []:
            pr_id = str(pr.get("pullRequestId"))
            head_ref = (pr.get("sourceRefName") or "").removeprefix("refs/heads/")
            url = f"{ref.org_url}/{ref.project}/_git/{ref.repository}/pullrequest/{pr_id}"
            prs.append({
                "number": pr.get("pullRequestId"),
                "title": pr.get("title"),
                "headRefName": head_ref,
                "url": url,
                "isDraft": bool(pr.get("isDraft")),
                "statusCheckRollup": self._checks_for(pr_id),
            })
        return _ok(prs)

    def pr_diff(self, number) -> ForgeResult:
        view = self.pr_view(number)
        if view.returncode != 0:
            return view
        return _local_pr_diff(view.data["_baseRefName"], view.data["_headRefName"])

    def pr_comments(self, number) -> ForgeResult:
        return _ok([])

    def pr_view(self, number) -> ForgeResult:
        result = self._invoke(["repos", "pr", "show", "--id", str(number)])
        if result.returncode != 0:
            return result
        pr = result.data
        merge_commit = None
        last_merge = pr.get("lastMergeCommit") or {}
        if last_merge.get("commitId"):
            merge_commit = {"oid": last_merge["commitId"]}
        author = (pr.get("createdBy") or {})
        return _ok({
            "state": _AZDEVOPS_PR_STATE_MAP.get(pr.get("status"), "OPEN"),
            "statusCheckRollup": self._checks_for(str(number)),
            "title": pr.get("title"),
            "author": {"login": author.get("uniqueName") or author.get("displayName") or ""},
            "reviews": self._reviews_for(pr),
            "mergeCommit": merge_commit,
            "_baseRefName": (pr.get("targetRefName") or "").removeprefix("refs/heads/"),
            "_headRefName": (pr.get("sourceRefName") or "").removeprefix("refs/heads/"),
        })

    def pr_approve(self, number, body) -> ForgeResult:
        # Azure DevOps has no branch-policy setting that blocks the PR
        # author's own vote (unlike GitHub/Forgejo), so self_approval_blocked
        # is never set here - `set-vote` on your own PR simply succeeds.
        result = self._invoke(["repos", "pr", "set-vote", "--id", str(number), "--vote", "approve"])
        if result.returncode != 0:
            return result
        return _ok(f"Voted 'approve' on PR {number}.")

    def pr_merge(self, number) -> ForgeResult:
        result = self._invoke([
            "repos", "pr", "update", "--id", str(number),
            "--status", "completed", "--squash", "false", "--delete-source-branch", "true",
        ])
        return ForgeResult(result.returncode, error=result.error)


# ---------------------------------------------------------------------------
# Forgejo / Gitea (no official CLI worth depending on - talk to the REST
# API directly with urllib, stdlib-only, same category of "just another
# external tool" as gh/az but reached over HTTP instead of a subprocess)
# ---------------------------------------------------------------------------

_FORGEJO_STATUS_MAP = {
    "success": "SUCCESS",
    "failure": "FAILURE",
    "error": "FAILURE",
    "pending": "PENDING",
    "warning": "NEUTRAL",
}
_FORGEJO_REVIEW_STATE_MAP = {
    "APPROVED": "APPROVED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
    "COMMENT": "COMMENTED",
}


class ForgejoForge(Forge):
    """Talks to a self-hosted Forgejo (or Gitea - same API) instance's REST
    API directly. Needs a personal access token with repo read/write scope
    in FORGEJO_TOKEN (.env or the environment) - this is the user's own
    Forgejo credential, unrelated to the write-scoped Cloudflare token that
    stays a GitHub Actions secret (see CLAUDE.md), so keeping it in .env is fine."""

    name = "forgejo"
    display_name = "Forgejo"

    def __init__(self, local_env: dict | None = None):
        self._local_env = local_env or {}
        self._ref = None  # lazily resolved ForgejoRepoRef | False

    def _repo_ref(self) -> ForgejoRepoRef | None:
        if self._ref is None:
            self._ref = parse_forgejo_remote(get_origin_url()) or False
        return self._ref or None

    def _token(self) -> str | None:
        return self._local_env.get("FORGEJO_TOKEN") or os.environ.get("FORGEJO_TOKEN")

    def available(self) -> bool:
        return self._repo_ref() is not None and bool(self._token())

    def unavailable_message(self) -> str:
        if not self._token():
            return (
                "error: FORGEJO_TOKEN not set (.env or the environment) - needed to talk "
                "to this repo's Forgejo instance. Create a personal access token with "
                "repo read/write scope and set FORGEJO_TOKEN."
            )
        return (
            "error: could not parse a Forgejo host/owner/repo from origin's URL "
            f"({get_origin_url() or '(no origin remote)'})."
        )

    def _api(
        self, method: str, path: str, body: object = None, raw: bool = False
    ) -> tuple[int, object, str]:
        """Returns (status_code, parsed_body_or_raw_text, error_text). A
        network/connection failure surfaces as status_code 0."""
        ref = self._repo_ref()
        url = f"{ref.base_url}/api/v1{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"token {self._token()}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=procutil.GH_TIMEOUT) as resp:
                raw_body = resp.read().decode("utf-8")
                return resp.status, (raw_body if raw else (json.loads(raw_body) if raw_body else None)), ""
        except urllib.error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", errors="replace")
            message = raw_body
            try:
                message = json.loads(raw_body).get("message", raw_body)
            except (json.JSONDecodeError, AttributeError):
                pass
            return exc.code, None, message
        except urllib.error.URLError as exc:
            return 0, None, str(exc.reason)

    def _checks_for(self, sha: str) -> list[dict]:
        ref = self._repo_ref()
        status, data, _err_text = self._api(
            "GET", f"/repos/{ref.owner}/{ref.repo}/commits/{sha}/statuses"
        )
        if status != 200 or not data:
            return []
        return [
            {
                "name": (s.get("context") or "status"),
                "conclusion": _FORGEJO_STATUS_MAP.get((s.get("status") or "").lower()),
            }
            for s in data
        ]

    def _reviews_for(self, number) -> list[dict]:
        ref = self._repo_ref()
        status, data, _err_text = self._api(
            "GET", f"/repos/{ref.owner}/{ref.repo}/pulls/{number}/reviews"
        )
        if status != 200 or not data:
            return []
        reviews = []
        for r in data:
            state = _FORGEJO_REVIEW_STATE_MAP.get(r.get("state"))
            if not state:
                continue
            login = (r.get("user") or {}).get("login", "")
            reviews.append({"author": {"login": login}, "state": state, "submittedAt": r.get("submitted_at") or ""})
        return reviews

    def pr_create(self, title, body, base, head) -> ForgeResult:
        ref = self._repo_ref()
        status, data, err_text = self._api(
            "POST", f"/repos/{ref.owner}/{ref.repo}/pulls",
            body={"title": title, "body": body or "", "base": base, "head": head},
        )
        if status not in (200, 201):
            return _err(err_text or f"Forgejo API returned HTTP {status}.")
        return _ok(data.get("html_url"))

    def pr_list_open(self) -> ForgeResult:
        ref = self._repo_ref()
        status, data, err_text = self._api(
            "GET", f"/repos/{ref.owner}/{ref.repo}/pulls?state=open&type=all"
        )
        if status != 200:
            return _err(err_text or f"Forgejo API returned HTTP {status}.")
        prs = []
        for pr in data or []:
            head = pr.get("head") or {}
            prs.append({
                "number": pr.get("number"),
                "title": pr.get("title"),
                "headRefName": head.get("ref"),
                "url": pr.get("html_url"),
                "isDraft": bool(pr.get("draft")),
                "statusCheckRollup": self._checks_for(head.get("sha")) if head.get("sha") else [],
            })
        return _ok(prs)

    def pr_diff(self, number) -> ForgeResult:
        ref = self._repo_ref()
        status, text, err_text = self._api(
            "GET", f"/repos/{ref.owner}/{ref.repo}/pulls/{number}.diff", raw=True
        )
        if status != 200:
            return _err(err_text or f"Forgejo API returned HTTP {status}.")
        return _ok(text)

    def pr_comments(self, number) -> ForgeResult:
        ref = self._repo_ref()
        status, data, err_text = self._api(
            "GET", f"/repos/{ref.owner}/{ref.repo}/issues/{number}/comments"
        )
        if status != 200:
            return _err(err_text or f"Forgejo API returned HTTP {status}.")
        return _ok([{"body": c.get("body", "")} for c in (data or [])])

    def pr_view(self, number) -> ForgeResult:
        ref = self._repo_ref()
        status, pr, err_text = self._api("GET", f"/repos/{ref.owner}/{ref.repo}/pulls/{number}")
        if status != 200:
            return _err(err_text or f"Forgejo API returned HTTP {status}.")
        if pr.get("merged"):
            state = "MERGED"
        elif pr.get("state") == "open":
            state = "OPEN"
        else:
            state = "CLOSED"
        head = pr.get("head") or {}
        merge_commit = {"oid": pr["merge_commit_sha"]} if pr.get("merge_commit_sha") else None
        return _ok({
            "state": state,
            "statusCheckRollup": self._checks_for(head.get("sha")) if head.get("sha") else [],
            "title": pr.get("title"),
            "author": {"login": (pr.get("user") or {}).get("login", "")},
            "reviews": self._reviews_for(number),
            "mergeCommit": merge_commit,
        })

    def pr_approve(self, number, body) -> ForgeResult:
        ref = self._repo_ref()
        status, data, err_text = self._api(
            "POST", f"/repos/{ref.owner}/{ref.repo}/pulls/{number}/reviews",
            body={"event": "APPROVED", "body": body or ""},
        )
        if status not in (200, 201):
            blocked = "own pull request" in (err_text or "").lower()
            return ForgeResult(1, error=err_text or f"Forgejo API returned HTTP {status}.", self_approval_blocked=blocked)
        return _ok(f"Approved PR {number}.")

    def pr_merge(self, number) -> ForgeResult:
        ref = self._repo_ref()
        status, _data, err_text = self._api(
            "POST", f"/repos/{ref.owner}/{ref.repo}/pulls/{number}/merge",
            body={"Do": "merge", "delete_branch_after_merge": True},
        )
        if status not in (200, 201):
            return _err(err_text or f"Forgejo API returned HTTP {status}.")
        return _ok(None)


# ---------------------------------------------------------------------------
# Detection / factory
# ---------------------------------------------------------------------------

_KIND_ALIASES = {
    "github": "github", "gh": "github",
    "azuredevops": "azuredevops", "azure-devops": "azuredevops", "azdevops": "azuredevops", "az": "azuredevops",
    "forgejo": "forgejo", "gitea": "forgejo",
}


def detect_forge_kind(remote_url: str) -> str:
    """Auto-detect which platform `remote_url` (origin's URL) points at.
    No remote configured yet (e.g. before the first `git push`) defaults to
    GitHub, today's only supported platform historically."""
    if not remote_url:
        return "github"
    lowered = remote_url.lower()
    if "dev.azure.com" in lowered or "visualstudio.com" in lowered:
        return "azuredevops"
    if "github" in lowered:  # covers github.com and GitHub Enterprise hostnames
        return "github"
    return "forgejo"


def get_forge(local_env: dict | None = None) -> Forge:
    """The active Forge for this repo: DNSCTL_FORGE (.env or the
    environment) overrides auto-detection from origin's remote URL, exactly
    like every other DNSCTL_* team-mode setting (see cli_utils.get_dnsctl_setting)."""
    local_env = local_env or {}
    override = (local_env.get("DNSCTL_FORGE") or os.environ.get("DNSCTL_FORGE") or "").strip().lower()
    kind = _KIND_ALIASES.get(override)
    if not kind:
        if override:
            eprint(f"warning: unrecognized DNSCTL_FORGE='{override}' - ignoring, auto-detecting instead.")
        kind = detect_forge_kind(get_origin_url())

    if kind == "azuredevops":
        return AzureDevOpsForge(local_env)
    if kind == "forgejo":
        return ForgejoForge(local_env)
    return GithubForge()
