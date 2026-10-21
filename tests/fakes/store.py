"""Stateful in-memory stores for the fake Azure DevOps / GitHub APIs."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class WorkItem:
    id: int
    rev: int = 1
    fields: dict[str, Any] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rev": self.rev,
            "fields": self.fields,
            "relations": self.relations,
            "url": f"/_apis/wit/workItems/{self.id}",
        }


@dataclass
class Comment:
    id: int
    text: str
    author: str
    created_date: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "createdBy": {"displayName": self.author},
            "createdDate": self.created_date,
        }


@dataclass
class Issue:
    number: int
    title: str
    body: str
    state: str = "open"
    labels: list[dict[str, str]] = field(default_factory=list)
    assignee: dict[str, str] | None = None
    milestone: dict[str, Any] | None = None
    comments: list[Comment] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_json(self, include_body: bool = True) -> dict[str, Any]:
        data = {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "labels": self.labels,
            "assignee": self.assignee,
            "milestone": self.milestone,
            "html_url": f"https://github.com/fake/{self.number}",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_body:
            data["body"] = self.body
        return data


class AzureStore:
    """Stateful store backing the fake Azure DevOps API."""

    WORK_ITEM_TYPES = [
        {"name": "Task", "description": "A unit of work"},
        {"name": "Bug", "description": "A product defect"},
        {"name": "User Story", "description": "A user-facing feature"},
        {"name": "Issue", "description": "A problem to track"},
        {"name": "Change Request", "description": "A proposed change"},
    ]

    def __init__(self) -> None:
        self._ids = itertools.count(101)
        self._comment_ids = itertools.count(1)
        self._attachment_ids = itertools.count(1)
        self.work_items: dict[int, WorkItem] = {}
        self.comments: dict[int, list[Comment]] = {}
        self.attachments: dict[int, bytes] = {}
        self.iterations: list[dict[str, Any]] = [
            {
                "id": "sprint-1",
                "name": "Sprint 1",
                "path": "\\MockProj\\Sprint 1",
                "attributes": {"startDate": "2026-01-01", "finishDate": "2026-01-14", "timeFrame": "past"},
            },
            {
                "id": "sprint-2",
                "name": "Sprint 2",
                "path": "\\MockProj\\Sprint 2",
                "attributes": {"startDate": "2026-01-15", "finishDate": "2026-01-28", "timeFrame": "current"},
            },
        ]

    def create_work_item(self, item_type: str, operations: list[dict[str, Any]]) -> WorkItem:
        wi_id = next(self._ids)
        fields: dict[str, Any] = {
            "System.WorkItemType": item_type,
            "System.State": "New",
            "System.CreatedDate": _now_iso(),
            "System.ChangedDate": _now_iso(),
        }
        relations: list[dict[str, Any]] = []
        for op in operations:
            path = op.get("path", "")
            value = op.get("value")
            if path.startswith("/fields/"):
                fields[path.removeprefix("/fields/")] = value
            elif path == "/relations/-" and isinstance(value, dict):
                relations.append(value)
        wi = WorkItem(id=wi_id, fields=fields, relations=relations)
        self.work_items[wi_id] = wi
        self.comments[wi_id] = []
        return wi

    def patch_work_item(self, wi_id: int, operations: list[dict[str, Any]]) -> WorkItem | None:
        wi = self.work_items.get(wi_id)
        if wi is None:
            return None
        for op in operations:
            path = op.get("path", "")
            value = op.get("value")
            if path.startswith("/fields/"):
                fields_key = path.removeprefix("/fields/")
                wi.fields[fields_key] = value
                wi.fields["System.ChangedDate"] = _now_iso()
            elif path == "/relations/-" and isinstance(value, dict):
                wi.relations.append(value)
        wi.rev += 1
        return wi

    def add_comment(self, wi_id: int, text: str, author: str = "Fake User") -> Comment | None:
        if wi_id not in self.work_items:
            return None
        c = Comment(
            id=next(self._comment_ids),
            text=text,
            author=author,
            created_date=_now_iso(),
        )
        self.comments.setdefault(wi_id, []).append(c)
        return c

    def upload_attachment(self, file_name: str, data: bytes) -> dict[str, Any]:
        att_id = next(self._attachment_ids)
        self.attachments[att_id] = data
        return {"id": att_id, "url": f"/_apis/wit/attachments/{att_id}?fileName={file_name}"}

    def evaluate_wiql(self, query: str) -> list[int]:
        """Minimal WIQL evaluator: supports =, <>, Contains on a single AND chain."""
        candidates = list(self.work_items.values())
        where_idx = query.upper().find("WHERE")
        if where_idx == -1:
            return [wi.id for wi in candidates]

        where_clause = query[where_idx + len("WHERE") :]
        conditions = [c.strip() for c in where_clause.split(" AND ")]
        results = []
        for wi in candidates:
            if all(self._match_condition(wi, cond) for cond in conditions):
                results.append(wi.id)
        return results

    @staticmethod
    def _match_condition(wi: WorkItem, condition: str) -> bool:
        import re

        m = re.match(r"\[([^\]]+)\]\s*(=|<>|Contains)\s*'([^']*)'", condition.strip())
        if not m:
            return True
        field_name, op, expected = m.groups()
        actual = wi.fields.get(field_name, "")
        if not isinstance(actual, str):
            actual = str(actual)
        if op == "=":
            return actual.lower() == expected.lower()
        if op == "<>":
            return actual.lower() != expected.lower()
        if op == "Contains":
            return expected.lower() in actual.lower()
        return False


class GithubStore:
    """Stateful store backing the fake GitHub API."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._comment_ids = itertools.count(1)
        self.issues: dict[int, Issue] = {}
        self.labels: list[dict[str, str]] = [
            {"name": "bug", "color": "d73a4a"},
            {"name": "enhancement", "color": "a2eeef"},
            {"name": "priority: high", "color": "b60205"},
            {"name": "priority: low", "color": "0e8a16"},
        ]
        self.milestones: list[dict[str, Any]] = [
            {"number": 1, "title": "Sprint 1", "state": "closed"},
            {"number": 2, "title": "Sprint 2", "state": "open"},
        ]

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        milestone_number: int | None = None,
    ) -> Issue:
        number = next(self._ids)
        label_objs = [{"name": name, "color": "cccccc"} for name in (labels or [])]
        assignee = {"login": assignees[0]} if assignees else None
        milestone = next((m for m in self.milestones if m["number"] == milestone_number), None)
        issue = Issue(
            number=number,
            title=title,
            body=body,
            labels=label_objs,
            assignee=assignee,
            milestone=milestone,
        )
        self.issues[number] = issue
        return issue

    def list_issues(self, state: str = "", assignee: str = "") -> list[Issue]:
        issues = list(self.issues.values())
        if state:
            issues = [i for i in issues if i.state == state]
        if assignee and assignee != "*":
            issues = [i for i in issues if i.assignee and i.assignee.get("login", "").lower() == assignee.lower()]
        return issues

    def patch_issue(self, number: int, payload: dict[str, Any]) -> Issue | None:
        issue = self.issues.get(number)
        if issue is None:
            return None
        if "title" in payload:
            issue.title = payload["title"]
        if "body" in payload:
            issue.body = payload["body"]
        if "state" in payload:
            issue.state = payload["state"]
        if "labels" in payload:
            issue.labels = [{"name": n, "color": "cccccc"} for n in payload["labels"]]
        if "assignees" in payload:
            assignees = payload["assignees"] or []
            issue.assignee = {"login": assignees[0]} if assignees else None
        elif "assignee" in payload:
            login = payload["assignee"]
            issue.assignee = {"login": login} if login else None
        if "milestone" in payload:
            number_m = payload["milestone"]
            issue.milestone = next((m for m in self.milestones if m["number"] == number_m), None) if number_m else None
        issue.updated_at = _now_iso()
        return issue

    def add_comment(self, number: int, body: str) -> Comment | None:
        issue = self.issues.get(number)
        if issue is None:
            return None
        c = Comment(
            id=next(self._comment_ids),
            text=body,
            author="fake-bot",
            created_date=_now_iso(),
        )
        issue.comments.append(c)
        return c

    def search_issues(self, query: str) -> list[Issue]:
        """Tiny subset of GitHub search: bare terms match title/body, plus
        is:/label:/assignee:/milestone: qualifiers (repo: is ignored)."""
        results = []
        terms: list[str] = []
        state = label = assignee = milestone = ""
        for token in query.split():
            if token.startswith("repo:"):
                continue
            if token.startswith("is:"):
                state = token.removeprefix("is:")
            elif token.startswith("label:"):
                label = token.removeprefix("label:").lower()
            elif token.startswith("assignee:"):
                assignee = token.removeprefix("assignee:").lower()
            elif token.startswith("milestone:"):
                milestone = token.removeprefix("milestone:").lower()
            else:
                terms.append(token.lower())
        for issue in self.issues.values():
            if state and issue.state != state:
                continue
            if label and not any(lbl.get("name", "").lower() == label for lbl in issue.labels):
                continue
            if assignee and not (issue.assignee and issue.assignee.get("login", "").lower() == assignee):
                continue
            if milestone and not (issue.milestone and issue.milestone.get("title", "").lower() == milestone):
                continue
            haystack = f"{issue.title} {issue.body}".lower()
            if all(term in haystack for term in terms):
                results.append(issue)
        return results
