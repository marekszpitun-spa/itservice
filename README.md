# IT Ticket Auto-Router

A Claude Code Cloud Routine that assigns unassigned IT Jira tickets to team members based
on skills and configurable target percentages. Runs hourly on Anthropic's cloud (works
when your laptop is closed); acts only during the configured business-hours window.

## Files
- `routing-config.yaml` — the control panel. Edit this to change percentages, skills,
  team membership, or to pause (kill-switch). Commit your change; it takes effect next run.
- `CLAUDE.md` — the agent's durable logic and guardrails. Rarely needs editing.
- `routine-prompt.md` — the prompt pasted into the routine's Instructions box.

## To finish setup
1. Set `jira.project_key` to your real IT project key.
2. Replace each `REPLACE_ME_*` with the member's Atlassian accountId.
3. Confirm skills keywords and target percentages (should sum to 100).
4. Optionally set `slack.channel` for a per-run summary.

## How to get Atlassian accountIds (easiest first)
- **Ask Claude (with the Atlassian connector on):** "Look up the Atlassian accountId for
  these emails: …" — it can resolve them for you directly.
- **From a profile URL:** open the person's Jira profile; the accountId is in the URL
  (e.g. `.../jira/people/5b10a2…`).
- **Admin console:** Atlassian admin → User management → the user's details.
