"""
apps/learning/reply_starters.py
-------------------------------
The tap-to-start sentences above the reply box.

Two sets, deliberately small:

  * teachers get guiding questions that help a pupil find the answer without
    handing it over;
  * pupils helping a classmate get gentle openers that end where they carry on
    typing.

A starter is plain text. Clicking one puts those words in the reply box and
leaves the cursor at the end, so the writer adds their own sentence. There are
no ``[placeholders]``; the School Settings form refuses to save a set that
contains one, because the words are typed into a real child's reply exactly as
written.

Where the text lives: ``SchoolSettings.feed_teacher_starters`` and
``SchoolSettings.feed_student_starters`` (School Settings → Reply starters),
one starter per line. A blank box uses the built-in sets below, so clearing a
box restores the defaults.
"""

from __future__ import annotations

#: The wording shipped with F2a. Used when the school has not written its own.
DEFAULT_TEACHER_STARTERS: tuple[str, ...] = (
    "What have you tried so far?",
    "Which part is confusing you?",
    "What is the question asking you to find?",
    "Have you seen a question like this before? What did you do then?",
    "Let's break it into smaller steps. What do you think the first step is?",
    "Look at the lesson notes again. What do you notice?",
    "What do you think would happen if you tried a different method?",
    "Can you describe or draw what you are picturing?",
    "Good question. Check your working from the beginning and tell me where you get stuck.",
    "You are closer than you think. What does the key word in the question mean to you?",
)

DEFAULT_STUDENT_STARTERS: tuple[str, ...] = (
    "I got stuck on this too. What helped me was",
    "Here is how I thought about it:",
    "Try re-reading the lesson notes. I found the idea in",
    "Thanks for asking. Another way to see it is",
    "Can you tell me which step you are on?",
)

TEACHER_ROLES = {"teacher", "admin", "director"}


def _parse(raw: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    if not (raw or "").strip():
        return defaults
    lines = []
    for line in raw.splitlines():
        text = line.strip()
        # A bracket means a placeholder that would end up in a reply. The
        # settings form refuses to save one; this second guard means an older
        # row can never leak one into a child's reply box.
        if text and "[" not in text and "]" not in text:
            lines.append(text)
    return tuple(lines) or defaults


def starters_for_role(role: str, *, request=None) -> tuple[str, ...]:
    """The starters a role may tap. Parents get none: they cannot reply."""
    if role not in TEACHER_ROLES and role != "student":
        return ()

    from apps.core.selectors import school_settings_row

    row = school_settings_row(request)
    if role in TEACHER_ROLES:
        return _parse(
            getattr(row, "feed_teacher_starters", "") or "",
            DEFAULT_TEACHER_STARTERS,
        )
    return _parse(
        getattr(row, "feed_student_starters", "") or "",
        DEFAULT_STUDENT_STARTERS,
    )


def _normalise(text: str) -> str:
    """Whitespace/case/trailing-punctuation-insensitive comparison form."""
    return " ".join(text.split()).casefold().rstrip(" .!?…")


def is_starter_only(body: str, role: str, *, request=None) -> bool:
    """
    True when a pupil's reply is nothing but a starter with nothing added.

    Such a reply is refused by ``create_reply``: a starter on its own is not an
    answer, and sending it would alert the asker for nothing. Teachers may send
    a guiding question on its own — that is the point of their set.
    """
    if role != "student":
        return False
    normalised = _normalise(body)
    if not normalised:
        return False
    return any(
        normalised == _normalise(starter)
        for starter in starters_for_role(role, request=request)
    )
