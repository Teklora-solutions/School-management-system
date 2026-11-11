"""apps/learning/urls.py"""

from django.urls import path

from . import live_class_views, views

app_name = "learning"

urlpatterns = [
    # ── Existing L0/L1 classrooms (unchanged) ─────────────────────────────
    path("teaching/", views.TeacherClassroomsView.as_view(), name="teacher_classrooms"),
    path(
        "teaching/<int:class_id>/<int:subject_id>/",
        views.TeacherClassroomDetailView.as_view(),
        name="teacher_classroom",
    ),
    path("", views.StudentClassroomsView.as_view(), name="classrooms"),
    path(
        "<int:class_id>/<int:subject_id>/",
        views.StudentClassroomDetailView.as_view(),
        name="classroom",
    ),

    # ── Piece 1: progress and streaks ──────────────────────────────────────
    path(
        "progress/",
        views.StudentProgressView.as_view(),
        name="student_progress",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/progress/",
        views.TeacherClassProgressView.as_view(),
        name="teacher_class_progress",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/engagement/",
        views.ClassEngagementSettingsView.as_view(),
        name="class_engagement_settings",
    ),
    path(
        "lesson/<int:lesson_id>/complete/",
        views.MarkLessonCompleteView.as_view(),
        name="mark_lesson_complete",
    ),
    path(
        "director/engagement/",
        views.DirectorEngagementOverviewView.as_view(),
        name="director_engagement",
    ),

    # ── Piece 2: class community feed ─────────────────────────────────────
    path("feed/", views.FeedHomeView.as_view(), name="feed_home"),
    path(
        "class/<int:class_id>/feed/",
        views.ClassFeedView.as_view(),
        name="class_feed",
    ),
    path(
        "class/<int:class_id>/feed/items/",
        views.ClassFeedItemsView.as_view(),
        name="feed_items",
    ),
    path(
        "class/<int:class_id>/feed/poll/",
        views.ClassFeedPollView.as_view(),
        name="feed_poll",
    ),
    path(
        "class/<int:class_id>/feed/settings/",
        views.ClassFeedSettingsView.as_view(),
        name="feed_settings",
    ),
    path(
        "class/<int:class_id>/feed/post/<int:post_id>/",
        views.ClassFeedThreadView.as_view(),
        name="feed_post",
    ),
    path(
        "class/<int:class_id>/feed/post/<int:post_id>/reply/",
        views.ClassFeedReplyCreateView.as_view(),
        name="feed_reply_create",
    ),
    path(
        "class/<int:class_id>/feed/reply/<int:reply_id>/action/",
        views.ClassFeedReplyActionView.as_view(),
        name="feed_reply_action",
    ),
    path(
        "class/<int:class_id>/feed/post/<int:post_id>/action/",
        views.ClassFeedPostActionView.as_view(),
        name="feed_post_action",
    ),
    # ── Piece 3: points, levels and badges ────────────────────────────────
    path(
        "my-badges/",
        views.StudentBadgesView.as_view(),
        name="student_badges",
    ),
    path(
        "teaching/class/<int:class_id>/points/",
        views.TeacherClassPointsView.as_view(),
        name="teacher_class_points",
    ),
    path(
        "teaching/class/<int:class_id>/student/<int:student_id>/ledger/",
        views.TeacherStudentLedgerView.as_view(),
        name="teacher_student_ledger",
    ),
    path(
        "teaching/class/<int:class_id>/student/<int:student_id>/bonus/",
        views.TeacherBonusAwardView.as_view(),
        name="teacher_bonus_award",
    ),
    path(
        "teaching/class/<int:class_id>/ledger/<int:ledger_id>/reverse/",
        views.TeacherReverseAwardView.as_view(),
        name="teacher_reverse_award",
    ),
    path(
        "director/points/",
        views.DirectorPointsOverviewView.as_view(),
        name="director_points",
    ),

    # ── Checkpoint A: course builder ──────────────────────────────────────

    # Student lesson view (stub — full experience in Checkpoint B)
    path(
        "<int:class_id>/<int:subject_id>/lesson/<int:lesson_id>/",
        views.StudentLessonView.as_view(),
        name="lesson_view",
    ),
    # Modules
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/add/",
        views.ModuleCreateView.as_view(),
        name="module_create",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/edit/",
        views.ModuleEditView.as_view(),
        name="module_edit",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/delete/",
        views.ModuleDeleteView.as_view(),
        name="module_delete",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/move/<str:direction>/",
        views.ModuleMoveView.as_view(),
        name="module_move",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/duplicate/",
        views.ModuleDuplicateView.as_view(),
        name="module_duplicate",
    ),

    # Lessons
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/add/",
        views.LessonCreateView.as_view(),
        name="lesson_create",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/edit/",
        views.LessonEditView.as_view(),
        name="lesson_edit",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/delete/",
        views.LessonDeleteView.as_view(),
        name="lesson_delete",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/move/<str:direction>/",
        views.LessonMoveView.as_view(),
        name="lesson_move",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/duplicate/",
        views.LessonDuplicateView.as_view(),
        name="lesson_duplicate",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/preview/",
        views.LessonPreviewView.as_view(),
        name="lesson_preview",
    ),

    # Attachments
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/attachments/<int:attachment_id>/delete/",
        views.AttachmentDeleteView.as_view(),
        name="attachment_delete",
    ),
    path(
        "lesson/<int:lesson_id>/attachments/<int:attachment_id>/",
        views.LessonAttachmentFileView.as_view(),
        name="lesson_attachment_file",
    ),

    # Copy course
    path(
        "teaching/<int:class_id>/<int:subject_id>/copy/",
        views.CopyCourseView.as_view(),
        name="copy_course",
    ),

    # ── Checkpoint B: lesson notes and search ─────────────────────────────
    path(
        "<int:class_id>/<int:subject_id>/lesson/<int:lesson_id>/note/",
        views.SaveLessonNoteView.as_view(),
        name="save_lesson_note",
    ),
    path(
        "<int:class_id>/<int:subject_id>/search/",
        views.CourseSearchView.as_view(),
        name="course_search",
    ),

    # ── Checkpoint C: quizzes ─────────────────────────────────────────────

    # Teacher quiz builder
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/",
        views.TeacherQuizDetailView.as_view(),
        name="teacher_quiz",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/create/",
        views.TeacherQuizCreateView.as_view(),
        name="teacher_quiz_create",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/settings/",
        views.TeacherQuizSettingsView.as_view(),
        name="teacher_quiz_settings",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/<str:action>/",
        views.TeacherQuizPublishView.as_view(),
        name="teacher_quiz_action",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/questions/add/",
        views.TeacherAddQuestionView.as_view(),
        name="teacher_add_question",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/questions/<int:question_id>/edit/",
        views.TeacherEditQuestionView.as_view(),
        name="teacher_edit_question",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/questions/<int:question_id>/delete/",
        views.TeacherDeleteQuestionView.as_view(),
        name="teacher_delete_question",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/questions/<int:question_id>/move/<str:direction>/",
        views.TeacherMoveQuestionView.as_view(),
        name="teacher_move_question",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/modules/<int:module_id>/lessons/<int:lesson_id>/quiz/results/",
        views.TeacherQuizResultsView.as_view(),
        name="teacher_quiz_results",
    ),

    # Student quiz
    path(
        "<int:class_id>/<int:subject_id>/quiz/<int:lesson_id>/",
        views.StudentQuizLandingView.as_view(),
        name="quiz_landing",
    ),
    path(
        "<int:class_id>/<int:subject_id>/quiz/<int:lesson_id>/take/",
        views.StudentTakeQuizView.as_view(),
        name="quiz_take",
    ),
    path(
        "<int:class_id>/<int:subject_id>/quiz/<int:lesson_id>/save-answer/",
        views.SaveQuizAnswerView.as_view(),
        name="quiz_save_answer",
    ),
    path(
        "<int:class_id>/<int:subject_id>/quiz/<int:lesson_id>/results/<int:attempt_id>/",
        views.StudentQuizResultsView.as_view(),
        name="quiz_results",
    ),

    # ── Checkpoint D: Live Classes & Calendar ─────────────────────────────
    path(
        "teaching/<int:class_id>/<int:subject_id>/live/",
        live_class_views.TeacherLiveClassListView.as_view(),
        name="teacher_live_sessions",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/live/create/",
        live_class_views.TeacherLiveClassCreateView.as_view(),
        name="teacher_live_create",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/live/<int:session_id>/edit/",
        live_class_views.TeacherLiveClassEditView.as_view(),
        name="teacher_live_edit",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/live/<int:session_id>/cancel/",
        live_class_views.TeacherLiveClassCancelView.as_view(),
        name="teacher_live_cancel",
    ),
    path(
        "teaching/<int:class_id>/<int:subject_id>/live/<int:session_id>/<str:action>/",
        live_class_views.TeacherLiveClassActionView.as_view(),
        name="teacher_live_action",
    ),
    path(
        "live/<int:session_id>/",
        live_class_views.LiveClassDetailView.as_view(),
        name="live_session_detail",
    ),
    path(
        "<int:class_id>/<int:subject_id>/live/",
        live_class_views.StudentLiveClassListView.as_view(),
        name="student_live_sessions",
    ),
    path(
        "live/<int:session_id>/join/",
        live_class_views.StudentJoinLiveClassView.as_view(),
        name="student_live_join",
    ),
    path(
        "class/<int:class_id>/calendar/",
        live_class_views.ClassCalendarView.as_view(),
        name="class_calendar",
    ),
]
