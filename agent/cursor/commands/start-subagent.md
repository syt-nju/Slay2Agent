# Start Subagent

Use Cursor subagents to handle the task I give you in this chat.

Requirements:

1. Briefly restate my task in 1-2 sentences.
2. If the task is ambiguous, ask clarifying questions first.
3. When the task is clear, split it into concrete subtasks and delegate them to appropriate subagents.
4. Prefer running independent subtasks in parallel.
5. Choose the subagent type based on the work:
   - `explore` for codebase exploration, locating files, tracing implementations, and understanding architecture
   - `shell` for terminal commands, tests, builds, and git inspection
   - `browser-use` for browser automation, UI checks, and interaction testing
   - `generalPurpose` for complex research or mixed multi-step work that does not fit the others cleanly
6. Do not just say you will use subagents. Actually invoke them when they are appropriate.
7. After all subagents finish, merge the results into one concise final answer with:
   - outcome
   - important findings
   - risks or open questions
   - suggested next steps if useful

If I did not include a concrete task after invoking this command, ask me what work you should delegate.
