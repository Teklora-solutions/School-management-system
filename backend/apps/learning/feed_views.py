from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.views.generic import TemplateView

from apps.core.features import feature_disabled_message, feature_enabled
from apps.core.mixins import (
    AdminOrDirectorMixin,
    DirectorRequiredMixin,
    RoleRequiredMixin,
    TeacherRequiredMixin,
)
from apps.homework.selectors import class_roster
from apps.students.models import Student

from . import selectors
from .classroom_views import (
    EngagementEnabledMixin,
    LearningEnabledMixin,
    StudentOrParentMixin,
    _resolve_student,
)

logger = logging.getLogger(__name__)

from . import feed_media_services, feed_selectors, feed_services, poll_services
from .models import (
    FEED_FILE_MAX_BYTES,
    FEED_MAX_FILES,
    FEED_MAX_PHOTOS,
    FEED_PHOTO_MAX_BYTES,
    ClassFeedSettings,
    ClassPost,
    ClassPostAttachment,
    ClassPostReaction,
    ClassPostReply,
)


class _FeedClassMixin:
    """
    Shared helper: resolve the SchoolClass for feed views and guard access.

    URL kwargs: class_id (int)
    Sets self.school_class on the instance.
    """

    def _resolve_class(self, request, class_id: int):
        from apps.academics.models import SchoolClass
        school_class = get_object_or_404(SchoolClass, pk=class_id)
        if not feed_selectors.can_view_feed(request.user, school_class):
            raise Http404  # 404, not 403 — don't reveal the class exists
        return school_class


_BAD_LINK = object()


def _posted_link(request, field: str, kind: str):
    """Resolve an optional lesson/quiz id from the composer, or a sentinel."""
    raw = (request.POST.get(field) or "").strip()
    if not raw:
        return None
    from .models import Lesson, Quiz

    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return _BAD_LINK
    if kind == "Lesson":
        obj = Lesson.objects.filter(pk=pk).select_related("module").first()
    else:
        obj = Quiz.objects.filter(pk=pk).select_related("lesson__module").first()
    return obj if obj is not None else _BAD_LINK


def _nairobi_datetime(raw: str):
    """
    Parse a ``datetime-local`` value as Nairobi time.

    The device may be set to another zone, so the entered wall-clock time is
    pinned to Africa/Nairobi rather than to the browser's zone.
    """
    from zoneinfo import ZoneInfo

    from django.conf import settings
    from django.utils import timezone as dj_timezone
    from django.utils.dateparse import parse_datetime

    value = parse_datetime((raw or "").strip())
    if value is None:
        return None
    if dj_timezone.is_naive(value):
        value = value.replace(tzinfo=ZoneInfo(settings.TIME_ZONE))
    return value


def _feed_viewer_student(request):
    return _resolve_student(request) if request.user.role in ("student", "parent") else None


def _feed_link_lessons(school_class, student=None):
    from .models import Lesson

    lessons = list(
        Lesson.objects.filter(
            module__school_class=school_class,
            module__is_published=True,
            is_published=True,
        )
        .select_related("module__subject")
        .prefetch_related("assigned_students")
        .order_by("title")[:200]
    )
    if student is not None:
        lessons = [l for l in lessons if course_selectors.lesson_visible_to_student(l, student)]
    return lessons


def _feed_link_quizzes(school_class, student=None):
    from .models import Quiz

    quizzes = list(
        Quiz.objects.filter(
            lesson__module__school_class=school_class,
            is_published=True,
        )
        .select_related("lesson__module__subject")
        .prefetch_related("assigned_students", "lesson__assigned_students")
        .order_by("title")[:200]
    )
    if student is not None:
        quizzes = [q for q in quizzes if course_selectors.quiz_visible_to_student(q, student)]
    return quizzes


class FeedHomeView(RoleRequiredMixin, LearningEnabledMixin, TemplateView):
    """
    "Class Feed" menu entry: go straight to the feed.

    One class whose feed this user may open → straight there. Several → a
    short chooser. None → an honest empty state, not a dead menu link.
    """

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]
    template_name = "features/learning/feed_home.html"

    def get(self, request, *args, **kwargs):
        from django.shortcuts import redirect

        classes = feed_selectors.classrooms_for_feed(request.user)
        if len(classes) == 1:
            return redirect("learning:class_feed", class_id=classes[0].pk)
        self.classes = classes
        return self.render_to_response({"classes": classes})


