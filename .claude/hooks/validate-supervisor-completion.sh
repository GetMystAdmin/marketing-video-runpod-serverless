#!/bin/bash
# Hook: Remind supervisors to update Kanban on completion
# Triggers: PostToolCall for Task (when supervisor returns)

echo "SUPERVISOR COMPLETION REMINDER:"
echo "  [ ] Did the supervisor update Kanban task status?"
echo "  [ ] Were all changes reviewed?"
echo "  [ ] Is documentation updated if needed?"
echo ""

exit 0
