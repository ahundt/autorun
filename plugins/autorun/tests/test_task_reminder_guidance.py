#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""What a task reminder asks the model to do, per harness.

Two rules the reminders must satisfy:

1. Ask for a fine-grained breakdown. "Create tasks" produces one broad task
   that tracks nothing; the reminder has to say one task per concrete step.
2. Name a dependency parameter only where the harness's own task tools accept
   one. This is the rule `Platform.native_task_statuses` already applies to
   status values: guidance that names a parameter the harness rejects turns
   into a hard validation error for the model instead of the action autorun
   offered it.
"""

import pytest

from autorun import plugins
from autorun.core import EventContext, ThreadSafeDB, format_suggestion
from autorun.platforms import PLATFORMS

HOOK_PLATFORMS = ["claude", "gemini", "qwen", "antigravity", "codex"]

# Claude Code's TaskUpdate declares addBlocks and addBlockedBy. No other
# harness autorun targets documents a dependency parameter on its task tools:
# Codex's update_plan is a flat checklist, and the Gemini family's task tools
# take a title and a status.
DEPENDENCY_CAPABLE = {"claude"}


def _reminder(cli_type, **kwargs):
    ctx = EventContext(
        session_id=f"reminder-{cli_type}",
        event="PostToolUse",
        cli_type=cli_type,
        store=ThreadSafeDB(),
    )
    return format_suggestion(plugins._task_staleness_notification(ctx, 50, **kwargs), cli_type)


class TestRemindersAskForFineGrainedTasks:
    @pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
    def test_update_reminder_asks_for_one_task_per_step(self, cli_type):
        assert "step" in _reminder(cli_type).lower()

    @pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
    def test_no_tasks_reminder_rejects_one_broad_task(self, cli_type):
        text = _reminder(cli_type, no_tasks=True).lower()
        assert "step" in text
        assert "concrete" in text or "not one broad task" in text


class TestRemindersNameOnlySupportedDependencySyntax:
    @pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
    def test_platform_declares_dependency_support_explicitly(self, cli_type):
        syntax = PLATFORMS[cli_type].task_dependency_syntax
        assert bool(syntax) is (cli_type in DEPENDENCY_CAPABLE)

    def test_claude_reminder_wires_dependencies_with_its_real_tool_name(self):
        text = _reminder("claude")
        assert "addBlockedBy" in text
        assert "TaskUpdate" in text
        assert "{task_update}" not in text  # placeholder resolved, not leaked

    @pytest.mark.parametrize("cli_type", sorted(set(HOOK_PLATFORMS) - DEPENDENCY_CAPABLE))
    @pytest.mark.parametrize("kwargs", [{}, {"overdue": True}, {"no_tasks": True}])
    def test_other_harnesses_are_never_told_to_wire_dependencies(self, cli_type, kwargs):
        text = _reminder(cli_type, **kwargs).lower()
        assert "blockedby" not in text
        assert "addblocks" not in text

    @pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
    def test_reminders_leave_no_unresolved_placeholder(self, cli_type):
        for kwargs in ({}, {"overdue": True}, {"no_tasks": True}):
            text = _reminder(cli_type, **kwargs)
            assert "{" not in text, f"{cli_type} reminder leaked a placeholder: {text!r}"


class TestDenyGuidanceFollowsTheSameRule:
    def _instructions(self, cli_type):
        ctx = EventContext(
            session_id=f"deny-{cli_type}",
            event="PostToolUse",
            cli_type=cli_type,
            store=ThreadSafeDB(),
        )
        return format_suggestion(plugins._task_staleness_instructions(ctx), cli_type)

    def test_claude_deny_guidance_wires_dependencies(self):
        assert "addBlockedBy" in self._instructions("claude")

    @pytest.mark.parametrize("cli_type", sorted(set(HOOK_PLATFORMS) - DEPENDENCY_CAPABLE))
    def test_other_harnesses_get_no_dependency_instruction(self, cli_type):
        assert "blockedby" not in self._instructions(cli_type).lower()
