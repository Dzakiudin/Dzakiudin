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


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _dot_line(key: str, value: str, *, value_col: int = 34) -> str:
    left = f"{key}: "
    dots = "." * max(1, value_col - len(left))
    return f"{left}{dots} {value}"


def _render_terminal_svg(login: str, stats: dict, *, theme: str) -> str:
    width = 920
    height = 380

    if theme == "dark":
        bg = "#0d1117"
        panel = "#0b1220"
        border = "#30363d"
        text = "#e6edf3"
        muted = "#9da7b1"
        accent = "#58a6ff"
    else:
        bg = "#ffffff"
        panel = "#f6f8fa"
        border = "#d0d7de"
        text = "#24292f"
        muted = "#57606a"
        accent = "#0969da"

    now = dt.datetime.now(dt.timezone.utc).date().isoformat()

    left_lines = [
        "      __",
        " (\\,--------'()'--o",
        "  (_    ___    /~\"",
        "   (_)_)  (_)_)",
        "",
        "   dzakiudin",
        "",
        "   github.com/",
        f"   {login.lower()}",
    ]

    repos = _format_int(stats["repos"])
    stars = _format_int(stats["stars"])
    followers = _format_int(stats["followers"])
    following = _format_int(stats["following"])
    commits_year = _format_int(stats["commits_year"])
    prs_year = _format_int(stats["prs_year"])
    issues_year = _format_int(stats["issues_year"])
    reviews_year = _format_int(stats["reviews_year"])
    contribs_year = _format_int(stats["contribs_year"])

    right_lines = [
        f"{login} / README.md",
        "-" * 52,
        _dot_line("Updated", now),
        _dot_line("Focus", "Software Engineering"),
        _dot_line("Editor", "VS Code"),
        "",
        "GitHub Stats",
        "-" * 52,
        _dot_line("Repos", repos),
        _dot_line("Stars", stars),
        _dot_line("Followers", followers),
        _dot_line("Following", following),
        _dot_line("Commits (year)", commits_year),
        _dot_line("PRs (year)", prs_year),
        _dot_line("Issues (year)", issues_year),
        _dot_line("Reviews (year)", reviews_year),
        _dot_line("Contribs (yr)", contribs_year),
    ]

    left_width = 30
    line_count = max(len(left_lines), len(right_lines))
    combined = []
    for i in range(line_count):
        l = left_lines[i] if i < len(left_lines) else ""
        r = right_lines[i] if i < len(right_lines) else ""
        combined.append(l.ljust(left_width) + "  " + r)

    font_family = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace"
    font_size = 14
    line_height = 18

    x = 34
    y = 92

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{_escape_xml(login)} README card">',
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="{bg}"/>',
        f'<rect x="18.5" y="18.5" width="{width-37}" height="{height-37}" rx="14" fill="{panel}" stroke="{border}"/>',
        f'<circle cx="44" cy="44" r="6" fill="#ff5f57"/>',
        f'<circle cx="64" cy="44" r="6" fill="#febc2e"/>',
        f'<circle cx="84" cy="44" r="6" fill="#28c840"/>',
        f'<text x="110" y="49" fill="{muted}" font-family="{font_family}" font-size="12">{_escape_xml(login)} / README.md</text>',
        f'<text x="{x}" y="{y}" fill="{text}" font-family="{font_family}" font-size="{font_size}" xml:space="preserve">',
    ]

    for idx, line in enumerate(combined):
        dy = 0 if idx == 0 else line_height
        lines.append(f'<tspan x="{x}" dy="{dy}">{_escape_xml(line)}</tspan>')

    lines.extend(
        [
            "</text>",
            f'<rect x="18.5" y="18.5" width="{width-37}" height="{height-37}" rx="14" fill="none" stroke="{border}"/>',
            f'<rect x="18.5" y="18.5" width="{width-37}" height="56" rx="14" fill="none" stroke="{border}"/>',
            f'<rect x="18.5" y="18.5" width="{width-37}" height="{height-37}" rx="14" fill="none" stroke="{accent}" opacity="0.18"/>',
            "</svg>",
        ]
    )

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

    _write_file("light_mode.svg", _render_terminal_svg(login, stats, theme="light"))
    _write_file("dark_mode.svg", _render_terminal_svg(login, stats, theme="dark"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