class ClassFeedView(_FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView):
    """
    Main class feed page.  Shows all posts for a class.
    Teachers see removed posts (greyed-out). Students and parents do not.
    Contains the new-post form inline (POST handled here too).

    When the community feed is switched on for this class the page renders
    the new experience (filters, replies status, Show more); when it is off
    the original notice board renders exactly as before.
    """

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]
    template_name = "features/learning/class_feed.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        school_class = self._resolve_class(self.request, self.kwargs["class_id"])
        community_on = feed_selectors.community_enabled_for(
            school_class, request=self.request
        )
        filter_key = self.request.GET.get("filter", "all")
        search = (self.request.GET.get("q") or "").strip()[:80]
        # Polls are created with their own card, never the post box.
        post_types = [
            choice
            for choice in ClassPost.PostType.choices
            if choice[0] != ClassPost.PostType.POLL
        ]
        if not community_on:
            # The original notice board offered four types; the two community
            # types must not appear until the class switch is on.
            legacy_types = {
                ClassPost.PostType.ANNOUNCEMENT,
                ClassPost.PostType.QUESTION,
                ClassPost.PostType.TIP,
                ClassPost.PostType.CELEBRATION,
            }
            post_types = [(v, label) for v, label in post_types if v in legacy_types]

        can_post = feed_selectors.can_post(self.request.user, school_class)
        media_on = feed_media_services.media_enabled_for(
            school_class, request=self.request
        )
        can_upload_media = (
            community_on
            and can_post
            and feed_media_services.can_upload_media(
                self.request.user, school_class, request=self.request
            )
        )
        ctx.update(
            {
                "school_class": school_class,
                "community_on": community_on,
                "can_post": can_post,
                "media_on": media_on,
                "can_upload_media": can_upload_media,
                "student_uploads_on": feed_media_services.student_uploads_enabled_for(
                    school_class, request=self.request
                ),
                "photo_limit": FEED_MAX_PHOTOS,
                "file_limit": FEED_MAX_FILES,
                "photo_max_mb": FEED_PHOTO_MAX_BYTES // (1024 * 1024),
                "file_max_mb": FEED_FILE_MAX_BYTES // (1024 * 1024),
                "media_alt_required": (
                    can_upload_media
                    and self.request.user.role in ("teacher", "admin", "director")
                ),
                "can_manage_feed": self.request.user.role
                in ("teacher", "admin", "director"),
                "is_moderator": feed_selectors.is_moderator(self.request.user),
                "post_types": post_types,
                "student_allowed_types": list(ClassPost.STUDENT_ALLOWED_TYPES),
                "emoji_choices": ClassPostReaction.Emoji.choices,
                "is_teacher": self.request.user.role in ("teacher", "admin", "director"),
                "is_parent": self.request.user.role == "parent",
                "feed_filters": feed_selectors.feed_filters_for(self.request.user),
                "active_filter": filter_key,
                "active_search": search,
                "polls_on": poll_services.polls_enabled_for(school_class),
                "mention_names": (
                    feed_selectors.mention_picker_for(school_class)
                    if community_on and can_post
                    else ()
                ),
                "can_schedule": (
                    community_on and self.request.user.role in ("teacher", "admin", "director")
                ),
                "draft_body": "",
                "draft_type": "",
                "draft_lesson_id": None,
                "draft_quiz_id": None,
                "copied_from": None,
            }
        )

        if community_on:
            page = feed_selectors.feed_page(
                school_class=school_class,
                viewer=self.request.user,
                filter_key=filter_key,
                search=search,
                student=_feed_viewer_student(self.request),
            )
            ctx.update(
                {
                    "posts": page.posts,
                    "pinned_posts": page.pinned,
                    "has_more": page.has_more,
                    "next_before_id": page.next_before_id,
                    "latest_visible_id": page.latest_visible_id,
                    "link_lessons": _feed_link_lessons(school_class, _feed_viewer_student(self.request)),
                    "link_quizzes": _feed_link_quizzes(school_class, _feed_viewer_student(self.request)),
                }
            )
            ctx.update(self._copy_draft(school_class, ctx["can_post"]))
        else:
            ctx["posts"] = feed_selectors.feed_for_classroom(
                school_class=school_class, viewer=self.request.user,
                student=_feed_viewer_student(self.request),
            )
        return ctx

    def _copy_draft(self, school_class, can_post) -> dict:
        """
        Prefill the composer from an existing post ("Duplicate").  Nothing is
        written until the author presses Post.
        """
        if not can_post:
            return {}
        raw = self.request.GET.get("copy")
        try:
            copy_id = int(raw) if raw else None
        except (TypeError, ValueError):
            copy_id = None
        if not copy_id:
            return {}
        source = ClassPost.objects.filter(
            pk=copy_id, school_class=school_class, is_removed=False
        ).first()
        if source is None:
            return {}
        if (
            source.is_scheduled
            and self.request.user.role not in ("teacher", "admin", "director")
        ):
            return {}
        composer_types = {
            value for value, _label in ClassPost.PostType.choices
            if value != ClassPost.PostType.POLL
        }
        return {
            "draft_body": source.body,
            "draft_type": (
                source.post_type
                if source.post_type in composer_types
                else ClassPost.PostType.ANNOUNCEMENT
            ),
            "draft_lesson_id": source.lesson_id,
            "draft_quiz_id": source.quiz_id,
            "copied_from": source,
        }

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        from django.urls import reverse

        school_class = self._resolve_class(request, self.kwargs["class_id"])

        body = request.POST.get("body", "").strip()
        post_type = request.POST.get("post_type", ClassPost.PostType.ANNOUNCEMENT)
        lesson = _posted_link(request, "lesson_id", "Lesson")
        quiz = _posted_link(request, "quiz_id", "Quiz")
        if lesson is _BAD_LINK or quiz is _BAD_LINK:
            messages.error(request, "That linked item was not found.")
            return redirect("learning:class_feed", class_id=school_class.pk)

        if not body:
            messages.error(request, "Post body cannot be empty.")
            return redirect("learning:class_feed", class_id=school_class.pk)

        scheduled_at = None
        raw_schedule = (request.POST.get("scheduled_at") or "").strip()
        if raw_schedule:
            scheduled_at = _nairobi_datetime(raw_schedule)
            if scheduled_at is None:
                messages.error(request, "That scheduled time is not valid.")
                return redirect("learning:class_feed", class_id=school_class.pk)

        # Every upload is checked before a post row exists, so a bad picture
        # cannot leave an empty post behind.
        try:
            uploads = feed_media_services.validate_uploads(
                request.FILES.getlist("photos"),
                request.FILES.getlist("files"),
                actor=request.user,
                school_class=school_class,
                alt_text=request.POST.get("photo_alt", ""),
                request=request,
            )
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("learning:class_feed", class_id=school_class.pk)

        try:
            post = feed_services.create_post(
                author=request.user,
                school_class=school_class,
                body=body,
                post_type=post_type,
                lesson=lesson,
                quiz=quiz,
                scheduled_at=scheduled_at,
            )
            if uploads:
                try:
                    feed_media_services.save_uploads(
                        post, uploads, actor=request.user, request=request
                    )
                except Exception:  # noqa: BLE001 — storage failure must not leave a post
                    logger.exception("Feed media save failed for post %s", post.pk)
                    post.delete()
                    messages.error(
                        request,
                        "Those photos or files could not be saved. "
                        "Please try again.",
                    )
                    return redirect(
                        "learning:class_feed", class_id=school_class.pk
                    )
            if scheduled_at is not None:
                from django.utils.formats import date_format
                from django.utils import timezone as dj_timezone

                messages.success(
                    request,
                    "Post scheduled for "
                    f"{date_format(dj_timezone.localtime(post.scheduled_at), 'j M, g:i a')}.",
                )
                return redirect(
                    reverse(
                        "learning:class_feed", kwargs={"class_id": school_class.pk}
                    )
                    + "?posted=1"
                )
            messages.success(request, "Your post has been added to the class feed.")
            return redirect(
                reverse("learning:class_feed", kwargs={"class_id": school_class.pk})
                + "?posted=1"
            )
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        except ValueError as exc:
            messages.error(request, str(exc))

        return redirect("learning:class_feed", class_id=school_class.pk)


