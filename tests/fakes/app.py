"""Stateful fake Azure DevOps + GitHub APIs as a single dependency-free ASGI app.

Serves both APIs under one process:
- Azure: /{org}/{project}/_apis/...
- GitHub: /repos/{owner}/{repo}/... and /search/issues

Used by the integration tests via docker-compose (uvicorn) and can be
mounted in-process for debugging. Zero third-party dependencies (pure ASGI).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

if TYPE_CHECKING:
    from collections.abc import Callable

from tests.fakes.store import AzureStore, GithubStore

AZURE_PATH = re.compile(r"^/(?P<org>[^/]+)/(?P<project>[^/]+)/_apis/(?P<rest>.*)$")
GITHUB_REPO_PATH = re.compile(r"^/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<rest>.*)$")
GITHUB_SEARCH_PATH = re.compile(r"^/search/issues$")


class FakeState:
    def __init__(self) -> None:
        self.azure = AzureStore()
        self.github = GithubStore()
        self.issuer_base = "http://localhost"  # overwritten per-request via Host


async def _read_body(scope: dict, receive: Callable) -> bytes:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            return body


async def _send_json(send: Callable, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_bytes(send: Callable, status: int, data: bytes, content_type: str = "application/octet-stream") -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(data)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": data})


def _apply_patch_fields(fields: dict, operations: list[dict]) -> None:
    for op in operations:
        path = op.get("path", "")
        if path.startswith("/fields/"):
            fields[path.removeprefix("/fields/")] = op.get("value")


def _wi_json(wi) -> dict:
    return wi.to_json()


def _issue_json(issue, include_body: bool = True) -> dict:
    return issue.to_json(include_body=include_body)


# ──────────────────────────────────────────────
#  AZURE ROUTES
# ──────────────────────────────────────────────


async def _azure_route(state: FakeState, method: str, path: str, query: dict, body: bytes, send: Callable) -> bool:
    m = AZURE_PATH.match(path)
    if not m:
        return False
    rest = m.group("rest")
    az = state.azure

    # GET wit/workitemtypes
    if method == "GET" and rest == "wit/workitemtypes":
        await _send_json(send, 200, {"workItemTypes": az.WORK_ITEM_TYPES})
        return True

    # POST wit/wiql
    if method == "POST" and rest == "wit/wiql":
        payload = json.loads(body or b"{}")
        ids = az.evaluate_wiql(payload.get("query", ""))
        await _send_json(send, 200, {"workItems": [{"id": i} for i in ids], "count": len(ids)})
        return True

    # GET wit/workitems?ids=... (batch)
    if method == "GET" and rest == "wit/workitems":
        ids_param = (query.get("ids") or [""])[0]
        requested = [int(x) for x in ids_param.split(",") if x.strip()]
        items = [_wi_json(az.work_items[i]) for i in requested if i in az.work_items]
        await _send_json(send, 200, {"count": len(items), "value": items})
        return True

    # POST wit/workitems/${type}
    if method == "POST" and rest.startswith("wit/workitems/$"):
        item_type = rest.removeprefix("wit/workitems/$")
        valid = {t["name"].lower() for t in az.WORK_ITEM_TYPES}
        if item_type.lower() not in valid:
            await _send_json(send, 400, {"message": f"TF401019: The work item type {item_type} does not exist."})
            return True
        operations = json.loads(body or b"[]")
        wi = az.create_work_item(item_type, operations)
        await _send_json(send, 200, _wi_json(wi))
        return True

    # POST wit/attachments
    if method == "POST" and rest == "wit/attachments":
        file_name = (query.get("fileName") or ["untitled"])[0]
        att = az.upload_attachment(file_name, body)
        await _send_json(send, 200, att)
        return True

    # GET wit/attachments/{id}
    if method == "GET" and rest.startswith("wit/attachments/"):
        att_id = int(rest.removeprefix("wit/attachments/"))
        data = az.attachments.get(att_id)
        if data is None:
            await _send_json(send, 404, {"message": "Attachment not found."})
            return True
        await _send_bytes(send, 200, data)
        return True

    # GET wit/classificationNodes/Areas
    if method == "GET" and rest == "wit/classificationNodes/Areas":
        project = m.group("project")
        await _send_json(send, 200, {"name": project, "path": f"\\{project}", "children": []})
        return True

    # GET work/teamsettings/iterations
    if method == "GET" and rest == "work/teamsettings/iterations":
        await _send_json(send, 200, {"count": len(az.iterations), "value": az.iterations})
        return True

    # GET/PATCH wit/workitems/{id}[/comments]
    m2 = re.match(r"^wit/workitems/(\d+)(/comments)?$", rest)
    if m2:
        wi_id = int(m2.group(1))
        is_comments = bool(m2.group(2))

        if is_comments:
            if method == "POST":
                payload = json.loads(body or b"{}")
                c = az.add_comment(wi_id, payload.get("text", ""))
                if c is None:
                    await _send_json(send, 404, {"message": "Work item not found."})
                    return True
                await _send_json(send, 200, {"comment": c.to_json()})
                return True
            if method == "GET":
                comments = [c.to_json() for c in az.comments.get(wi_id, [])]
                await _send_json(send, 200, {"count": len(comments), "comments": comments})
                return True
        else:
            if method == "GET":
                wi = az.work_items.get(wi_id)
                if wi is None:
                    await _send_json(send, 404, {"message": "Work item not found."})
                    return True
                await _send_json(send, 200, _wi_json(wi))
                return True
            if method == "PATCH":
                operations = json.loads(body or b"[]")
                wi = az.patch_work_item(wi_id, operations)
                if wi is None:
                    await _send_json(send, 404, {"message": "Work item not found."})
                    return True
                await _send_json(send, 200, _wi_json(wi))
                return True

    await _send_json(send, 404, {"message": f"Fake Azure: no route for {method} {path}"})
    return True


# ──────────────────────────────────────────────
#  GITHUB ROUTES
# ──────────────────────────────────────────────


async def _github_route(state: FakeState, method: str, path: str, query: dict, body: bytes, send: Callable) -> bool:
    gh = state.github

    if GITHUB_SEARCH_PATH.match(path) and method == "GET":
        q = (query.get("q") or [""])[0]
        items = [_issue_json(i) for i in gh.search_issues(q)]
        await _send_json(send, 200, {"total_count": len(items), "items": items})
        return True

    m = GITHUB_REPO_PATH.match(path)
    if not m:
        return False
    rest = m.group("rest")

    if method == "GET" and rest == "labels":
        await _send_json(send, 200, gh.labels)
        return True

    if method == "GET" and rest == "milestones":
        await _send_json(send, 200, gh.milestones)
        return True

    if method == "POST" and rest == "issues":
        payload = json.loads(body or b"{}")
        issue = gh.create_issue(
            payload.get("title", ""),
            payload.get("body", ""),
            payload.get("labels"),
            payload.get("assignees"),
            payload.get("milestone"),
        )
        await _send_json(send, 201, _issue_json(issue))
        return True

    if method == "GET" and rest == "issues":
        issues = gh.list_issues(
            state=(query.get("state") or [""])[0],
            assignee=(query.get("assignee") or [""])[0],
        )
        await _send_json(send, 200, [_issue_json(i, include_body=False) for i in issues])
        return True

    m2 = re.match(r"^issues/(\d+)(/comments)?$", rest)
    if m2:
        number = int(m2.group(1))
        is_comments = bool(m2.group(2))

        if is_comments:
            if method == "POST":
                payload = json.loads(body or b"{}")
                c = gh.add_comment(number, payload.get("body", ""))
                if c is None:
                    await _send_json(send, 404, {"message": "Not Found"})
                    return True
                await _send_json(
                    send,
                    201,
                    {"id": c.id, "body": c.text, "user": {"login": c.author}, "created_at": c.created_date},
                )
                return True
            if method == "GET":
                issue = gh.issues.get(number)
                if issue is None:
                    await _send_json(send, 404, {"message": "Not Found"})
                    return True
                comments = [
                    {"id": c.id, "body": c.text, "user": {"login": c.author}, "created_at": c.created_date}
                    for c in issue.comments
                ]
                await _send_json(send, 200, comments)
                return True
        else:
            if method == "GET":
                issue = gh.issues.get(number)
                if issue is None:
                    await _send_json(send, 404, {"message": "Not Found"})
                    return True
                await _send_json(send, 200, _issue_json(issue))
                return True
            if method == "PATCH":
                payload = json.loads(body or b"{}")
                issue = gh.patch_issue(number, payload)
                if issue is None:
                    await _send_json(send, 404, {"message": "Not Found"})
                    return True
                await _send_json(send, 200, _issue_json(issue))
                return True

    await _send_json(send, 404, {"message": f"Fake GitHub: no route for {method} {path}"})
    return True


# ──────────────────────────────────────────────
#  ASGI ENTRYPOINT
# ──────────────────────────────────────────────


def create_app() -> Callable:
    state = FakeState()

    async def app(scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return
        method = scope["method"]
        path = scope["path"].rstrip("/")
        raw_query = parse_qs(scope.get("query_string", b"").decode())
        body = await _read_body(scope, receive)

        if method == "GET" and path == "/healthz":
            await _send_json(send, 200, {"status": "ok"})
            return

        if await _azure_route(state, method, path, raw_query, body, send):
            return
        if await _github_route(state, method, path, raw_query, body, send):
            return

        await _send_json(send, 404, {"message": f"No route for {method} {path}"})

    return app


app = create_app()
