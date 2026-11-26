"""
apps/assistant/models.py
------------------------
The assistant's own tables. Everything here is additive to the ``assistant``
app; no existing app is modified.

Configuration
  AssistantSettings   one row (pk=1): switches, providers, rules, budgets,
                      limits, the safeguarding contact and the consent gate.
  ProviderConfig      one row per provider: models, encrypted key, prices,
                      fallback order and the data-region note.

Ledger and safety
  UsageEvent          one row per gateway attempt: tokens, cost, latency,
                      outcome. Never contains a key or message text.
  SafeguardingAlert   one row per safeguarding hit. Acknowledged by an admin
                      or director; escalated when overdue. Never deleted
                      automatically.
  CustomGuardrailRule rules an admin ADDS. The mandatory pack lives in code
                      (``guardrails/rules.py``) and has no database rows, so it
                      cannot be edited or removed from any screen.
  AssistantJobFailure assistant background jobs that failed, with the reason
                      and attempt count. Existing jobs keep their behaviour.

Knowledge base (Module 2)
  KnowledgeCollection one shelf of documents (Admissions, Fees, …) with the
                      broadest audience its documents may have.
  KnowledgeDocument   one source document: a private file, a URL, pasted text
                      or a set of Q&A pairs. Only Approved documents in
                      Approved collections are ever retrievable.
  KnowledgeDocumentVersion  a snapshot taken when the title or source changes.
  KnowledgeQAPair     one question and its approved answer inside a FAQ
                      document.
  KnowledgeChunk      one readable passage of an Approved document, with its
                      embedding. Replaced as a set; never edited in place.
  KnowledgeIngestRun  one reading attempt of a document: what happened, how
                      many chunks and tokens, and the plain reason if it failed.
  KnowledgeGap        a question the assistant could not answer reliably, kept
                      in masked form so the school can add the missing wording.

Public assistant and handoff (Module 3)
  HandoffTeam         a group of staff a conversation can be routed to.
  HandoffRule         an admin-defined routing rule: triggers, priority,
                      destination, working hours, out-of-hours wording and SLA.
                      Rules marked required (the safeguarding route) cannot be
                      deleted or switched off.
  SuggestedQuestion   an admin-editable opening suggestion in the widget.
  ChatConversation    one visitor's conversation, keyed by a hash of the signed
                      visitor cookie. No raw IP is stored here.
  ChatMessage         one turn: what the visitor wrote, what was sent to the
                      provider (masked), citations and usage.
  AnswerFeedback      helpful / not helpful, or a problem report, for one answer.
  CallbackRequest     optional name and phone/email left for a return call.
  Handoff             a conversation passed to a person or team: priority,
                      status, assignee, SLA deadline and a frozen packet.
  HandoffNote         an internal note on a handoff. Never shown to the visitor.
  CannedReply         an admin-editable reply staff can insert.
  HandoffEvent        an audit trail of handoff actions; ``dedupe_key`` stops a
                      reminder or alert being sent twice.
"""

from __future__ import annotations

import datetime
import re

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import BaseModel

from ..knowledge.roles import roles_key
from ..knowledge.storage import knowledge_storage, knowledge_upload_path
from ..knowledge.validators import validate_knowledge_file

# Editable defaults for the public widget. These are a friendly opening, not the
# consent wording: consent is read through ``consent.visible_consent()`` only.
DEFAULT_WIDGET_OPENING_EN = (
    "Hello. I'm the school's AI assistant. I answer from the school's approved "
    "information. I'm not a person — you can ask to talk to one at any time."
)
DEFAULT_WIDGET_OPENING_SW = (
    "Habari. Mimi ni msaidizi wa AI wa shule. Najibu kutoka taarifa zilizoidhinishwa "
    "na shule. Mimi si mtu — unaweza kuomba kuzungumza na mtu wakati wowote."
)

# Whether the public site should render the widget at all. Public pages are read
# by everyone, so the answer is cached for a minute and cleared whenever these
# settings are saved (``AssistantSettings.save``).
WIDGET_GATE_CACHE_KEY = "assistant:widget_gate"
WIDGET_GATE_SECONDS = 60


