#!/usr/bin/env python3
"""IT ticket auto-router (Managed Agents version).

Deterministic: every routing decision is made here from routing-config.yaml.
The agent supplies only the Slack availability judgment, via --unavailable.

Environment:
  JIRA_API_TOKEN   scoped token for the svc-itsm-router service account (Bearer)
  SLACK_BOT_TOKEN  Slack bot token with chat:write (needed only when posting)

Exit codes: 0 = run finished (or stopped by kill-switch / business hours),
            1 = config, credential or Jira error (no further action taken),
            2 = run finished but the Slack summary could not be posted.
"""
import argparse
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routing-config.yaml")


class ConfigError(Exception):
    pass


class ApiError(Exception):
    pass


# --- helpers -----------------------------------------------------------------

def is_placeholder(value):
    v = str(value or "").strip()
    return not v or v.startswith("<") or v.upper() in ("TODO", "PLACEHOLDER")


def http(method, url, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        raise ApiError(f"{method} {urllib.parse.urlparse(url).path} -> HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ApiError(f"{method} {urllib.parse.urlparse(url).path} -> {e.reason}")
    return json.loads(raw) if raw else None


def adf_text(node):
    """Flatten an Atlassian Document Format description to plain text."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(adf_text(c) for c in node.get("content", [])) + " "
    if isinstance(node, list):
        return "".join(adf_text(c) for c in node)
    return node if isinstance(node, str) else ""


def matches(keyword, text, mode):
    kw = keyword.lower()
    if mode == "word":
        return re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text) is not None
    return kw in text


# --- Jira ----------------------------------------------------------------------

class Jira:
    def __init__(self, api_base, token):
        self.base = api_base.rstrip("/") + "/rest/api/3"
        self.token = token

    def search(self, jql, fields):
        issues, page_token = [], None
        while True:
            body = {"jql": jql, "fields": fields, "maxResults": 100}
            if page_token:
                body["nextPageToken"] = page_token
            page = http("POST", f"{self.base}/search/jql", self.token, body)
            issues += page.get("issues", [])
            page_token = page.get("nextPageToken")
            if not page_token:
                return issues

    def issue(self, key):
        return http("GET", f"{self.base}/issue/{urllib.parse.quote(key)}?fields=status,assignee", self.token)

    def assign(self, key, account_id):
        # Assignee-only endpoint: sets the assignee field and nothing else.
        http("PUT", f"{self.base}/issue/{urllib.parse.quote(key)}/assignee", self.token, {"accountId": account_id})


# --- config --------------------------------------------------------------------

def load_config(path):
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {path}: {e}")
    if not isinstance(cfg, dict):
        raise ConfigError("config is not a YAML mapping")
    return cfg


def validate(cfg):
    for key in ("business_hours", "jira", "strategy", "team"):
        if key not in cfg:
            raise ConfigError(f"missing key: {key}")
    for key in ("api_base", "project_key", "unassigned_jql"):
        if not cfg["jira"].get(key):
            raise ConfigError(f"missing key: jira.{key}")
    if cfg["jira"].get("load_basis", "open") != "open":
        raise ConfigError("jira.load_basis: only 'open' is supported")
    if cfg["strategy"] not in ("load_balanced", "weighted_random"):
        raise ConfigError(f"unknown strategy: {cfg['strategy']}")
    if cfg.get("match_mode", "substring") not in ("substring", "word"):
        raise ConfigError(f"unknown match_mode: {cfg.get('match_mode')}")


# --- routing -------------------------------------------------------------------

class Router:
    """Routing state for one run. Pure: never talks to Jira."""

    def __init__(self, cfg, loads, unavailable, mode):
        self.cfg, self.mode, self.unavailable = cfg, mode, unavailable
        self.cap = cfg.get("max_per_run_per_person")
        self.team = [m for m in cfg["team"] if not is_placeholder(m.get("account_id"))]
        self.keyword = [m for m in cfg.get("keyword_routing") or [] if not is_placeholder(m.get("account_id"))]
        self.by_name = {m["name"]: m for m in self.keyword + self.team}
        self.loads = dict(loads)
        self.counts = {}

    def decide(self, key, text, priority):
        """Return (member, rule) or (None, reason)."""
        # b. priority routing
        routes = {str(k).lower(): v for k, v in (self.cfg.get("priority_routing") or {}).items()}
        if priority and priority.lower() in routes:
            names = routes[priority.lower()]
            names = [names] if isinstance(names, str) else list(names)
            eligible = [self.by_name[n] for n in names if n in self.by_name]
            if eligible:
                return self._finish(key, eligible, {m["name"]: f"priority={priority}" for m in eligible})

        # c. keyword routing
        rules = {}
        for m in self.keyword:
            hit = next((k for k in m.get("keywords", []) if matches(k, text, self.mode)), None)
            if hit:
                rules[m["name"]] = f'keyword="{hit}"'
        if rules:
            return self._finish(key, [self.by_name[n] for n in rules], rules)

        # d. skill match, then fallback
        for m in self.team:
            hit = next((k for k in m.get("skills", []) if matches(k, text, self.mode)), None)
            if hit:
                rules[m["name"]] = f'matched "{hit}"'
        if rules:
            return self._finish(key, [m for m in self.team if m["name"] in rules], rules)
        if self.cfg.get("fallback_to_all"):
            return self._finish(key, self.team, {m["name"]: "fallback" for m in self.team})
        return None, "unassigned — no skill match (fallback_to_all is false)"

    def _finish(self, key, eligible, rules):
        # e. per-run cap and availability
        capped = [m for m in eligible if self.cap is not None and self.counts.get(m["name"], 0) >= self.cap]
        out = [m for m in eligible if m not in capped and m.get("slack_user_id") in self.unavailable]
        left = [m for m in eligible if m not in capped and m not in out]
        if not left:
            if not out:
                return None, "deferred — all eligible members at per-run cap"
            if not capped:
                return None, "unassigned — all eligible members are out today"
            return None, "unassigned — all eligible members capped or out today"
        # f. select
        pick = self._select(key, left)
        return pick, rules[pick["name"]]

    def _select(self, key, left):
        if len(left) == 1:
            return left[0]
        if any("target_pct" not in m for m in left):
            return min(left, key=lambda m: m["name"])
        if self.cfg["strategy"] == "weighted_random":
            # Seeded by ticket key so reruns and the shadow compare are reproducible.
            return random.Random(key).choices(left, weights=[m["target_pct"] for m in left])[0]
        total = sum(self.loads.values())

        def deficit(m):
            share = self.loads.get(m["name"], 0) / total * 100 if total else 0
            return m["target_pct"] - share
        return sorted(left, key=lambda m: (-deficit(m), -m["target_pct"], m["name"]))[0]

    def commit(self, member):
        name = member["name"]
        self.counts[name] = self.counts.get(name, 0) + 1
        if name in self.loads:
            self.loads[name] += 1


# --- summary -------------------------------------------------------------------

def render(sections, site_url=None):
    """sections: list of (heading, [(key_or_None, text)]). site_url set = Slack mrkdwn."""
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if site_url else s

    lines = []
    for heading, items in sections:
        if heading:
            lines.append(esc(heading))
        for key, text in items:
            if key and site_url:
                key = f"<{site_url.rstrip('/')}/browse/{key}|{key}>"
            lines.append(f"  {key + ' ' if key else ''}{esc(text)}".rstrip())
    return "\n".join(lines)


def post_slack(channel, text):
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        raise ApiError("SLACK_BOT_TOKEN is not set")
    resp = http("POST", "https://slack.com/api/chat.postMessage", token,
                {"channel": channel, "text": text, "unfurl_links": False})
    if not resp.get("ok"):
        raise ApiError(f"chat.postMessage: {resp.get('error')}")


# --- run -----------------------------------------------------------------------

def run(args):
    try:
        cfg = load_config(args.config)
        if cfg.get("enabled") is not True:
            print("paused via kill-switch (enabled is not true) — no action")
            return 0
        validate(cfg)
    except ConfigError as e:
        print(f"config error: {e} — no action")
        return 1

    bh = cfg["business_hours"]
    now = datetime.now(ZoneInfo(bh["timezone"]))
    if DAYS[now.weekday()] not in bh["active_days"] or not bh["start_hour"] <= now.hour <= bh["end_hour"]:
        print(f"outside business hours ({now:%a %H:%M %Z}) — no action")
        return 0

    token = os.environ.get("JIRA_API_TOKEN")
    if not token:
        print("JIRA_API_TOKEN is not set — no action")
        return 1

    jira_cfg, slack_cfg = cfg["jira"], cfg.get("slack") or {}
    dry_run = cfg.get("dry_run") is not False  # anything but an explicit false is a dry run
    mode = cfg.get("match_mode", "substring")
    notes = []

    pct = sum(m.get("target_pct", 0) for m in cfg["team"])
    if pct != 100:
        notes.append(f"team target_pct sums to {pct}, not 100")
    for m in cfg["team"] + (cfg.get("keyword_routing") or []):
        if is_placeholder(m.get("account_id")):
            notes.append(f"{m['name']} skipped: placeholder account_id")

    if not cfg.get("availability_check", True):
        unavailable = set()
        notes.append("availability check disabled in config — everyone treated as available")
    elif args.unavailable is None:
        unavailable = set()
        notes.append("no availability list supplied — everyone treated as available")
    else:
        unavailable = {s.strip() for s in args.unavailable.split(",") if s.strip()}

    jira = Jira(jira_cfg["api_base"], token)
    try:
        jql = jira_cfg["unassigned_jql"]
        if "order by" not in jql.lower():
            jql += " ORDER BY created ASC"
        tickets = jira.search(jql, ["summary", "description", "priority", "status"])
        loads = {}
        for m in cfg["team"]:
            if is_placeholder(m.get("account_id")):
                continue
            q = f'project = "{jira_cfg["project_key"]}" AND assignee = "{m["account_id"]}" AND statusCategory != Done'
            loads[m["name"]] = len(jira.search(q, ["status"]))
    except ApiError as e:
        print(f"Jira error: {e} — no action")
        return 1

    router = Router(cfg, loads, unavailable, mode)
    other = "word" if mode == "substring" else "substring"
    shadow = Router(cfg, loads, unavailable, other) if cfg.get("shadow_compare") else None

    assigned, alerts, left, skipped, failed, diffs = [], [], [], [], [], []
    for t in tickets:
        key, f = t["key"], t["fields"]
        try:
            fresh = jira.issue(key)["fields"]
        except ApiError as e:
            failed.append((key, f"re-read failed: {e}"))
            continue
        status = fresh["status"]
        if status["statusCategory"]["key"] == "done":
            skipped.append((key, f"closed ({status['name']}) but returned by JQL — not assigned"))
            continue
        if fresh.get("assignee"):
            skipped.append((key, "already assigned — not touched"))
            continue

        text = " ".join(f"{f.get('summary') or ''} {adf_text(f.get('description'))}".lower().split())
        priority = (f.get("priority") or {}).get("name")
        member, rule = router.decide(key, text, priority)

        if shadow:
            s_member, s_rule = shadow.decide(key, text, priority)
            if s_member:
                shadow.commit(s_member)
            if (member or {}).get("name") != (s_member or {}).get("name"):
                a = member["name"] if member else "unassigned"
                b = s_member["name"] if s_member else "unassigned"
                diffs.append((key, f"{mode}: {a} | {other}: {b} ({s_rule})"))

        if member is None:
            left.append((key, rule))
            continue
        if not dry_run:
            try:
                jira.assign(key, member["account_id"])
            except ApiError as e:
                failed.append((key, f"assign to {member['name']} failed: {e}"))
                continue
            try:
                after = jira.issue(key)["fields"]["status"]
                if after["id"] != status["id"]:
                    alerts.append((key, f"status changed by external automation after assignment "
                                        f"({status['name']} -> {after['name']})"))
            except ApiError as e:
                alerts.append((key, f"could not re-read status after assignment: {e}"))
        router.commit(member)
        assigned.append((key, f"-> {member['name']} ({rule})"))

    names = {m.get("slack_user_id"): m["name"] for m in cfg["team"] + (cfg.get("keyword_routing") or [])}
    header = (f"ITSM router — {'DRY RUN, no Jira writes' if dry_run else 'LIVE'} — {now:%Y-%m-%d %H:%M %Z}\n"
              f"{len(tickets)} unassigned found | {len(assigned)} {'would be ' if dry_run else ''}assigned | "
              f"{len(left)} left unassigned | {len(skipped)} skipped | {len(failed)} failed")
    sections = [(header, [])]
    if alerts:
        sections.append(("⚠️ STATUS CHANGED AFTER ASSIGNMENT — needs a human look:", alerts))
    if assigned:
        sections.append(("Would assign:" if dry_run else "Assigned:", assigned))
    if left:
        sections.append(("Left unassigned:", left))
    if skipped:
        sections.append(("Skipped:", skipped))
    if failed:
        sections.append(("Failed:", failed))
    if unavailable:
        sections.append(("Out today (from Slack status):",
                         [(None, names.get(u, f"unknown Slack ID {u}")) for u in sorted(unavailable)]))
    if shadow:
        sections.append((f"Shadow compare ({other}): " + (f"{len(diffs)} difference(s)" if diffs else "no differences"),
                         diffs))
    if notes:
        sections.append(("Notes:", [(None, n) for n in notes]))

    print(render(sections))

    channel = slack_cfg.get("channel")
    if args.no_post or (not tickets and not slack_cfg.get("post_when_empty")):
        return 0
    if is_placeholder(channel):
        print("slack.channel not set — summary not posted")
        return 0
    try:
        post_slack(channel, render(sections, jira_cfg.get("site_url") or None))
    except ApiError as e:
        print(f"Slack post failed: {e}")
        return 2
    print(f"summary posted to {channel}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Assign unassigned SPAITSM tickets per routing-config.yaml.")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="path to routing-config.yaml (default: next to this script)")
    p.add_argument("--unavailable", metavar="SLACK_IDS",
                   help='comma-separated Slack user IDs judged out today; pass "" when nobody is out')
    p.add_argument("--no-post", action="store_true", help="print the summary only; do not post to Slack")
    sys.exit(run(p.parse_args()))


if __name__ == "__main__":
    main()
