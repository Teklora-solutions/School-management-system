# Hand-written migration for Module 4: signed-in conversations, messages,
# memory and tool call logs.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assistant", "0006_module3_handoff_foundations"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── SignedInConversation ──────────────────────────────────────────────
        migrations.CreateModel(
            name="SignedInConversation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(
                    choices=[("open", "Open"), ("closed", "Closed")],
                    default="open", max_length=10,
                )),
                ("language", models.CharField(default="en", max_length=8)),
                ("role_snapshot", models.CharField(blank=True, max_length=20)),
                ("class_ids_snapshot", models.CharField(blank=True, max_length=200)),
                ("active_child_id", models.IntegerField(blank=True, null=True)),
                ("message_count", models.PositiveIntegerField(default=0)),
                ("last_activity_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("is_test", models.BooleanField(default=False)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="assistant_signed_in_conversations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"db_table": "assistant_signed_in_conversation", "ordering": ["-last_activity_at"]},
        ),
        migrations.AddIndex(
            model_name="signedinconversation",
            index=models.Index(fields=["user", "-last_activity_at"], name="asst_sinconv_user_act"),
        ),
        migrations.AddIndex(
            model_name="signedinconversation",
            index=models.Index(fields=["status", "-last_activity_at"], name="asst_sinconv_status_act"),
        ),

        # ── SignedInMessage ───────────────────────────────────────────────────
        migrations.CreateModel(
            name="SignedInMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("role", models.CharField(
                    choices=[("user", "User"), ("assistant", "Assistant"), ("system", "System notice")],
                    max_length=12,
                )),
                ("body", models.TextField()),
                ("body_masked", models.TextField(blank=True)),
                ("language", models.CharField(blank=True, max_length=8)),
                ("citations", models.JSONField(blank=True, default=list)),
                ("tool_summary", models.JSONField(blank=True, default=list)),
                ("model_name", models.CharField(blank=True, max_length=120)),
                ("provider_label", models.CharField(blank=True, max_length=100)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("tokens_in", models.PositiveIntegerField(default=0)),
                ("tokens_out", models.PositiveIntegerField(default=0)),
                ("blocked_reason", models.CharField(blank=True, max_length=120)),
                ("is_streamed", models.BooleanField(default=False)),
                ("conversation", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="messages",
                    to="assistant.signedinconversation",
                )),
            ],
            options={"db_table": "assistant_signed_in_message", "ordering": ["created_at", "pk"]},
        ),
        migrations.AddIndex(
            model_name="signedinmessage",
            index=models.Index(fields=["conversation", "created_at"], name="asst_sinmsg_conv_at"),
        ),

        # ── AssistantMemory ───────────────────────────────────────────────────
        migrations.CreateModel(
            name="AssistantMemory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("opted_in", models.BooleanField(default=False)),
                ("prefer_language", models.CharField(blank=True, max_length=8)),
                ("prefer_tone", models.CharField(blank=True, max_length=20)),
                ("recurring_summary", models.CharField(blank=True, max_length=200)),
                ("opted_in_at", models.DateTimeField(blank=True, null=True)),
                ("last_updated_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="assistant_memory",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"db_table": "assistant_memory"},
        ),

        # ── ToolCallLog ───────────────────────────────────────────────────────
        migrations.CreateModel(
            name="ToolCallLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tool_name", models.CharField(db_index=True, max_length=80)),
                ("outcome", models.CharField(
                    choices=[("ok", "Success"), ("denied", "Permission denied"), ("invalid", "Invalid arguments"), ("error", "Error")],
                    default="ok", max_length=10,
                )),
                ("fields_returned", models.JSONField(blank=True, default=list)),
                ("reason", models.CharField(blank=True, max_length=200)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("actor", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("conversation", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="tool_calls",
                    to="assistant.signedinconversation",
                )),
            ],
            options={"db_table": "assistant_tool_call_log", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="toolcalllog",
            index=models.Index(fields=["actor", "-created_at"], name="asst_toollog_actor_at"),
        ),
        migrations.AddIndex(
            model_name="toolcalllog",
            index=models.Index(fields=["tool_name", "outcome"], name="asst_toollog_name_out"),
        ),
    ]
