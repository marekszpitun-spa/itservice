# IT Ticket Auto-Router

A Claude Code Cloud Routine that assigns unassigned IT Jira tickets to team members based
on priority, dedicated keyword categories, skills, and configurable target percentages.
Runs once per working day at 11:00 (Europe/Berlin) on Anthropic's cloud — works when your
laptop is closed.

## Files
- `routing-config.yaml` — the control panel. Edit this to change percentages, skills,
  routing rules, team membership, or to pause (kill-switch). Commit; it takes effect next run.
- `CLAUDE.md` — the agent's durable logic and guardrails. Rarely needs editing.
- `routine-prompt.md` — the prompt pasted into the routine's Instructions box.

## How a ticket is routed
Per ticket, the agent evaluates rules in this order:
1. **Priority routing** — if the ticket's priority is listed in `priority_routing`, it
   goes straight to the named owner (bypasses keywords + skills).
2. **Keyword routing** — if the ticket text contains any keyword listed in
   `keyword_routing`, it goes straight to that named specialist. These members have no
   `target_pct` and aren't part of the load-balanced pool — they own their category
   outright (e.g. Bartosz Tomaszewski gets all network/Wi-Fi/hosting/"odwijka" tickets).
3. **Skill match** — among the full `team`, those whose `skills` keywords appear in
   the ticket text.
4. **Selection** — `load_balanced` picks whoever is furthest below their target %.

## To finish / verify setup
1. `jira.project_key` and `unassigned_jql` use your real project (`SPAITSM`); confirm the
   **status names** (Cancelled/Resolved/Done) match your workflow exactly.
2. `priority_routing` key (`Critical`) matches your real Jira priority scheme.
3. Target percentages in `team` sum to 100 (`keyword_routing` members are excluded from
   this — they take no percentage).
4. Slack connector attached to the routine and `slack.channel` is correct.

## How to get Atlassian accountIds (easiest first)
- **Ask Claude (with the Atlassian connector on):** e.g. "Look up the accountId for these
  emails: …" — it resolves directly.
- **From a profile URL:** the accountId is in the Jira profile URL.
- **Admin console:** Atlassian admin → User management.
