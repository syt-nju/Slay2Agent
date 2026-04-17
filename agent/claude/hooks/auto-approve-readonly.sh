#!/bin/bash
# Auto-approve all read-only permission requests

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""')

# Read-only tools: always approve
READONLY_TOOLS=("Read" "Glob" "Grep" "WebFetch" "WebSearch" "LS" "NotebookRead")

for tool in "${READONLY_TOOLS[@]}"; do
  if [[ "$TOOL_NAME" == "$tool" ]]; then
    jq -n '{
      hookSpecificOutput: {
        hookEventName: "PermissionRequest",
        permissionDecision: "allow",
        permissionDecisionReason: "Auto-approved: read-only tool"
      }
    }'
    exit 0
  fi
done

# For Bash tool: approve only if the command is read-only
if [[ "$TOOL_NAME" == "Bash" ]]; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

  # Read-only bash command patterns
  READONLY_PATTERNS=(
    "^ls"
    "^cat "
    "^head "
    "^tail "
    "^find "
    "^grep "
    "^rg "
    "^echo "
    "^pwd"
    "^which "
    "^type "
    "^file "
    "^wc "
    "^stat "
    "^du "
    "^df "
    "^env "
    "^printenv "
    "^uname"
    "^whoami"
    "^id "
    "^date"
    "^git (log|status|diff|show|branch|remote|tag|describe|rev-parse|ls-files|shortlog|stash list)"
    "^jq "
    "^python.* -c ['\"]?print"
    "^uv (run --help|pip list|pip show)"
  )

  for pattern in "${READONLY_PATTERNS[@]}"; do
    if echo "$COMMAND" | grep -qE "$pattern"; then
      jq -n '{
        hookSpecificOutput: {
          hookEventName: "PermissionRequest",
          permissionDecision: "allow",
          permissionDecisionReason: "Auto-approved: read-only bash command"
        }
      }'
      exit 0
    fi
  done
fi

# Otherwise: let Claude Code handle the permission dialog normally
exit 0
