from __future__ import annotations

import datetime as dt
import base64
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
            createdAt
            followers { totalCount }
            following { totalCount }
            contributionsCollection(from: $from, to: $to) {
              totalCommitContributions
              totalIssueContributions
              totalPullRequestContributions
              totalPullRequestReviewContributions
              contributionCalendar { totalContributions }
              repositoryContributionsByRepository(maxRepositories: 100) {
                repository { nameWithOwner }
                contributions { totalCount }
              }
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
            contributed_repos = len(cc.get("repositoryContributionsByRepository") or [])
            base_stats = {
                "created_at": user["createdAt"],
                "followers": user["followers"]["totalCount"],
                "following": user["following"]["totalCount"],
                "repos": repos_total_count,
                "contributed_repos_year": contributed_repos,
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


def _fetch_image_data_uri(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "profile-readme-card"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type") or ""
        data = resp.read()
    if not data:
        return ""
    if "image/" not in content_type:
        content_type = "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def _file_image_data_uri(path: str) -> str:
    ext = os.path.splitext(path.lower())[1]
    if ext in {".jpg", ".jpeg"}:
        content_type = "image/jpeg"
    elif ext == ".png":
        content_type = "image/png"
    elif ext == ".webp":
        content_type = "image/webp"
    else:
        content_type = "image/png"
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        return ""
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def _format_duration(start: dt.datetime, end: dt.datetime) -> str:
    days = max(0, (end - start).days)
    years = days // 365
    days -= years * 365
    months = days // 30
    days -= months * 30
    parts = []
    if years:
        parts.append(f"{years} years")
    if months:
        parts.append(f"{months} months")
    parts.append(f"{days} days")
    return ", ".join(parts)


def _count_loc(root: str) -> int:
    ignore_dirs = {".git", ".github", "__pycache__"}
    ignore_ext = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip"}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
        for filename in filenames:
            _, ext = os.path.splitext(filename.lower())
            if ext in ignore_ext:
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, "rb") as f:
                    data = f.read()
                if b"\x00" in data:
                    continue
                count += data.count(b"\n")
            except OSError:
                continue
    return count


def _render_card_svg(login: str, stats: dict, *, theme: str) -> str:
    width_px = 985
    height_px = 530

    if theme == "dark":
        bg = "#161b22"
        fg = "#c9d1d9"
        key = "#ffa657"
        value = "#a5d6ff"
        cc = "#616e7f"
        border = "#30363d"
    else:
        bg = "#ffffff"
        fg = "#24292f"
        key = "#bc4c00"
        value = "#0969da"
        cc = "#57606a"
        border = "#d0d7de"

    now_dt = dt.datetime.now(dt.timezone.utc)
    created_at_raw = stats.get("created_at") or ""
    created_at = None
    try:
        created_at = dt.datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except ValueError:
        created_at = None
    uptime = _format_duration(created_at, now_dt) if created_at else "unknown"

    profile_os = os.environ.get("PROFILE_OS") or "Windows 10, Android, Linux"
    profile_host = os.environ.get("PROFILE_HOST") or "GitHub"
    profile_kernel = os.environ.get("PROFILE_KERNEL") or "Software Engineering"
    profile_ide = os.environ.get("PROFILE_IDE") or "VS Code"
    profile_lang_prog = os.environ.get("PROFILE_LANG_PROG") or "Python, TypeScript, JavaScript"
    profile_lang_comp = os.environ.get("PROFILE_LANG_COMP") or "HTML, CSS, JSON, YAML"
    profile_lang_real = os.environ.get("PROFILE_LANG_REAL") or "Indonesian, English"
    profile_hobby_soft = os.environ.get("PROFILE_HOBBY_SOFT") or "Web Dev, Open Source"
    profile_hobby_hw = os.environ.get("PROFILE_HOBBY_HW") or "PC Building"

    profile_email = os.environ.get("PROFILE_EMAIL") or ""
    profile_discord = os.environ.get("PROFILE_DISCORD") or ""
    profile_linkedin = os.environ.get("PROFILE_LINKEDIN") or ""

    repos = _format_int(stats["repos"])
    contributed_repos_year = _format_int(stats.get("contributed_repos_year") or 0)
    stars = _format_int(stats["stars"])
    commits_year = _format_int(stats["commits_year"])
    followers = _format_int(stats["followers"])
    contribs_year = _format_int(stats["contribs_year"])
    loc = _format_int(_count_loc(os.getcwd()))

    image_file = os.environ.get("PROFILE_IMAGE_FILE") or ""
    image_url = os.environ.get("PROFILE_IMAGE_URL") or ""
    image_data_uri = ""
    try:
        if image_file and os.path.exists(image_file):
            image_data_uri = _file_image_data_uri(image_file)
        elif os.path.exists("download (1).jpg"):
            image_data_uri = _file_image_data_uri("download (1).jpg")
        elif image_url:
            image_data_uri = _fetch_image_data_uri(image_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        image_data_uri = ""

    header_line = " -———————————————————————————————————————————————-—-"
    contact_line = " - Contact" + " -——————————————————————————————————————————————-—-"

    lines = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        f'<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,monospace" width="{width_px}px" height="{height_px}px" font-size="16px">',
        "<style>",
        "@font-face {",
        "  src: local('Consolas'), local('Consolas Bold');",
        "  font-family: 'ConsolasFallback';",
        "  font-display: swap;",
        "  -webkit-size-adjust: 109%;",
        "  size-adjust: 109%;",
        "}",
        f".key {{fill: {key};}}",
        f".value {{fill: {value};}}",
        ".addColor {fill: #3fb950;}",
        ".delColor {fill: #f85149;}",
        f".cc {{fill: {cc};}}",
        "text, tspan {white-space: pre;}",
        "</style>",
        f'<rect width="{width_px}px" height="{height_px}px" fill="{bg}" rx="15" stroke="{border}"/>',
    ]

    if image_data_uri:
        lines.extend(
            [
                "<defs>",
                '<clipPath id="pfp_clip">',
                '<circle cx="170" cy="200" r="135" />',
                "</clipPath>",
                "</defs>",
                f'<image x="35" y="65" width="270" height="270" href="{image_data_uri}" clip-path="url(#pfp_clip)" preserveAspectRatio="xMidYMid slice" />',
                f'<circle cx="170" cy="200" r="135" fill="none" stroke="{border}" stroke-width="2" />',
                f'<text x="60" y="380" fill="{fg}">{_escape_xml(login)}</text>',
                f'<text x="60" y="405" fill="{fg}">github.com/{_escape_xml(login.lower())}</text>',
            ]
        )
    else:
        lines.extend(
            [
                f'<text x="50" y="120" fill="{fg}">{_escape_xml(login)}</text>',
                f'<text x="50" y="145" fill="{fg}">Set PROFILE_IMAGE_URL</text>',
            ]
        )

    lines.append(f'<text x="390" y="30" fill="{fg}">')
    lines.append(f'<tspan x="390" y="30">{_escape_xml(login.lower())}@github</tspan>{_escape_xml(header_line)}')
    lines.append(f'<tspan x="390" y="50" class="cc">. </tspan><tspan class="key">OS</tspan>:<tspan class="cc"> ........................ </tspan><tspan class="value">{_escape_xml(profile_os)}</tspan>')
    lines.append(f'<tspan x="390" y="70" class="cc">. </tspan><tspan class="key">Uptime</tspan>:<tspan class="cc" id="age_data_dots"> ...................... </tspan><tspan class="value" id="age_data">{_escape_xml(uptime)}</tspan>')
    lines.append(f'<tspan x="390" y="90" class="cc">. </tspan><tspan class="key">Host</tspan>:<tspan class="cc"> ............................. </tspan><tspan class="value">{_escape_xml(profile_host)}</tspan>')
    lines.append(f'<tspan x="390" y="110" class="cc">. </tspan><tspan class="key">Kernel</tspan>:<tspan class="cc"> ...... </tspan><tspan class="value">{_escape_xml(profile_kernel)}</tspan>')
    lines.append(f'<tspan x="390" y="130" class="cc">. </tspan><tspan class="key">IDE</tspan>:<tspan class="cc"> ........................ </tspan><tspan class="value">{_escape_xml(profile_ide)}</tspan>')
    lines.append(f'<tspan x="390" y="150" class="cc">. </tspan>')
    lines.append(f'<tspan x="390" y="170" class="cc">. </tspan><tspan class="key">Languages</tspan>.<tspan class="key">Programming</tspan>:<tspan class="cc"> ..... </tspan><tspan class="value">{_escape_xml(profile_lang_prog)}</tspan>')
    lines.append(f'<tspan x="390" y="190" class="cc">. </tspan><tspan class="key">Languages</tspan>.<tspan class="key">Computer</tspan>:<tspan class="cc"> ......... </tspan><tspan class="value">{_escape_xml(profile_lang_comp)}</tspan>')
    lines.append(f'<tspan x="390" y="210" class="cc">. </tspan><tspan class="key">Languages</tspan>.<tspan class="key">Real</tspan>:<tspan class="cc"> ......................... </tspan><tspan class="value">{_escape_xml(profile_lang_real)}</tspan>')
    lines.append(f'<tspan x="390" y="230" class="cc">. </tspan>')
    lines.append(f'<tspan x="390" y="250" class="cc">. </tspan><tspan class="key">Hobbies</tspan>.<tspan class="key">Software</tspan>:<tspan class="cc"> .... </tspan><tspan class="value">{_escape_xml(profile_hobby_soft)}</tspan>')
    lines.append(f'<tspan x="390" y="270" class="cc">. </tspan><tspan class="key">Hobbies</tspan>.<tspan class="key">Hardware</tspan>:<tspan class="cc"> ............. </tspan><tspan class="value">{_escape_xml(profile_hobby_hw)}</tspan>')

    if profile_email or profile_discord or profile_linkedin:
        lines.append(f'<tspan x="390" y="310">{_escape_xml(contact_line)}</tspan>')
        y = 330
        if profile_email:
            lines.append(f'<tspan x="390" y="{y}" class="cc">. </tspan><tspan class="key">Email</tspan>.<tspan class="key">Personal</tspan>:<tspan class="cc"> ..................... </tspan><tspan class="value">{_escape_xml(profile_email)}</tspan>')
            y += 20
        if profile_linkedin:
            lines.append(f'<tspan x="390" y="{y}" class="cc">. </tspan><tspan class="key">LinkedIn</tspan>:<tspan class="cc"> .......................... </tspan><tspan class="value">{_escape_xml(profile_linkedin)}</tspan>')
            y += 20
        if profile_discord:
            lines.append(f'<tspan x="390" y="{y}" class="cc">. </tspan><tspan class="key">Discord</tspan>:<tspan class="cc"> ........................... </tspan><tspan class="value">{_escape_xml(profile_discord)}</tspan>')
            y += 20
        stats_y = y + 30
    else:
        stats_y = 310

    lines.append(f'<tspan x="390" y="{stats_y}">- GitHub Stats{_escape_xml(" -——————————————————————————————————————————————-—-")}</tspan>')
    lines.append(f'<tspan x="390" y="{stats_y+20}" class="cc">. </tspan><tspan class="key">Repos</tspan>:<tspan class="cc"> ....... </tspan><tspan class="value">{repos}</tspan> <tspan class="cc">{{Contributed: </tspan><tspan class="value">{contributed_repos_year}</tspan><tspan class="cc">}} | Stars: </tspan><tspan class="value">{stars}</tspan>')
    lines.append(f'<tspan x="390" y="{stats_y+40}" class="cc">. </tspan><tspan class="key">Commits</tspan>:<tspan class="cc"> ...... </tspan><tspan class="value">{commits_year}</tspan><tspan class="cc"> | Followers: </tspan><tspan class="value">{followers}</tspan>')
    lines.append(f'<tspan x="390" y="{stats_y+60}" class="cc">. </tspan><tspan class="key">Lines of Code</tspan>:<tspan class="cc"> .. </tspan><tspan class="value">{loc}</tspan><tspan class="cc"> | Contribs (yr): </tspan><tspan class="value">{contribs_year}</tspan>')

    lines.append("</text>")
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
            "created_at": "",
            "repos": 0,
            "stars": 0,
            "forks": 0,
            "followers": 0,
            "following": 0,
            "contributed_repos_year": 0,
            "commits_year": 0,
            "prs_year": 0,
            "issues_year": 0,
            "reviews_year": 0,
            "contribs_year": 0,
        }

    _write_file("light_mode.svg", _render_card_svg(login, stats, theme="light"))
    _write_file("dark_mode.svg", _render_card_svg(login, stats, theme="dark"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
