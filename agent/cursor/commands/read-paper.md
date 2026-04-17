# Read Paper

Help me read a research paper with a staged workflow.

Primary goal:
- Discuss exactly one topic per invocation.
- When I invoke this command again in the same chat, continue to the next unfinished topic instead of restarting.
- Ground the discussion in the actual paper and, when relevant, the accompanying code.

Workflow order:

1. Problem and research gap
   - What problem does the paper solve?
   - What limitation in prior work is claimed?
   - Is the gap real, important, and supported by evidence?

2. Method and why it should work
   - What is the core idea?
   - What are the main modules, steps, or design choices?
   - Why should this method work in principle?

3. Experimental setup and code-grounded verification
   - What datasets, metrics, baselines, and training/evaluation settings are used?
   - Cross-check the paper's claims with the available code, configs, and scripts when possible.
   - Point out missing details, hidden assumptions, or reproducibility risks.

4. Critical analysis
   - Are the theory, assumptions, and experiments robust?
   - What are the limitations, failure modes, and engineering trade-offs?
   - Is the method likely to be reliable in practice?

5. Final synthesis
   - Only do this after the four topics above are finished, or if I explicitly ask for a full summary.
   - Summarize the paper's real contribution, evidence quality, and practical value.

Behavior rules:

1. If no paper is specified, ask which paper, PDF, note, or codebase you should read.
2. If this is the first invocation for a paper, start with topic 1 only.
3. If earlier topics were already discussed in this chat, infer the next unfinished topic from the conversation and continue there.
4. If the conversation history is not enough to determine the next topic, ask me which topic to continue from.
5. Do not cover more than one topic per invocation unless I explicitly request a full pass or summary.
6. Keep each response focused and concise. Use this structure:
   - Current topic
   - Key conclusions
   - Evidence from the paper/code
   - Open questions or concerns
   - Next topic for the next invocation
7. Be critical but fair. Do not merely restate the paper; evaluate whether the claims are actually supported.
8. When discussing experiments, prefer checking code/configs/scripts if they exist instead of relying only on the PDF text.
9. If information is missing, say so clearly instead of guessing.
10. Respond in the same language I use unless I ask for another language.
