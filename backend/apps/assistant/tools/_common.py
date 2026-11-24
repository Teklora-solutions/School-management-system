"""
apps/assistant/tools/registry.py
----------------------------------
The tool registry.  Every tool in this file is:
  - read-only (no mutations)
  - scoped to the calling user's own data (never another person's)
  - argument-validated before the selector is called
  - audit-logged (tool name, outcome, field names returned -- never values)
  - permission-checked inside the tool, not just at the view level

Design rules (from architecture.md section 6):
  * The model never receives a user ID from its own output.
  * Arguments come from the conversation turn; they are validated here.
  * Selectors are called with the acting user's identity from the Django
    session, not from any string the model produced.
  * Results are minimised: only fields the prompt needs are included.
  * Sensitive reads are logged in ToolCallLog (see models.py).
  * No free-form SQL; no arbitrary model references.

Adding a tool
-------------
1. Write a function ``_tool_<name>(user, params) -> dict`` below.
2. Register it in REGISTRY.
3. Add its name to the right role set in policies.py.
4. Write a test in tests/test_tools.py that proves it cannot leak data.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str                    # shown in the system prompt tool catalogue
    allowed_roles: frozenset[str]       # which roles may call this tool
    params: dict                        # JSON-schema-style param definitions
    fn: Any                             # callable(user, params) -> dict
    result_fields: tuple[str, ...]      # expected top-level result keys (for audit log)


# ---------------------------------------------------------------------------
# Parameter validation helpers
# ---------------------------------------------------------------------------

class ToolError(ValueError):
    """Raised when a tool call cannot proceed; the message is safe to surface."""
    code: str = "invalid"

    def __init__(self, msg: str, *, code: str = "invalid"):
        self.code = code
        super().__init__(msg)


def _require(params: dict, name: str, *, type_=str):
    """Validate a required parameter exists and is the right type."""
    if name not in params:
        raise ToolError("Missing required parameter: %s" % name)
    val = params[name]
    if not isinstance(val, type_):
        raise ToolError("Parameter %s must be %s" % (name, type_.__name__))
    return val


def _int_param(params: dict, name: str, *, default=None, min_val=None, max_val=None):
    val = params.get(name, default)
    if val is None:
        return None
    try:
        val = int(val)
    except (TypeError, ValueError):
        raise ToolError("Parameter %s must be an integer" % name)
    if min_val is not None and val < min_val:
        raise ToolError("Parameter %s must be >= %d" % (name, min_val))
    if max_val is not None and val > max_val:
        raise ToolError("Parameter %s must be <= %d" % (name, max_val))
    return val


def _resolve_child(user, child_id: int | None):
    """
    Resolve a Student for a parent user.  If child_id is None, use the
    session-active child.  Raises ToolError if the child is not linked to
    this parent.  NEVER trusts child_id from the model's text alone.
    """
    from apps.students.models import Student

    role = getattr(user, "role", "")
    if role == "student":
        profile = getattr(user, "student_profile", None)
        if profile is None:
            raise ToolError("No student profile linked to this account.", code="denied")
        return profile

    if role == "parent":
        parent = getattr(user, "parent_profile", None)
        if parent is None:
            raise ToolError("No parent profile linked to this account.", code="denied")
        linked_ids = set(
            parent.children_links.values_list("student_id", flat=True)
        )
        if not linked_ids:
            raise ToolError("No children are linked to this account.", code="denied")
        if child_id is None:
            # Use the first linked child
            child_id = sorted(linked_ids)[0]
        if child_id not in linked_ids:
            raise ToolError(
                "You are not authorised to view this child's information.", code="denied"
            )
        return Student.objects.select_related(
            "user", "current_class", "current_stream"
        ).get(pk=child_id)

    raise ToolError("This tool is not available for your role.", code="denied")


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

