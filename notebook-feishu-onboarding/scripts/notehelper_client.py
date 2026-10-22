#!/usr/bin/env python3
"""Small client for the endpoints published in the official NoteHelper skill.

No dependency beyond Python 3.8+. No Feishu token handling or background jobs.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


BASE = "https://claw.notebooksyncer.com"
DEFAULT_CONFIG = Path.home() / ".openclaw" / "notehelper.json"
UA = "notebook-feishu-onboarding/0.6"
DEFAULT_WORK_ROOT = Path.home() / ".openclaw" / "notehelper-work"


class ClientError(Exception):
    pass


class SelectionError(ClientError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward the private x-api-key header to a redirected host.
        raise ClientError("接口要求重定向；请先核对官方新地址，未转发密钥。")


def output(value, path=None):
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path is None:
        print(text, end="")
        return
    target = Path(path)
    package_root = Path(__file__).resolve().parents[1]
    if package_root in target.resolve().parents:
        raise ClientError("资料不能导出到 skill 包内，请先运行 workspace 并切换到返回的目录。")
    # Exclusive creation: never silently replace existing user exports.
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(json.dumps({"ok": True, "output": str(target.resolve())}, ensure_ascii=False))


def load_key(config):
    env_key = os.environ.get("NOTEHELPER_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        value = json.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ClientError("尚未配置个人密钥，请先运行 configure。")
    except (OSError, ValueError):
        raise ClientError("无法读取密钥配置；请检查本机文件，不要发送文件正文。")
    key = value.get("api_key") if isinstance(value, dict) else None
    if not isinstance(key, str) or not key.strip():
        raise ClientError("配置文件中没有有效 api_key。")
    return key.strip()


def request(path, key, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(BASE + path, data=body, headers={
        "x-api-key": key, "User-Agent": UA,
        "Content-Type": "application/json", "Accept": "application/json",
    })
    try:
        with build_opener(NoRedirect()).open(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        messages = {
            401: "密钥验证未通过；请核对本人的密钥。",
            402: "权益或积分不足，请由用户自行查看官方页面。",
            403: "访问被拒；已设置自定义 UA，请核对官方接入要求与网络。",
            429: "请求过于频繁，请稍后查询。",
        }
        raise ClientError("HTTP {}：{} 提交失败/超时时不要直接重复提交链接。".format(
            exc.code, messages.get(exc.code, "服务响应异常。")))
    except (URLError, TimeoutError, OSError):
        raise ClientError("网络请求未完成；提交结果可能不明，先查云端，勿自动重提。")
    except ValueError:
        raise ClientError("服务返回非 JSON；请检查官方服务状态。")
    if not isinstance(result, dict) or result.get("errors") or result.get("ok") is False:
        raise ClientError("服务返回错误或未知结构；未判为成功。")
    return result


def prepare_workspace(root=DEFAULT_WORK_ROOT):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="run-", dir=str(root)))
    output({"ok": True, "workspace": str(workspace.resolve()),
            "next": "由 Agent 切换到此目录；脚本使用绝对路径调用。"})


def configure(config, replace, from_stdin=False):
    existing = {}
    if config.exists():
        if not replace:
            raise ClientError("配置文件已存在，默认保留。仅在用户确认换密钥后使用 --replace。")
        existing = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ClientError("已有配置不是 JSON 对象，未覆盖。")
    if from_stdin:
        if sys.stdin.isatty():
            raise ClientError("--stdin 需要工具提供的非交互输入流；PTY 请用 configure 的隐藏提示。")
        key = sys.stdin.readline().strip()
    else:
        if not sys.stdin.isatty():
            raise ClientError("Agent 可用 configure --stdin 传入已获授权的密钥，或准备本机隐藏输入入口。")
        key = getpass.getpass("请输入你自己的笔记同步助手 API 密钥（不显示）：").strip()
    if not key or any(char.isspace() for char in key):
        raise ClientError("密钥为空或包含空白，未保存。")
    config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing["api_key"] = key
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    fd = os.open(str(config), flags, 0o600)
    if os.name != "nt":
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    output({"ok": True, "configured": True, "path": str(config),
            "next": "check（尚未验证远端连通）"})


def search(key, query, limit, after, content, note_id=None):
    if content and (not note_id or limit != 1):
        raise ClientError("读取正文需指定 --id 和 --limit 1，并用元信息中的 result_after 设置 --after。")
    fields = "id title url author savedAt" + (" content" if content else "")
    # Use the exact variables-based convention published by the official skill.
    document = ('query { search(query: "", first: 10, after: 0) { edges { node { '
                + fields + ' } } pageInfo { totalCount hasNextPage } } }')
    result = request("/api/graphql", key, {"query": document, "variables": {
        "query": query, "first": limit, "after": after}})
    connection = result
    if "edges" not in connection:
        data = result.get("data")
        connection = data.get("search", data) if isinstance(data, dict) else {}
    edges = connection.get("edges") if isinstance(connection, dict) else None
    if not isinstance(edges, list):
        raise ClientError("搜索响应缺少 edges，未判为 0 篇或成功。")
    if content and (len(edges) != 1 or not isinstance(edges[0], dict)
                    or not isinstance(edges[0].get("node"), dict)
                    or edges[0]["node"].get("id") != note_id):
        raise SelectionError("正文结果与选定 ID 不一致，未输出或导出；请重新只读检索并确认位置，不扩大正文范围。")
    articles = []
    for index, edge in enumerate(edges):
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict) or not node.get("id"):
            raise ClientError("笔记响应缺少 ID，未输出不完整结果。")
        article = {field: node.get(field) for field in fields.split()}
        if content:
            article["content_available"] = bool(str(node.get("content") or "").strip())
        else:
            article["result_after"] = after + index
        articles.append(article)
    page = connection.get("pageInfo") or {}
    return {"ok": True, "articles": articles, "pageInfo": page,
            "next_after": after + len(articles) if not content and page.get("hasNextPage") else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="个人密钥文件；默认使用官方 NoteHelper 约定路径")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="只检查本机工具是否存在；不读密钥、不联网")
    commands.add_parser("workspace", help="由 Agent 创建独立工作目录，不写入 skill 包")
    setup = commands.add_parser("configure", help="Agent 接收标准输入，或使用交互终端隐藏提示")
    setup.add_argument("--replace", action="store_true", help="用户确认后更换已有密钥")
    setup.add_argument("--stdin", action="store_true", help="从工具标准输入读取一行个人密钥，不回显")
    commands.add_parser("check", help="只读验证密钥和文章计数")
    find = commands.add_parser("search", help="只读检索已保存笔记")
    find.add_argument("--query", default="")
    find.add_argument("--limit", type=int, default=10)
    find.add_argument("--after", type=int, default=0)
    find.add_argument("--content", action="store_true")
    find.add_argument("--id", dest="note_id", help="读取正文时必须为用户选定的笔记 ID；结果不符即停止")
    find.add_argument("--output", help="保存检索结果到新文件；可能含私人正文")
    send = commands.add_parser("submit", help="提交新链接，会使用用户的积分；不自动重试")
    send.add_argument("url")
    progress = commands.add_parser("progress", help="单次查询已提交任务；不会重新提交")
    progress.add_argument("task_id")
    args = parser.parse_args()

    if args.command == "doctor":
        output({"ok": True, "platform": platform.system(),
                "python": platform.python_version(),
                "tools_present": {name: bool(shutil.which(name))
                                  for name in ("node", "npm", "lark-cli")},
                "key_file_exists": args.config.is_file(),
                "key_env_present": bool(os.environ.get("NOTEHELPER_API_KEY")),
                "remote_verified": False})
        return
    if args.command == "configure":
        configure(args.config, args.replace, args.stdin)
        return
    if args.command == "workspace":
        prepare_workspace()
        return
    key = load_key(args.config)
    if args.command == "check":
        result = request("/api/stats/article-count", key)
        data = result.get("data", result)
        count = data.get("count") if isinstance(data, dict) else None
        if type(count) is not int or count < 0:
            raise ClientError("计数响应结构不符，未判为接入成功。")
        output({"ok": True, "key_verified": True, "article_count": count})
    elif args.command == "search":
        if not 1 <= args.limit <= 50 or args.after < 0:
            raise ClientError("limit 需为 1–50，after 不能为负。")
        output(search(key, args.query, args.limit, args.after, args.content, args.note_id), args.output)
    elif args.command == "submit":
        parsed = urlsplit(args.url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username:
            raise ClientError("请提供有效的 http/https 链接，不要带账号凭证。")
        result = request("/note/save", key, {"note_type": "link", "link_url": args.url})
        tasks = (result.get("data") or {}).get("tasks") or []
        if not tasks or not isinstance(tasks[0], dict) or not tasks[0].get("task_id"):
            raise ClientError("未取得 task_id，提交结果不明；请先查询云端，勿直接重提。")
        output({"ok": True, "submitted": True, "task_id": tasks[0]["task_id"],
                "message": "任务已接收，尚未解析完成；请保存 task_id 后查询 progress。"})
    elif args.command == "progress":
        result = request("/note/task/progress", key, {"task_id": args.task_id})
        data = result.get("data") or {}
        tasks = data.get("tasks") if isinstance(data, dict) else None
        task = tasks[0] if isinstance(tasks, list) and tasks else data
        if not isinstance(task, dict) or task.get("status") not in (
                "pending", "processing", "success", "failed", "error"):
            raise ClientError("任务状态结构未知，未判为解析成功。")
        # Deliberately avoid dumping service errors or arbitrary returned content.
        output({"ok": True, "task_id": args.task_id,
                "status": task["status"], "note_id": task.get("note_id"),
                "title": task.get("title"), "feishu_written": False})


if __name__ == "__main__":
    try:
        main()
    except (ClientError, OSError, ValueError, EOFError):
        exc = sys.exc_info()[1]
        # Only controlled error messages may be returned; never leak a raw key/body.
        message = str(exc) if isinstance(exc, ClientError) else "本机输入/文件操作失败；未输出私人内容，请检查路径和权限。"
        output({"ok": False, "error": message})
        sys.exit(1)