class ClassFeedPostActionView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """
    POST-only view for moderation actions on a single post:
      action=remove   → soft-delete
      action=restore  → un-remove
      action=pin      → toggle pin
      action=react    → add/toggle reaction (emoji in POST data)

    Returns a redirect back to the feed.  The reaction action also accepts
    X-Requested-With: XMLHttpRequest for a lightweight JSON response so the
    reaction buttons can update in-place without a full reload.
    """

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        from django.http import JsonResponse

        school_class = self._resolve_class(request, self.kwargs["class_id"])
        post = get_object_or_404(
            ClassPost, pk=self.kwargs["post_id"], school_class=school_class
        )
        action = request.POST.get("action", "")
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        try:
            if action == "remove":
                feed_services.remove_post(
                    post=post,
                    removed_by=request.user,
                    reason=request.POST.get("reason", "").strip(),
                    request=request,
                )
                messages.success(request, "Post removed.")

            elif action == "restore":
                feed_services.restore_post(
                    post=post, restored_by=request.user, request=request
                )
                messages.success(request, "Post restored.")

            elif action == "pin":
                feed_services.toggle_pin(
                    post=post, actor=request.user, request=request
                )
                label = "pinned" if post.is_pinned else "unpinned"
                messages.success(request, f"Post {label}.")

            elif action == "react":
                emoji = request.POST.get("emoji") or None
                reaction = feed_services.set_reaction(
                    post=post, user=request.user, emoji=emoji
                )
                if is_xhr:
                    from apps.learning.feed_selectors import reaction_summary
                    counts = reaction_summary(post=post)
                    return JsonResponse(
                        {
                            "ok": True,
                            "counts": counts,
                            "viewer_emoji": reaction.emoji if reaction else None,
                        }
                    )
            else:
                messages.error(request, "Unknown action.")

        except PermissionDenied as exc:
            if is_xhr:
                return JsonResponse({"ok": False, "error": str(exc)}, status=403)
            messages.error(request, str(exc))

        return redirect("learning:class_feed", class_id=school_class.pk)

    def get(self, request, *args, **kwargs):
        raise Http404


