"""
Export a Claude Code session JSONL into a readable Markdown transcript.

Sessions live at:
  C:\\Users\\<you>\\.claude\\projects\\<project-slug>\\<uuid>.jsonl

The JSONL has lots of housekeeping rows (ai-title, queue-operation, etc.)
we skip — only user + assistant messages turn into transcript entries.
Tool calls are shown as one-line summaries (full inputs would balloon the
file); tool results are truncated to keep things scannable.

Usage:
  python scripts/export_session.py                       # list sessions, pick interactively
  python scripts/export_session.py --list                # just list, don't export
  python scripts/export_session.py --session <uuid>      # export specific one
  python scripts/export_session.py --latest              # auto-pick most recent
  python scripts/export_session.py --output <path.md>    # custom output path
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Claude Code stores per-project sessions under this slug.
PROJECT_SLUG = "D--Project-claude-UNIS-Product-Selection"
SESSIONS_DIR = Path.home() / ".claude" / "projects" / PROJECT_SLUG
DEFAULT_OUT_DIR = Path(r"D:\ComputerFiles\Desktop")

# Tool-result content over this many chars gets truncated (keeps the
# markdown manageable for sessions with lots of Bash output).
TOOL_RESULT_MAX = 800


def list_sessions() -> list[Path]:
    """Return JSONL paths sorted newest-first."""
    if not SESSIONS_DIR.exists():
        return []
    return sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime)


def print_session_list(sessions: list[Path]) -> None:
    if not sessions:
        print(f"未找到任何 session,目录: {SESSIONS_DIR}")
        return
    print(f"{SESSIONS_DIR} 下共 {len(sessions)} 份 session(按修改时间倒序):\n")
    for i, p in enumerate(sessions, 1):
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        size_mb = p.stat().st_size / 1024 / 1024
        with p.open("r", encoding="utf-8") as f:
            n_lines = sum(1 for _ in f)
        print(f"  [{i}] {p.stem}  {mtime}  {size_mb:.1f} MB  ({n_lines} 行)")


def _fmt_content_blocks(content) -> str:
    """Render a message's content (str | list of blocks) into markdown."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return f"_(unknown content shape: {type(content).__name__})_"

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", "").strip())
        elif btype == "tool_use":
            name = block.get("name", "?")
            tid = block.get("id", "")
            inp = block.get("input", {})
            summary = _summarize_tool_input(name, inp)
            parts.append(f"> 🔧 **{name}** `{tid[:8]}` — {summary}")
        elif btype == "tool_result":
            tid = block.get("tool_use_id", "")
            raw = block.get("content", "")
            txt = _flatten_tool_result(raw)
            if len(txt) > TOOL_RESULT_MAX:
                txt = txt[:TOOL_RESULT_MAX] + f"\n... [截断,完整 {len(txt)} 字符]"
            err = " ❌" if block.get("is_error") else ""
            parts.append(f"> 📋 **tool_result**{err} `{tid[:8]}`\n>\n> ```\n> "
                         + txt.replace("\n", "\n> ") + "\n> ```")
        elif btype == "image":
            parts.append("> 🖼️ _(image)_")
        elif btype == "thinking":
            # Skip extended-thinking blocks — internal monologue
            continue
        else:
            parts.append(f"> _({btype} block)_")
    return "\n\n".join(p for p in parts if p)


def _flatten_tool_result(raw) -> str:
    """Tool results come as str OR list-of-blocks. Normalize to str."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        out = []
        for b in raw:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
            elif isinstance(b, dict) and b.get("type") == "image":
                out.append("[image]")
            else:
                out.append(json.dumps(b, ensure_ascii=False)[:200])
        return "\n".join(out)
    return str(raw)


def _summarize_tool_input(tool: str, inp: dict) -> str:
    """One-line, scannable summary of a tool call's most relevant arg."""
    if not isinstance(inp, dict):
        return "(no input)"
    if tool == "Bash":
        cmd = inp.get("command", "")
        return f"`{cmd[:100]}{'…' if len(cmd) > 100 else ''}`"
    if tool in ("Read", "Write"):
        return f"`{inp.get('file_path', '?')}`"
    if tool == "Edit":
        return f"`{inp.get('file_path', '?')}`"
    if tool == "Grep":
        return f"pattern=`{inp.get('pattern', '?')[:60]}`"
    if tool == "Glob":
        return f"pattern=`{inp.get('pattern', '?')}`"
    if tool == "TodoWrite":
        todos = inp.get("todos", [])
        return f"{len(todos)} todos"
    # Generic: show first 2 keys
    keys = list(inp.keys())[:2]
    return ", ".join(f"{k}=`{str(inp[k])[:50]}`" for k in keys)


def export(session_path: Path, output_path: Path) -> None:
    """Convert a JSONL session into a Markdown transcript."""
    entries: list[tuple[str, str, str]] = []  # (timestamp, role, body)

    with session_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") not in ("user", "assistant"):
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", d["type"])
            ts = d.get("timestamp", "")
            try:
                ts_short = datetime.fromisoformat(
                    ts.replace("Z", "+00:00")
                ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:                                         # noqa: BLE001
                ts_short = ts
            body = _fmt_content_blocks(msg.get("content"))
            if body.strip():
                entries.append((ts_short, role, body))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out:
        out.write(f"# Session: `{session_path.stem}`\n\n")
        out.write(f"- **来源**: `{session_path}`\n")
        out.write(f"- **导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write(f"- **消息数**: {len(entries)}(已过滤掉 ai-title/system 等元数据)\n\n")
        out.write("---\n\n")

        for ts, role, body in entries:
            badge = "👤 **User**" if role == "user" else "🤖 **Assistant**"
            out.write(f"### {badge} <small>· {ts}</small>\n\n")
            out.write(body)
            out.write("\n\n---\n\n")

    print(f"✅ 已导出: {output_path}")
    print(f"   消息数: {len(entries)},大小: {output_path.stat().st_size // 1024} KB")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="仅列出 session,不导出")
    ap.add_argument("--latest", action="store_true", help="自动选最近的 session 导出")
    ap.add_argument("--session", help="指定 session UUID 或文件名")
    ap.add_argument("--output", help="输出 .md 路径(默认放桌面)")
    args = ap.parse_args()

    sessions = list_sessions()
    if not sessions:
        print(f"❌ 没找到 session,目录: {SESSIONS_DIR}")
        sys.exit(1)

    if args.list:
        print_session_list(sessions)
        return

    # Pick which session
    if args.session:
        wanted = args.session.replace(".jsonl", "")
        chosen = next((p for p in sessions if p.stem == wanted), None)
        if chosen is None:
            print(f"❌ 找不到 session `{wanted}`,可用列表:")
            print_session_list(sessions)
            sys.exit(1)
    elif args.latest:
        chosen = sessions[0]
    else:
        # Interactive picker
        print_session_list(sessions)
        print()
        sel = input("请输入要导出的编号(1-N,默认 1):").strip() or "1"
        try:
            chosen = sessions[int(sel) - 1]
        except (ValueError, IndexError):
            print("❌ 编号无效")
            sys.exit(1)

    # Determine output path
    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.fromtimestamp(chosen.stat().st_mtime).strftime("%Y%m%d_%H%M")
        out_path = DEFAULT_OUT_DIR / f"session_{ts}_{chosen.stem[:8]}.md"

    print(f"导出: {chosen.name} → {out_path}")
    export(chosen, out_path)


if __name__ == "__main__":
    main()
