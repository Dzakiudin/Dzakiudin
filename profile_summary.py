from __future__ import annotations

import datetime as dt
import base64
import io
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
              totalRepositoriesWithContributedCommits
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
            contributed_repos = cc.get("totalRepositoriesWithContributedCommits", 0)
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


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _fetch_url_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "profile-readme-card"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _image_bytes_from_sources(image_file: str, image_url: str) -> bytes:
    if image_file and os.path.exists(image_file):
        return _read_file_bytes(image_file)
    if os.path.exists("download (1).jpg"):
        return _read_file_bytes("download (1).jpg")
    if image_url:
        return _fetch_url_bytes(image_url)
    return b""


def _image_to_ascii_lines(image_bytes: bytes, *, cols: int, rows: int, invert: bool) -> list[str]:
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        return []

    if not image_bytes:
        return []

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("L")  # Konversi ke Grayscale
    except Exception:
        return []

    # 1. MENINGKATKAN KONTRAS & DETAIL
    # Sangat penting untuk gambar stencil agar hitamnya benar-benar hitam dan putihnya bersih
    img = ImageOps.autocontrast(img, cutoff=0.5) 
    img = img.filter(ImageFilter.SHARPEN) # Mempertajam tepian karakter

    # 2. PENYESUAIAN UKURAN (Resize)
    # Gunakan Resampling LANCZOS untuk menjaga detail saat pengecilan ukuran
    w, h = img.size
    aspect_ratio = h / w
    # ASCII karakter biasanya lebih tinggi dari lebarnya (rasio ~1.6 - 2.0)
    # Kita kalibrasi agar gambar tidak terlihat "gepeng"
    target_h = int(cols * aspect_ratio * 0.55) 
    img = img.resize((cols, target_h), Image.Resampling.LANCZOS)

    # Pastikan jumlah baris sesuai dengan permintaan atau hasil resize
    actual_rows = rows if rows > 10 else target_h
    img = img.resize((cols, actual_rows), Image.Resampling.LANCZOS)

    pixels = list(img.getdata())
    
    # Invert jika background GitHub user adalah gelap tapi gambarnya putih (atau sebaliknya)
    if invert:
        pixels = [255 - p for p in pixels]

    # 3. RAMP KARAKTER YANG LEBIH HALUS
    # Ramp ini diurutkan dari yang paling tipis ke yang paling padat untuk tekstur yang kaya
    ramp = "$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\\|()1{}[]?-_+~<>i!lI;:,\"^`'. "
    if not invert:
        ramp = ramp[::-1] # Balik urutan jika tidak di-invert
        
    ramp_len = len(ramp) - 1

    lines: list[str] = []
    for r in range(actual_rows):
        row = pixels[r * cols : (r + 1) * cols]
        # Mapping pixel (0-255) ke karakter di dalam ramp
        line_chars = [ramp[int((p / 255) * ramp_len)] for p in row]
        lines.append("".join(line_chars).rstrip())
    
    return lines

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
    font_size = 16
    char_w = font_size * 0.6
    right_x = 380
    right_pad = int(os.environ.get("PROFILE_RIGHT_PAD") or "14")
    right_edge = width_px - right_pad

    if theme == "dark":
        bg = "#161b22"
        fg = "#c9d1d9"
        key = "#ffa657"
        value = "#a5d6ff"
        cc = "#616e7f"
    else:
        bg = "#ffffff"
        fg = "#24292f"
        key = "#bc4c00"
        value = "#0969da"
        cc = "#57606a"

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
    profile_facebook = os.environ.get("PROFILE_FACEBOOK") or ""
    profile_instagram = os.environ.get("PROFILE_INSTAGRAM") or ""

    repos = _format_int(stats["repos"])
    contributed_repos_year = _format_int(stats.get("contributed_repos_year") or 0)
    stars = _format_int(stats["stars"])
    commits_year = _format_int(stats["commits_year"])
    followers = _format_int(stats["followers"])
    following = _format_int(stats["following"])
    prs_year = _format_int(stats["prs_year"])
    issues_year = _format_int(stats["issues_year"])
    reviews_year = _format_int(stats["reviews_year"])
    contribs_year = _format_int(stats["contribs_year"])
    loc = _format_int(_count_loc(os.getcwd()))

    image_file = os.environ.get("PROFILE_IMAGE_FILE") or ""
    image_url = os.environ.get("PROFILE_IMAGE_URL") or ""
    left_mode = (os.environ.get("PROFILE_LEFT_MODE") or "image").strip().lower()
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

    image_bytes = b""
    try:
        image_bytes = _image_bytes_from_sources(image_file, image_url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        image_bytes = b""

    ascii_enabled = left_mode == "ascii"
    ascii_invert = (os.environ.get("PROFILE_ASCII_INVERT") or "").strip().lower() in {"1", "true", "yes"}

    header_line = " - "

    def _split_key(key_text: str) -> list[str]:
        return key_text.split(".") if "." in key_text else [key_text]

    def _build_header_line(y: int, left_text: str) -> str:
        left_text = f"{left_text} "
        bar_px = max(0.0, (right_edge - right_x) - (len(left_text) * char_w))
        bar = "—" * 120
        return (
            f'<text x="{right_x}" y="{y}" fill="{fg}">'
            f'<tspan>{_escape_xml(left_text)}</tspan>'
            f'<tspan class="cc" lengthAdjust="spacingAndGlyphs" textLength="{bar_px:.1f}">{_escape_xml(bar)}</tspan>'
            f"</text>"
        )

    def _build_section_line(y: int, title: str) -> str:
        left_text = f"{title} "
        bar_px = max(0.0, (right_edge - right_x) - (len(left_text) * char_w))
        bar = "—" * 120
        return (
            f'<text x="{right_x}" y="{y}" fill="{fg}">'
            f'<tspan>{_escape_xml(left_text)}</tspan>'
            f'<tspan class="cc" lengthAdjust="spacingAndGlyphs" textLength="{bar_px:.1f}">{_escape_xml(bar)}</tspan>'
            f"</text>"
        )

    def _build_kv_line(y: int, key_text: str, val_text: str) -> str:
        prefix = f". {key_text}: "
        prefix_w = len(prefix) * char_w
        dots_px = max(0.0, (right_edge - right_x) - prefix_w - (char_w * 1.0))
        dots_text = "." * 240

        parts = [f'<text x="{right_x}" y="{y}" fill="{fg}">', '<tspan class="cc">. </tspan>']
        key_parts = _split_key(key_text)
        for i, part in enumerate(key_parts):
            parts.append(f'<tspan class="key">{_escape_xml(part)}</tspan>')
            if i != len(key_parts) - 1:
                parts.append('<tspan class="key">.</tspan>')
        parts.append('<tspan class="cc">: </tspan>')
        parts.append(
            f'<tspan class="cc" lengthAdjust="spacingAndGlyphs" textLength="{dots_px:.1f}">{dots_text}</tspan>'
        )
        parts.append("</text>")

        rect_pad = 10
        value_w = len(val_text) * char_w
        rect_w = value_w + rect_pad * 2
        rect_x = max(float(right_x), float(right_edge) - rect_w)
        rect_y = y - font_size + 3
        rect_h = font_size + 6

        return "".join(
            parts
            + [
                f'<rect x="{rect_x:.1f}" y="{rect_y}" width="{rect_w:.1f}" height="{rect_h}" fill="{bg}"/>',
                f'<text x="{right_edge}" y="{y}" class="value" text-anchor="end">{_escape_xml(val_text)}</text>',
            ]
        )

    def _build_kv_box(y: int, key_text: str, val_text: str, *, x_start: int, x_end: int, prefix: str) -> str:
        prefix_text = f"{prefix}{key_text}: "
        prefix_w = len(prefix_text) * char_w
        dots_px = max(0.0, (x_end - x_start) - prefix_w - (char_w * 1.0))
        dots_text = "." * 240

        parts = [f'<text x="{x_start}" y="{y}" fill="{fg}">', f'<tspan class="cc">{_escape_xml(prefix)}</tspan>']
        key_parts = _split_key(key_text)
        for i, part in enumerate(key_parts):
            parts.append(f'<tspan class="key">{_escape_xml(part)}</tspan>')
            if i != len(key_parts) - 1:
                parts.append('<tspan class="key">.</tspan>')
        parts.append('<tspan class="cc">: </tspan>')
        parts.append(
            f'<tspan class="cc" lengthAdjust="spacingAndGlyphs" textLength="{dots_px:.1f}">{dots_text}</tspan>'
        )
        parts.append("</text>")

        rect_pad = 10
        value_w = len(val_text) * char_w
        rect_w = value_w + rect_pad * 2
        rect_x = max(float(x_start), float(x_end) - rect_w)
        rect_y = y - font_size + 3
        rect_h = font_size + 6

        return "".join(
            parts
            + [
                f'<rect x="{rect_x:.1f}" y="{rect_y}" width="{rect_w:.1f}" height="{rect_h}" fill="{bg}"/>',
                f'<text x="{x_end}" y="{y}" class="value" text-anchor="end">{_escape_xml(val_text)}</text>',
            ]
        )

    top_y = int(os.environ.get("PROFILE_TOP_Y") or "30")
    has_contact = bool(profile_email or profile_discord or profile_linkedin or profile_facebook or profile_instagram)
    
    # Calculate actual stats_y position based on contact section
    if has_contact:
        y = top_y + 300
        if profile_email:
            y += 20
        if profile_linkedin:
            y += 20
        if profile_discord:
            y += 20
        if profile_facebook:
            y += 20
        if profile_instagram:
            y += 20
        stats_y = y + 30
    else:
        stats_y = top_y + 280

    right_bottom_y = stats_y + 60
    bottom_pad = int(os.environ.get("PROFILE_BOTTOM_PAD") or "30")
    height_px = max(360, int(right_bottom_y + bottom_pad))

    ascii_font_size = int(os.environ.get("PROFILE_ASCII_FONT_SIZE") or "16")
    ascii_line_h = int(os.environ.get("PROFILE_ASCII_LINE_H") or "20")
    ascii_y0 = top_y
    ascii_x0 = 15
    ascii_cols = int((right_x - ascii_x0) / (ascii_font_size * 0.6))
    ascii_rows = max(10, int((right_bottom_y - ascii_y0) / ascii_line_h) + 1)
    ascii_lines = (
        _image_to_ascii_lines(image_bytes, cols=ascii_cols, rows=ascii_rows, invert=ascii_invert) if ascii_enabled else []
    )

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
        f".ascii {{fill: {fg};}}",
        "text, tspan {white-space: pre;}",
        "</style>",
        f'<rect width="{width_px}px" height="{height_px}px" fill="{bg}" rx="15"/>',
    ]

    if ascii_lines:
        lines.append(f'<text x="{ascii_x0}" y="{ascii_y0}" class="ascii" font-size="{ascii_font_size}px">')
        for i, line in enumerate(ascii_lines):
            y = ascii_y0 + i * ascii_line_h
            lines.append(f'<tspan x="{ascii_x0}" y="{y}">{_escape_xml(line)}</tspan>')
        lines.append("</text>")
    elif image_data_uri:
        lines.extend(
            [
                "<defs>",
                '<clipPath id="pfp_clip">',
                '<circle cx="170" cy="195" r="150" />',
                "</clipPath>",
                "</defs>",
                f'<image x="20" y="45" width="300" height="300" href="{image_data_uri}" clip-path="url(#pfp_clip)" preserveAspectRatio="xMidYMid slice" />',
                f'<circle cx="170" cy="195" r="150" fill="none" stroke="{cc}" stroke-width="2" opacity="0.35" />',
            ]
        )

    lines.append(_build_header_line(top_y, f"{login.lower()}@github{header_line}"))
    lines.append(_build_kv_line(top_y + 20, "OS", profile_os))
    lines.append(_build_kv_line(top_y + 40, "Uptime", uptime))
    lines.append(_build_kv_line(top_y + 60, "Host", profile_host))
    lines.append(_build_kv_line(top_y + 80, "Kernel", profile_kernel))
    lines.append(_build_kv_line(top_y + 100, "IDE", profile_ide))
    lines.append(f'<text x="{right_x}" y="{top_y + 120}" class="cc">. </text>')
    lines.append(_build_kv_line(top_y + 140, "Languages.Programming", profile_lang_prog))
    lines.append(_build_kv_line(top_y + 160, "Languages.Computer", profile_lang_comp))
    lines.append(_build_kv_line(top_y + 180, "Languages.Real", profile_lang_real))
    lines.append(f'<text x="{right_x}" y="{top_y + 200}" class="cc">. </text>')
    lines.append(_build_kv_line(top_y + 220, "Hobbies.Software", profile_hobby_soft))
    lines.append(_build_kv_line(top_y + 240, "Hobbies.Hardware", profile_hobby_hw))

    if has_contact:
        lines.append(_build_section_line(top_y + 280, "- Contact"))
        y = top_y + 300
        if profile_email:
            lines.append(_build_kv_line(y, "Email.Personal", profile_email))
            y += 20
        if profile_linkedin:
            lines.append(_build_kv_line(y, "LinkedIn", profile_linkedin))
            y += 20
        if profile_discord:
            lines.append(_build_kv_line(y, "Discord", profile_discord))
            y += 20
        if profile_facebook:
            lines.append(_build_kv_line(y, "Facebook", profile_facebook))
            y += 20
        if profile_instagram:
            lines.append(_build_kv_line(y, "Instagram", profile_instagram))
            y += 20
        stats_y = y + 30

    lines.append(_build_section_line(stats_y, "- GitHub Stats"))
    col_mid = int((right_x + right_edge) / 2)
    gutter = 16
    left_start = right_x
    left_end = col_mid - gutter
    right_start = col_mid + gutter
    right_end = right_edge

    def _stats_row(y: int, left_label: str, left_val: str, right_label: str, right_val: str) -> list[str]:
        return [
            _build_kv_box(y, left_label, left_val, x_start=left_start, x_end=left_end, prefix=". "),
            f'<text x="{col_mid}" y="{y}" class="cc" text-anchor="middle">|</text>',
            _build_kv_box(y, right_label, right_val, x_start=right_start, x_end=right_end, prefix=""),
        ]

    lines.extend(
        _stats_row(
            stats_y + 20,
            "Repos",
            f"{repos} {{Contributed: {contributed_repos_year}}}",
            "Stars",
            stars,
        )
    )
    lines.extend(_stats_row(stats_y + 40, "Commits", commits_year, "Followers", followers))
    lines.extend(_stats_row(stats_y + 60, "Lines of Code", loc, "Contribs (yr)", contribs_year))

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