class ClassFeedPostEditView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """POST-only: the author rewrites a post's words."""

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        from django.urls import reverse

        school_class = self._resolve_class(request, self.kwargs["class_id"])
        post = get_object_or_404(
            ClassPost, pk=self.kwargs["post_id"], school_class=school_class
        )
        try:
            feed_services.edit_post(
                post=post,
                actor=request.user,
                body=request.POST.get("body", ""),
                request=request,
            )
            messages.success(request, "Post updated.")
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))

        target = reverse(
            "learning:class_feed", kwargs={"class_id": school_class.pk}
        )
        return redirect(f"{target}#post-{post.pk}")

    def get(self, request, *args, **kwargs):
        raise Http404


class ClassFeedItemsView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """HTML fragment for "Show more" and for filter switches."""

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]
    template_name = "features/learning/_feed_items.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        school_class = self._resolve_class(self.request, self.kwargs["class_id"])
        if not feed_selectors.community_enabled_for(
            school_class, request=self.request
        ):
            raise Http404
        before_raw = self.request.GET.get("before_id")
        try:
            before_id = int(before_raw) if before_raw else None
        except (TypeError, ValueError):
            before_id = None
        search = (self.request.GET.get("q") or "").strip()[:80]
        page = feed_selectors.feed_page(
            school_class=school_class,
            viewer=self.request.user,
            filter_key=self.request.GET.get("filter", "all"),
            before_id=before_id,
            search=search,
            student=_feed_viewer_student(self.request),
        )
        ctx.update(
            {
                "school_class": school_class,
                "posts": page.posts,
                "has_more": page.has_more,
                "next_before_id": page.next_before_id,
                "active_search": search,
                "emoji_choices": ClassPostReaction.Emoji.choices,
                "is_moderator": feed_selectors.is_moderator(self.request.user),
                "is_parent": self.request.user.role == "parent",
                "can_post": feed_selectors.can_post(self.request.user, school_class),
            }
        )
        return ctx

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data())


class ClassFeedPollView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """Very cheap JSON poll: how many visible posts are newer than *after*."""

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def get(self, request, *args, **kwargs):
        school_class = self._resolve_class(request, self.kwargs["class_id"])
        if not feed_selectors.community_enabled_for(
            school_class, request=request
        ):
            return JsonResponse({"count": 0})
        try:
            after_id = int(request.GET.get("after", 0) or 0)
        except (TypeError, ValueError):
            after_id = 0
        count = feed_selectors.new_posts_count(
            school_class=school_class,
            viewer=request.user,
            after_id=after_id,
            filter_key=request.GET.get("filter", "all"),
        )
        return JsonResponse({"count": count})


