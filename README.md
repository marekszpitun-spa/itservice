# IT Ticket Auto-Router

A Claude Code Cloud Routine that assigns unassigned IT Jira tickets to team members based
on priority, region, skills, and configurable target percentages. Runs once per working
day at 11:00 (Europe/Berlin) on Anthropic's cloud — works when your laptop is closed.

## Files
- `routing-config.yaml` — the control panel. Edit this to change percentages, skills,
  routing rules, team membership, or to pause (kill-switch). Commit; it takes effect next run.
- `CLAUDE.md` — the agent's durable logic and guardrails. Rarely needs editing.
- `routine-prompt.md` — the prompt pasted into the routine's Instructions box.

## How a ticket is routed
Per ticket, the agent evaluates rules in this order:
1. **Priority routing** — if the ticket's priority is listed in `priority_routing`, it
   goes straight to the named owner (bypasses region + skills).
2. **Region gate** — members with a `regions` list only receive tickets from those
   regions; members without one are eligible everywhere. Inactive until `region_field` is set.
3. **Skill match** — among the remaining members, those whose `skills` keywords appear in
   the ticket text.
4. **Selection** — `load_balanced` picks whoever is furthest below their target %.

## To finish / verify setup
1. `jira.project_key` and `unassigned_jql` use your real project (`SPAITSM`); confirm the
   **status names** (Cancelled/Resolved/Done) match your workflow exactly.
2. `priority_routing` key (`Critical`) matches your real Jira priority scheme.
3. `region_field` is set to the field that holds country/site (e.g. `customfield_xxxxx`),
   otherwise region routing stays inactive and Luka is treated as region-agnostic.
4. Target percentages sum to 100.
5. Slack connector attached to the routine and `slack.channel` is correct.

## How to get Atlassian accountIds / field ids (easiest first)
- **Ask Claude (with the Atlassian connector on):** e.g. "What field on SPAITSM holds the
  country/region?" or "Look up the accountId for these emails: …" — it resolves directly.
- **From a profile URL:** the accountId is in the Jira profile URL.
- **Admin console:** Atlassian admin → User management.
