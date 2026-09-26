# IT Ticket Auto-Router

A Claude Managed Agent that assigns unassigned IT Jira tickets to team members based
on priority, dedicated keyword categories, skills, and configurable target percentages.
Runs once per working day at 11:00 (Europe/Berlin) — works when your laptop is closed.

All routing is done by `assign.py`, a deterministic script. The agent's only judgment
call is reading Slack statuses to decide who is out today; it passes that list to the
script and never touches Jira itself.

## Files
- `routing-config.yaml` — the control panel. Edit this to change percentages, skills,
  routing rules, team membership, or to pause (kill-switch). Commit; it takes effect next run.
- `assign.py` — the router. Reads the config, finds tickets, assigns, and posts the
  Slack summary. Run `python3 assign.py --help` for options.
- `AGENT.md` — the agent's instructions and guardrails. Rarely needs editing.
- `requirements.txt` — Python dependencies (PyYAML).
- `legacy/` — the old connector-based routine (`CLAUDE.routine.md`, `routine-prompt.md`),
  kept for reference only.

## How a ticket is routed
Per ticket, the router evaluates rules in this order:
1. **Priority routing** — if the ticket's priority is listed in `priority_routing`, it
   goes straight to the named owner (bypasses keywords + skills).
2. **Keyword routing** — if the ticket text contains any keyword listed in
   `keyword_routing`, it goes straight to that named specialist. These members have no
   `target_pct` and aren't part of the load-balanced pool — they own their category
   outright (e.g. Bartosz Tomaszewski gets all network/Wi-Fi/hosting/"odwijka" tickets).
3. **Skill match** — among the full `team`, those whose `skills` keywords appear in
   the ticket text.
4. **Selection** — `load_balanced` picks whoever is furthest below their target %.

At every stage, anyone the agent judged out today from their Slack status (OOO, vacation,
travel, sick, etc.) is excluded from that ticket's eligible set — see `availability_check`
in the config. Set it to `false` to disable this if Slack lookups are misbehaving.

Safety rails in `assign.py`: `dry_run` (anything but an explicit `false` writes nothing),
assignee-only updates, a re-check that skips closed or already-assigned tickets, and an
alert if a ticket's status changes after assignment (external Jira automation).
`shadow_compare` lists tickets the other `match_mode` would have routed differently.

## Environment
- `JIRA_API_TOKEN` — scoped token for the `svc-itsm-router` service account (Bearer).
- `SLACK_BOT_TOKEN` — bot token with `chat:write` (summary) and `users.profile:read`
  (`assign.py --statuses`, which the agent reads to judge availability). For a private
  `slack.channel`, the bot must be invited to the channel.

## To finish / verify setup
1. `jira.project_key`, `unassigned_jql` and `jira.api_base` point at your real site and
   project (`SPAITSM`).
2. `priority_routing` key (`Critical`) matches your real Jira priority scheme.
3. Target percentages in `team` sum to 100 (`keyword_routing` members are excluded from
   this — they take no percentage).
4. Every `team`/`keyword_routing` member has a real `account_id` and `slack_user_id` (not
   a placeholder) — placeholder members are skipped, and members without a Slack ID
   default to available.
5. `slack.channel` is correct and the bot is in it.
6. First run with `dry_run: true`; review the summary before setting `dry_run: false`.

## How to get Atlassian accountIds / Slack user IDs (easiest first)
- **Ask Claude (with the connectors on):** e.g. "Look up the accountId and Slack user ID
  for these emails: …" — it resolves both directly.
- **Atlassian, from a profile URL:** the accountId is in the Jira profile URL.
- **Atlassian admin console:** Atlassian admin → User management.
- **Slack:** search by email in Slack's own member directory, or ask Claude as above.
