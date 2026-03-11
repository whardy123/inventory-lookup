import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
import subprocess

import pyodbc


DEFAULT_CONFIG_FILE = "inventory_config.json"
DEFAULT_DB_PATH = r"\\main\Office Files\Vagabond Group\VHData.accdb"
DEFAULT_OUTPUT_FILES = ["Inventory_Mobile.html", "index.html"]
DEFAULT_LOG_FILE = "inventory_update.log"
DEFAULT_GIT_REMOTE = "origin"
DEFAULT_GIT_BRANCH = "main"
DEFAULT_GIT_COMMIT_MESSAGE = "Update inventory HTML"
TABLE_NAME = "Table ProductInventory"
FIELD_CODE = "ProductCode"
FIELD_NAME = "DES"
FIELD_QTY = "InStock"
FIELD_LOCATION = "Location"
FIELD_RETIRED = "Retired"
FILTER_FIELD = "OnDropdown"
FILTER_VALUE = True
ORDER_FIELD = "ProductCode"


def bracket(identifier: str) -> str:
    safe = identifier.replace("]", "]]")
    return f"[{safe}]"


def to_int(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def quantity_class(qty: int) -> str:
    if qty <= 10:
        return "low"
    if qty <= 49:
        return "medium"
    return ""


def build_item_html(code: str, name: str, qty: int, location: str, retired) -> str:
    code_text = html.escape(code or "")
    name_text = html.escape(name or "")
    location_text = html.escape(location or "")
    search_text = html.escape(
        f"{code_text} {name_text} {location_text}".lower().strip(),
        quote=True,
    )
    retired_flag = "true" if bool(retired) else "false"
    retired_badge = " <span class=\"retired\">Retired</span>" if retired_flag == "true" else ""
    qty_value = to_int(qty)
    qty_class = quantity_class(qty_value)
    qty_class_attr = f" {qty_class}" if qty_class else ""
    zero_retired_class = " zero-retired" if retired_flag == "true" and qty_value == 0 else ""

    lines = [
        "        <div class=\"inventory-item" + zero_retired_class + "\" data-search=\"" + search_text + "\" data-retired=\"" + retired_flag + "\">",
        f"            <div class=\"product-code\">{code_text}{retired_badge}</div>",
        f"            <div class=\"product-name\">{name_text}</div>",
        "            <div class=\"details\">",
        f"                <span class=\"quantity{qty_class_attr}\">Qty: {qty_value}</span>",
        f"                <span class=\"location\">{location_text}</span>",
        "            </div>",
        "        </div>",
    ]
    return "\n".join(lines)


def build_items_block(rows) -> str:
    items = [build_item_html(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    if not items:
        return ""
    return "\n\n" + "\n\n".join(items) + "\n"


def update_html(output_path: Path, items_html: str, total_items: int) -> None:
    html_text = output_path.read_text(encoding="utf-8")

    updated_text = dt.datetime.now().strftime("Updated: %B %d, %Y at %I:%M %p")
    html_text = re.sub(
        r"(<div class=\"updated\">)Updated:.*?(</div>)",
        r"\g<1>" + updated_text + r"\g<2>",
        html_text,
        flags=re.IGNORECASE,
    )

    html_text = re.sub(
        r"(<span id=\"resultCount\">)\d+(</span>)",
        r"\g<1>" + str(total_items) + r"\g<2>",
        html_text,
    )

    pattern = re.compile(
        r"(<div class=\"inventory-list\" id=\"inventoryList\">\s*)(.*?)(\s*</div>\s*<div class=\"no-results\" id=\"noResults\">)",
        re.DOTALL,
    )
    match = pattern.search(html_text)
    if not match:
        raise RuntimeError("Could not find inventory list section in HTML.")

    html_text = pattern.sub(r"\1" + items_html + r"\3", html_text)

    output_path.write_text(html_text, encoding="utf-8")


def load_config(config_path: Path) -> dict:
    config = {
        "db_path": DEFAULT_DB_PATH,
        "output_files": DEFAULT_OUTPUT_FILES,
        "log_file": DEFAULT_LOG_FILE,
        "git": {
            "enabled": False,
            "remote": DEFAULT_GIT_REMOTE,
            "branch": DEFAULT_GIT_BRANCH,
            "commit_message": DEFAULT_GIT_COMMIT_MESSAGE,
            "add": [],
        },
        "table": TABLE_NAME,
        "fields": {
            "code": FIELD_CODE,
            "name": FIELD_NAME,
            "qty": FIELD_QTY,
            "location": FIELD_LOCATION,
            "retired": FIELD_RETIRED,
        },
        "filter": {"field": FILTER_FIELD, "value": FILTER_VALUE},
        "order_by": ORDER_FIELD,
    }

    if not config_path.exists():
        return config

    loaded = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(loaded)
    fields = config.get("fields") or {}
    config["fields"] = {
        "code": fields.get("code", FIELD_CODE),
        "name": fields.get("name", FIELD_NAME),
        "qty": fields.get("qty", FIELD_QTY),
        "location": fields.get("location", FIELD_LOCATION),
        "retired": fields.get("retired", FIELD_RETIRED),
    }
    filter_cfg = config.get("filter") or {}
    config["filter"] = {
        "field": filter_cfg.get("field", FILTER_FIELD),
        "value": filter_cfg.get("value", FILTER_VALUE),
    }
    git_cfg = config.get("git") or {}
    config["git"] = {
        "enabled": bool(git_cfg.get("enabled", False)),
        "remote": git_cfg.get("remote", DEFAULT_GIT_REMOTE),
        "branch": git_cfg.get("branch", DEFAULT_GIT_BRANCH),
        "commit_message": git_cfg.get("commit_message", DEFAULT_GIT_COMMIT_MESSAGE),
        "add": git_cfg.get("add", []),
    }
    if isinstance(config.get("output_files"), str):
        config["output_files"] = [config["output_files"]]
    return config


def write_log(log_path: Path, total_items: int, output_paths) -> None:
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    outputs = ", ".join(str(p) for p in output_paths)
    line = f"{timestamp} | items={total_items} | outputs={outputs}\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(line, encoding="utf-8")


def print_summary(total_items: int, output_paths) -> None:
    outputs = ", ".join(str(p) for p in output_paths)
    print(f"Updated {total_items} items -> {outputs}")


def run_git(args, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        check=check,
        capture_output=True,
        text=True,
    )


def git_commit_and_push(
    repo_root: Path,
    paths_to_add,
    commit_message: str,
    remote: str,
    branch: str,
) -> None:
    if paths_to_add:
        run_git(["add"] + [str(p) for p in paths_to_add], repo_root)
    else:
        run_git(["add", "-A"], repo_root)

    status = run_git(["status", "--porcelain"], repo_root).stdout.strip()
    if not status:
        print("No git changes to commit.")
        return

    run_git(["commit", "-m", commit_message], repo_root)
    push_result = run_git(["push", remote, branch], repo_root, check=False)
    if push_result.returncode != 0:
        if push_result.stderr:
            print(push_result.stderr.strip())
        if push_result.stdout:
            print(push_result.stdout.strip())
        print(
            f"git command failed: git push {remote} {branch} (exit {push_result.returncode})"
        )
        raise SystemExit(push_result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update Inventory_Mobile.html from Access inventory data."
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_FILE, help="Path to config JSON"
    )
    parser.add_argument("--db", default=None, help="Path to .accdb/.mdb")
    parser.add_argument(
        "--output",
        action="append",
        help="Path to HTML output (repeatable)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    config = load_config(config_path)

    db_path = Path(args.db or config["db_path"])
    output_files = args.output or config["output_files"]
    output_paths = [Path(p) for p in output_files]
    table_name = config["table"]
    fields = config["fields"]
    filter_field = config["filter"]["field"]
    filter_value = config["filter"]["value"]
    order_field = config.get("order_by")
    log_file = config.get("log_file")
    git_cfg = config.get("git") or {}

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    for output_path in output_paths:
        if not output_path.exists():
            raise FileNotFoundError(f"HTML not found: {output_path}")

    conn_str = (
        "Driver={Microsoft Access Driver (*.mdb, *.accdb)};"
        f"DBQ={db_path};"
    )

    query = (
        "SELECT "
        f"{bracket(fields['code'])}, {bracket(fields['name'])}, {bracket(fields['qty'])}, "
        f"{bracket(fields['location'])}, {bracket(fields['retired'])} "
        f"FROM {bracket(table_name)} "
        f"WHERE {bracket(filter_field)} = ?"
    )
    if order_field:
        query += f" ORDER BY {bracket(order_field)}"

    with pyodbc.connect(conn_str) as conn:
        cursor = conn.cursor()
        rows = cursor.execute(query, filter_value).fetchall()

    items_html = build_items_block(rows)
    for output_path in output_paths:
        update_html(output_path, items_html, len(rows))

    if log_file:
        write_log(Path(log_file), len(rows), output_paths)

    print_summary(len(rows), output_paths)

    if git_cfg.get("enabled"):
        print(
            f"Git push target: {git_cfg.get('remote', DEFAULT_GIT_REMOTE)} {git_cfg.get('branch', DEFAULT_GIT_BRANCH)}"
        )
        add_paths = [Path(p) for p in (git_cfg.get("add") or [])]
        if not add_paths:
            add_paths = output_paths + [Path(log_file)] if log_file else output_paths
        git_commit_and_push(
            repo_root=Path.cwd(),
            paths_to_add=add_paths,
            commit_message=git_cfg.get("commit_message", DEFAULT_GIT_COMMIT_MESSAGE),
            remote=git_cfg.get("remote", DEFAULT_GIT_REMOTE),
            branch=git_cfg.get("branch", DEFAULT_GIT_BRANCH),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
