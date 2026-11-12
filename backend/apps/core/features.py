"""
apps/core/features.py
---------------------
The feature registry: which modules this school has switched on.

A flag is a boolean column on the ``SchoolSettings`` singleton, named here —
never a separate settings table and never inferred from whether data exists.
Screens ask ``feature_enabled("fees")`` and render a placeholder when it is off;
they must not substitute a zero or a demo figure.

Adding a module: add the column (an additive migration), then one entry here.
Nothing else reads the model field directly.
"""

from __future__ import annotations

from .selectors import school_settings_values

#: Feature slug → label, and the ``SchoolSettings`` field storing the switch.
#: ``disabled_message`` is the sentence a screen shows in place of figures.
FEATURES: dict[str, dict[str, str]] = {
    "fees": {
        "label": "Finance and fees",
        "field": "fees_enabled",
        "disabled_message": "Available once the Finance module is activated.",
    },
    "learning": {
        "label": "Learning (virtual school)",
        "field": "learning_enabled",
        "disabled_message": "Available once the Learning module is activated.",
    },
    "engagement": {
        "label": "Progress and streaks",
        "field": "engagement_enabled",
        "disabled_message": "Available once the Engagement module is activated.",
    },
    "quizzes": {
        "label": "Quizzes",
        "field": "quizzes_enabled",
        "disabled_message": "Available once the Quizzes module is activated.",
    },
    "live_classes": {
        "label": "Live classes and calendar",
        "field": "live_classes_enabled",
        "disabled_message": "Available once the Live Classes module is activated.",
    },
    "feed_community": {
        "label": "Class community feed",
        "field": "feed_community_enabled",
        "disabled_message": (
            "Available once the class community feed is activated for the school. "
            "Each teacher also switches it on for their own class."
        ),
    },
    "homework_grading": {
        "label": "Homework marking and feedback",
        "field": "homework_grading_enabled",
        "disabled_message": (
            "Available once the school switches on homework marking."
        ),
    },
}


class UnknownFeature(KeyError):
    """Raised for a slug that is not in the registry — a coding mistake."""


def feature_enabled(slug: str, *, request=None) -> bool:
    """
    Whether this school has switched the named feature on.

    ``request`` is passed straight to the settings reader so a screen that has
    already read the row for its currency pays nothing for this question; the
    sidebar asks it for every entry it might hide.
    """
    spec = FEATURES.get(slug)
    if spec is None:
        raise UnknownFeature(slug)
    return bool(school_settings_values(request)[spec["field"]])


def feature_disabled_message(slug: str) -> str:
    """
    The sentence a screen prints in place of the figures it is not showing.

    Screens ask for this instead of reading ``FEATURES`` themselves, so the
    wording stays in one place and an off switch never has to be re-invented
    per template. It is a sentence, not a number: an empty card reads as "the
    school owes nothing", which is the exact lie this registry exists to stop.
    """
    spec = FEATURES.get(slug)
    if spec is None:
        raise UnknownFeature(slug)
    return spec["disabled_message"]
