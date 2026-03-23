from __future__ import annotations

import datetime as dt
import json
import os
import textwrap
import urllib.error
import urllib.request


def _format_int(value: int) -> str:
    return f"{value:,}"


def _graphql_request(token: str, query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        method="POST",
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-summary-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "errors" in payload and payload["errors"]:
        raise RuntimeError(payload["errors"][0].get("message") or "GraphQL error")
    return payload["data"]


def _fetch_stats(token: str, login: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=365)

    query = textwrap.dedent(
        """
        query($login: String!, $from: DateTime!, $to: DateTime!, $after: String) {
          user(login: $login) {
            followers { totalCount }
            following { totalCount }
            contributionsCollection(from: $from, to: $to) {
              totalCommitContributions
              totalIssueContributions
              totalPullRequestContributions
              totalPullRequestReviewContributions
              contributionCalendar { totalContributions }
            }
            repositories(ownerAffiliations: OWNER, isFork: false, first: 100, after: $after) {
              totalCount
              pageInfo { hasNextPage endCursor }
              nodes { stargazerCount forkCount }
            }
          }
        }
        """
    ).strip()

    after = None
    total_stars = 0
    total_forks = 0
    repos_total_count = 0
    base_stats = None

    while True:
        data = _graphql_request(
            token,
            query,
            {
                "login": login,
                "from": start.isoformat(),
                "to": now.isoformat(),
                "after": after,
            },
        )
        user = data["user"]
        if user is None:
            raise RuntimeError(f"User '{login}' not found")
        repos = user["repositories"]

        if base_stats is None:
            repos_total_count = repos["totalCount"]
            cc = user["contributionsCollection"]
            base_stats = {
                "followers": user["followers"]["totalCount"],
                "following": user["following"]["totalCount"],
                "repos": repos_total_count,
                "commits_year": cc["totalCommitContributions"],
                "prs_year": cc["totalPullRequestContributions"],
                "issues_year": cc["totalIssueContributions"],
                "reviews_year": cc["totalPullRequestReviewContributions"],
                "contribs_year": cc["contributionCalendar"]["totalContributions"],
            }

        for node in repos["nodes"]:
            total_stars += int(node["stargazerCount"] or 0)
            total_forks += int(node["forkCount"] or 0)

        if not repos["pageInfo"]["hasNextPage"]:
            break
        after = repos["pageInfo"]["endCursor"]

    base_stats["stars"] = total_stars
    base_stats["forks"] = total_forks
    return base_stats


def _render_svg(login: str, stats: dict, *, theme: str) -> str:
    width = 760
    height = 190

    if theme == "dark":
        bg = "#0d1117"
        border = "#30363d"
        title = "#e6edf3"
        label = "#9da7b1"
        value = "#e6edf3"
        accent = "#58a6ff"
    else:
        bg = "#ffffff"
        border = "#d0d7de"
        title = "#24292f"
        label = "#57606a"
        value = "#24292f"
        accent = "#0969da"

    title_text = f"{login} • Profile Summary"

    items = [
        ("Public Repos", _format_int(stats["repos"])),
        ("Stars Earned", _format_int(stats["stars"])),
        ("Followers", _format_int(stats["followers"])),
        ("Following", _format_int(stats["following"])),
        ("Commits (last year)", _format_int(stats["commits_year"])),
        ("PRs (last year)", _format_int(stats["prs_year"])),
        ("Issues (last year)", _format_int(stats["issues_year"])),
        ("Reviews (last year)", _format_int(stats["reviews_year"])),
        ("Total Contributions", _format_int(stats["contribs_year"])),
    ]

    col_count = 3
    row_count = 3
    x0 = 32
    y0 = 74
    col_w = 232
    row_h = 36

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{title_text}">',
        f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="14" fill="{bg}" stroke="{border}"/>',
        f'<text x="28" y="42" fill="{title}" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial" font-size="20" font-weight="700">{title_text}</text>',
        f'<rect x="28" y="54" width="64" height="4" rx="2" fill="{accent}"/>',
    ]

    for idx, (k, v) in enumerate(items[: col_count * row_count]):
        col = idx % col_count
        row = idx // col_count
        x = x0 + col * col_w
        y = y0 + row * row_h
        lines.append(
            f'<text x="{x}" y="{y}" fill="{label}" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial" font-size="12">{k}</text>'
        )
        lines.append(
            f'<text x="{x}" y="{y+18}" fill="{value}" font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial" font-size="16" font-weight="700">{v}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def main() -> int:
    login = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GITHUB_ACTOR") or "Dzakiudin"
    token = os.environ.get("GITHUB_TOKEN") or ""

    try:
        if not token:
            raise RuntimeError("Missing GITHUB_TOKEN")
        stats = _fetch_stats(token, login)
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError):
        stats = {
            "repos": 0,
            "stars": 0,
            "forks": 0,
            "followers": 0,
            "following": 0,
            "commits_year": 0,
            "prs_year": 0,
            "issues_year": 0,
            "reviews_year": 0,
            "contribs_year": 0,
        }

    _write_file("profile-summary-light.svg", _render_svg(login, stats, theme="light"))
    _write_file("profile-summary-dark.svg", _render_svg(login, stats, theme="dark"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
