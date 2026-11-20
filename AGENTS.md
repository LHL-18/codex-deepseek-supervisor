# Development and publication instructions

- Keep normal tasks in Codex independent mode. Delegate to DeepSeek only for an
  explicitly requested, named task or an active session covering that same task.
- The reusable skill is `skills/deepseek-supervisor/SKILL.md`. Read it when the
  user invokes `$deepseek-supervisor` or requests supervised DeepSeek work.
- This repository contains development tooling, not the source or data of an
  application that previously used it. Keep examples independent of that project.
- Keys belong in `.env.deepseek.local` or environment variables. Never commit
  credential files, real call history, session records or prompt/source traces.
- The optional dashboard is local only. It is not required for skill execution.
- Run offline verification with
  `python -m unittest discover -s tools -p 'test_*.py' -v` and
  `node --check tools/collaboration_dashboard.js` when JavaScript changes.
- Do not submit a paid API request merely to validate packaging or documentation.
- A successful API response is not acceptance. Review proposed changes and run
  appropriate checks before marking work complete. Close the task mode afterward.
