#!/bin/bash
# Hook: Validate task dispatch includes required information
# Triggers: PreToolCall for Task

TOOL_NAME="${CLAUDE_TOOL_NAME:-}"

if [[ "$TOOL_NAME" != "Task" ]]; then
    exit 0
fi

# Reminder about proper task dispatch
echo "TASK DISPATCH CHECKLIST:"
echo "  [ ] Task has clear description"
echo "  [ ] Subagent type is specified"
echo "  [ ] Prompt includes context needed"
echo "  [ ] Kanban task ID referenced if applicable"
echo ""

exit 0
