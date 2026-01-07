#!/bin/bash
# Hook: Remind orchestrator to delegate work
# Triggers: PreToolCall for Edit, Write, Bash

TOOL_NAME="${CLAUDE_TOOL_NAME:-}"

# Only trigger for implementation tools
case "$TOOL_NAME" in
    Edit|Write|Bash)
        # Check if we're in orchestrator mode (not in a subagent)
        if [[ -z "${CLAUDE_AGENT_NAME:-}" ]]; then
            echo "ORCHESTRATOR REMINDER: You should delegate implementation work to subagents."
            echo "Use Task tool to dispatch work to appropriate supervisor:"
            echo "  - Emilia (infra-supervisor): Docker, CI/CD, deployment"
            echo "  - Luna (runpod-supervisor): RunPod, GPU, ComfyUI workflows"
            echo ""
            echo "Only use Edit/Write/Bash directly for:"
            echo "  - Quick fixes requested explicitly by user"
            echo "  - Configuration updates"
            echo "  - Emergency hotfixes"
        fi
        ;;
esac

exit 0