class ClassFeedPollCreateView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """POST-only: teacher creates a poll post with 2–6 options."""

    allowed_roles = ["admin", "director", "teacher"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect

        from .models import ClassPoll

        school_class = self._resolve_class(request, self.kwargs["class_id"])

        raw_close = (request.POST.get("closes_at") or "").strip()
        closes_at = None
        if raw_close:
            closes_at = _nairobi_datetime(raw_close)
            if closes_at is None:
                messages.error(request, "That closing time is not valid.")
                return redirect("learning:class_feed", class_id=school_class.pk)

        options = [
            request.POST.get(f"option_{index}", "")
            for index in range(1, poll_services.MAX_OPTIONS + 1)
        ]
        try:
            poll_services.create_poll(
                author=request.user,
                school_class=school_class,
                question=request.POST.get("question", ""),
                options=options,
                results_visibility=request.POST.get(
                    "results_visibility",
                    ClassPoll.ResultsVisibility.AFTER_CLOSE,
                ),
                closes_at=closes_at,
            )
            messages.success(request, "Poll added to the class feed.")
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))

        return redirect("learning:class_feed", class_id=school_class.pk)

    def get(self, request, *args, **kwargs):
        raise Http404


class ClassFeedPollVoteView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """POST-only: a student votes, or changes a vote before the poll closes."""

    allowed_roles = ["student"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        from django.urls import reverse

        from .models import ClassPoll

        school_class = self._resolve_class(request, self.kwargs["class_id"])

        # The view checks class membership too; cast_vote re-checks it.
        student = getattr(request.user, "student_profile", None)
        if student is None or student.current_class_id != school_class.pk:
            messages.error(
                request, "You can only vote in polls for your own class."
            )
            return redirect("learning:class_feed", class_id=school_class.pk)

        poll = get_object_or_404(
            ClassPoll,
            post_id=self.kwargs["post_id"],
            post__school_class=school_class,
        )
        try:
            option_id = int(request.POST.get("option", ""))
        except (TypeError, ValueError):
            option_id = None

        try:
            if option_id is None:
                raise ValueError("Choose one of the poll options.")
            poll_services.cast_vote(
                poll=poll, voter=request.user, option_id=option_id
            )
            messages.success(request, "Your vote was saved.")
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(
                "learning:class_feed", class_id=school_class.pk
            )

        if request.POST.get("from") == "feed":
            return redirect(
                f"{reverse('learning:class_feed', kwargs={'class_id': school_class.pk})}"
                f"?voted=1#post-{poll.post_id}"
            )
        return redirect(
            f"{reverse('learning:feed_post', kwargs={'class_id': school_class.pk, 'post_id': poll.post_id})}"
            f"?voted=1#poll-{poll.post_id}"
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class ClassFeedThreadView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """One post and its replies — the only place a reply can be written."""

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]
    template_name = "features/learning/class_feed_thread.html"

    def get(self, request, *args, **kwargs):
        from django.shortcuts import redirect

        school_class = self._resolve_class(request, self.kwargs["class_id"])
        if not feed_selectors.community_enabled_for(
            school_class, request=request
        ):
            return redirect("learning:class_feed", class_id=school_class.pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        school_class = self._resolve_class(self.request, self.kwargs["class_id"])
        thread = feed_selectors.post_thread(
            post_id=self.kwargs["post_id"], viewer=self.request.user,
            student=_feed_viewer_student(self.request),
        )
        if thread is None:
            raise Http404
        # The post must belong to the class in the URL, not just to a class
        # this reader may see.
        if thread.post.post.school_class_id != school_class.pk:
            raise Http404
        from .reply_starters import starters_for_role

        ctx.update(
            {
                "school_class": school_class,
                "thread": thread,
                "mention_names": (
                    feed_selectors.mention_picker_for(school_class)
                    if thread.can_reply
                    else ()
                ),
                "community_on": True,
                "is_moderator": feed_selectors.is_moderator(self.request.user),
                "is_parent": self.request.user.role == "parent",
                "emoji_choices": ClassPostReaction.Emoji.choices,
                "reply_starters": starters_for_role(
                    self.request.user.role, request=self.request
                )
                if thread.can_reply
                else (),
                "replied": self.request.GET.get("replied") == "1",
                "can_post": feed_selectors.can_post(self.request.user, school_class),
            }
        )
        return ctx


class ClassFeedReplyCreateView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """POST-only: add a reply under one post."""

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        from django.urls import reverse

        school_class = self._resolve_class(request, self.kwargs["class_id"])
        post = get_object_or_404(
            ClassPost, pk=self.kwargs["post_id"], school_class=school_class
        )
        try:
            feed_services.create_reply(
                post=post,
                author=request.user,
                body=request.POST.get("body", ""),
            )
            messages.success(request, "Your reply was added.")
            target = reverse(
                "learning:feed_post",
                kwargs={
                    "class_id": school_class.pk,
                    "post_id": post.pk,
                },
            )
            return redirect(target + "?replied=1#replies")
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))

        return redirect(
            "learning:feed_post",
            class_id=school_class.pk,
            post_id=post.pk,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class ClassFeedReplyActionView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """POST-only: best answer or reply removal (teacher)."""

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect

        school_class = self._resolve_class(request, self.kwargs["class_id"])
        reply = get_object_or_404(
            ClassPostReply,
            pk=self.kwargs["reply_id"],
            post__school_class=school_class,
        )
        action = request.POST.get("action", "")

        try:
            if action == "best":
                feed_services.mark_best_answer(
                    reply=reply, actor=request.user, request=request
                )
                messages.success(request, "Best answer saved.")
            elif action == "remove":
                feed_services.remove_reply(
                    reply=reply,
                    removed_by=request.user,
                    reason=request.POST.get("reason", "").strip(),
                    request=request,
                )
                messages.success(request, "Reply removed.")
            else:
                messages.error(request, "Unknown action.")
        except (PermissionDenied, ValueError) as exc:
            messages.error(request, str(exc))

        return redirect(
            "learning:feed_post",
            class_id=school_class.pk,
            post_id=reply.post_id,
        )

    def get(self, request, *args, **kwargs):
        raise Http404


class ClassFeedSettingsView(RoleRequiredMixin, LearningEnabledMixin, TemplateView):
    """
    Per-class feed switch for teachers and above.

    Off by default. The page also says plainly when points are off for this
    class, so a teacher is not left wondering why questions earn nothing.
    """

    allowed_roles = ["admin", "director", "teacher"]
    template_name = "features/learning/class_feed_settings.html"

    def _get_class(self, request):
        from apps.academics.models import SchoolClass

        school_class = get_object_or_404(SchoolClass, pk=self.kwargs["class_id"])
        if not feed_selectors.can_view_feed(request.user, school_class):
            raise Http404
        return school_class

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        school_class = self._get_class(self.request)
        row, _ = ClassFeedSettings.objects.get_or_create(
            school_class=school_class, defaults={"is_active": False}
        )
        school_switch_on = feature_enabled("feed_community", request=self.request)
        engagement_school_on = feature_enabled(
            "engagement", request=self.request
        )
        try:
            engagement_class_on = school_class.engagement_settings.is_active
        except ClassEngagementSettings.DoesNotExist:
            engagement_class_on = False
        ctx.update(
            {
                "school_class": school_class,
                "feed_settings": row,
                "school_switch_on": school_switch_on,
                "points_on": engagement_school_on and engagement_class_on,
                "school_media_on": feature_enabled(
                    "feed_media", request=self.request
                ),
                "school_student_uploads_on": feature_enabled(
                    "feed_student_uploads", request=self.request
                ),
                "quota": feed_media_services.quota_state(school_class),
            }
        )
        return ctx

    def post(self, request, *args, **kwargs):
        from django.shortcuts import redirect

        school_class = self._get_class(request)
        row, _ = ClassFeedSettings.objects.get_or_create(
            school_class=school_class, defaults={"is_active": False}
        )
        row.is_active = request.POST.get("is_active") == "1"
        row.polls_enabled = request.POST.get("polls_enabled") == "1"
        row.media_enabled = request.POST.get("media_enabled") == "1"
        row.student_uploads_enabled = (
            request.POST.get("student_uploads_enabled") == "1"
        )
        row.full_clean()
        row.save(
            update_fields=[
                "is_active",
                "polls_enabled",
                "media_enabled",
                "student_uploads_enabled",
                "updated_at",
            ]
        )
        try:
            from apps.core.audit_log import log_action

            log_action(
                actor=request.user,
                action="feed.settings.updated",
                content_type="learning.ClassFeedSettings",
                object_id=row.pk,
                change_summary={
                    "class_id": school_class.pk,
                    "is_active": row.is_active,
                    "polls_enabled": row.polls_enabled,
                    "media_enabled": row.media_enabled,
                    "student_uploads_enabled": row.student_uploads_enabled,
                },
                request=request,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Audit log write failed for feed settings %s", row.pk
            )
        messages.success(
            request,
            f"Class community feed switched {'on' if row.is_active else 'off'} "
            f"for {school_class.name}.",
        )
        return redirect("learning:feed_settings", class_id=school_class.pk)


class ClassFeedAttachmentFileView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """
    Serve one feed photo or file after checking the reader belongs to the class,
    can see the post, and (for a pupil's upload) that a teacher approved it.

    Feed media never has a public URL: it lives outside MEDIA_ROOT and this is
    the only way in. When the media switch is off, only admin/director can
    still retrieve an item (the audit copy); everyone else gets a 404.
    """

    allowed_roles = ["admin", "director", "teacher", "student", "parent"]

    def get(self, request, class_id, post_id, attachment_id, *args, **kwargs):
        import mimetypes

        from django.http import FileResponse

        from . import feed_selectors as selectors_mod

        school_class = self._resolve_class(request, class_id)
        threaded = selectors_mod.post_detail(post_id=post_id, viewer=request.user)
        if threaded is None or threaded.post.school_class_id != school_class.pk:
            raise Http404
        post = threaded.post
        attachment = get_object_or_404(
            ClassPostAttachment, pk=attachment_id, post=post
        )

        role = getattr(request.user, "role", "")
        if not feed_media_services.media_enabled_for(school_class) and role not in (
            "admin",
            "director",
        ):
            raise Http404
        if not feed_media_services.attachment_visible_to(attachment, request.user):
            raise Http404

        want_thumb = request.GET.get("thumb") == "1" and bool(attachment.thumbnail)
        field = attachment.thumbnail if want_thumb else attachment.file
        try:
            handle = field.open("rb")
        except FileNotFoundError:
            raise Http404("File not found in storage.")

        if want_thumb:
            content_type = "image/jpeg"
        else:
            content_type = (
                attachment.content_type
                or mimetypes.guess_type(attachment.file.name)[0]
                or "application/octet-stream"
            )
        inline_ok = attachment.is_image and content_type.startswith("image/")
        as_attachment = request.GET.get("download") == "1" or not inline_ok

        base_name = attachment.original_name or attachment.file.name.rsplit("/", 1)[-1]
        if want_thumb:
            base_name = f"{base_name.rsplit('.', 1)[0]}-thumb.jpg"

        response = FileResponse(
            handle,
            as_attachment=as_attachment,
            filename=base_name,
            content_type=content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, max-age=300"
        return response


class ClassFeedAttachmentActionView(
    _FeedClassMixin, RoleRequiredMixin, LearningEnabledMixin, TemplateView
):
    """Teacher approval and removal of one feed photo or file."""

    allowed_roles = ["admin", "director", "teacher"]

    def get(self, request, *args, **kwargs):
        raise Http404

    def post(self, request, class_id, post_id, attachment_id, *args, **kwargs):
        from django.shortcuts import redirect
        from django.urls import reverse
        from django.utils.http import url_has_allowed_host_and_scheme

        school_class = self._resolve_class(request, class_id)
        post = get_object_or_404(
            ClassPost, pk=post_id, school_class=school_class
        )
        attachment = get_object_or_404(
            ClassPostAttachment, pk=attachment_id, post=post
        )
        action = request.POST.get("action")
        try:
            if action == "approve":
                feed_media_services.approve_attachment(
                    attachment=attachment, actor=request.user, request=request
                )
                messages.success(
                    request, "Approved — pupils and parents can see it now."
                )
            elif action == "remove":
                feed_media_services.remove_attachment(
                    attachment=attachment, actor=request.user, request=request
                )
                messages.success(
                    request,
                    "Removed from the feed. A copy is kept for the school's records.",
                )
            else:
                messages.error(request, "Unknown action.")
        except PermissionDenied as exc:
            messages.error(request, str(exc))

        target = request.POST.get("next") or reverse(
            "learning:feed_post", args=[school_class.pk, post.pk]
        )
        if not url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}
        ):
            target = reverse("learning:class_feed", args=[school_class.pk])
        return redirect(target)


# ---------------------------------------------------------------------------
# Points, levels and badges views — Piece 3
# ---------------------------------------------------------------------------
# All additive.  Nothing above this line is touched.
# Gated behind "engagement" feature flag.
# ---------------------------------------------------------------------------

