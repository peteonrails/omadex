"""omadex — diagnostics and the operations the overlay will call.

Works directly on the store, so it is usable before the daemon exists and
after it dies. Commands that change decisions re-sync in place.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from omadex import config as config_module
from omadex.config import (
    DESCRIPTIONS,
    FIELDS,
    LABELS,
    label,
    source_for_label,
)
from omadex.engine import sync
from omadex.launch import LaunchError, open_source, target_for
from omadex.limits import MAX_PAGE, MAX_REVIEW_ITEMS
from omadex.models import VERDICT_DISTINCT, VERDICT_SAME, Override
from omadex.readiness import check_all, needs_onboarding
from omadex.store import open_store


def _show(identity: dict) -> str:
    addresses = identity["emails"] + identity["phones"]
    trailer = f"  [{', '.join(label(s) for s in identity['sources'])}]"
    shown = ", ".join(a.partition(":")[2] for a in addresses[:3])
    return f"{identity['name']:<32} {shown}{trailer}"


def _emit_json(store, args) -> int:
    """One JSON object per invocation, for callers that are not humans.

    The QML overlay runs this: Quickshell has no generic D-Bus client, only
    DBusMenu and a fixed set of services, so the plugin talks to OmaDex by
    spawning the CLI and parsing stdout.
    """
    if args.command == "search":
        payload = {"results": store.search(" ".join(args.query))}
    elif args.command == "list":
        limit = min(args.limit, MAX_PAGE)
        payload = {"results": (
            store.list_by_source(args.source, args.offset, limit)
            if getattr(args, "source", None) else store.list(args.offset, limit)
        )}
    elif args.command == "show":
        found = store.get(Override.normalize_handle(args.address) or args.address)
        payload = {"result": found, "records": (
            [record.to_dict() for record in store.records_for(found["key"])]
            if found else []
        )}
    elif args.command == "review":
        payload = {"results": store.review_items(args.limit)}
    elif args.command == "doctor":
        checks = check_all()
        payload = {"checks": [check.to_dict() for check in checks],
                   "onboarding": needs_onboarding(checks)}
    elif args.command == "sources":
        settings = config_module.load()
        counts = store.source_counts()
        state = {check.source: check for check in check_all(settings)}
        payload = {"sources": [
            {
                "key": name,
                "label": label(name),
                "description": DESCRIPTIONS.get(name, ""),
                "enabled": settings.enabled(name),
                "records": counts.get(name, (0, 0))[0],
                "people": counts.get(name, (0, 0))[1],
                "state": state[name].state if name in state else "missing",
                "detail": state[name].detail if name in state else "",
                "hint": state[name].hint if name in state else "",
                "fields": [
                    {"key": key, "title": title,
                     "value": str(settings.option(name, key, ""))}
                    for key, title in FIELDS.get(name, [])
                ],
            }
            for name in sorted(config_module.DEFAULTS["sources"])
        ], "store": str(settings.store_path)}
    elif args.command == "stats":
        payload = store.counts()
    elif args.command == "sync":
        payload = sync(store).to_dict()
    else:
        payload = {"error": f"{args.command} has no json form"}
    if isinstance(payload, dict):
        payload = {**payload, "labels": LABELS}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if "error" not in payload else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omadex", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="rebuild from every source")
    sub.add_parser("stats", help="show counts")
    sub.add_parser("doctor", help="check that each source is usable")
    plugin = sub.add_parser("plugin", help="install the Omarchy overlay")
    plugin.add_argument("action", nargs="?", default="install",
                        choices=("install", "remove"))
    search = sub.add_parser("search", help="find people")
    search.add_argument("query", nargs="+")
    show = sub.add_parser("show", help="show one person by address")
    show.add_argument("address")
    show.add_argument("--records", action="store_true",
                      help="show what each source contributed")
    listing = sub.add_parser("list", help="page through everyone")
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--source", help="only people this source contributed to")
    sources = sub.add_parser("sources", help="list, enable, or configure sources")
    sources.add_argument("action", nargs="?", default="list",
                         choices=("list", "enable", "disable", "set"))
    sources.add_argument("name", nargs="?", help="source key or label")
    sources.add_argument("option", nargs="?", help="option to set, e.g. path")
    sources.add_argument("value", nargs="?", help="new value")

    opener = sub.add_parser("open", help="open a contact in a source's own app")
    opener.add_argument("address")
    opener.add_argument("--source", help="which source's application to open")
    opener.add_argument("--dry-run", action="store_true",
                        help="print the command instead of running it")
    review = sub.add_parser("review", help="merges held back for a human")
    review.add_argument("--limit", type=int, default=MAX_REVIEW_ITEMS)
    for name, help_text in (
        ("link", "assert two addresses are the same person"),
        ("unlink", "assert two addresses are different people"),
        ("forget", "drop a previous decision"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("left")
        command.add_argument("right")

    parser.add_argument(
        "--json", action="store_true",
        help="machine-readable output (the overlay reads this)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    with open_store() as store:
        if args.json:
            return _emit_json(store, args)
        if args.command == "sync":
            result = sync(store)
            for source, error in result.errors.items():
                print(f"  {source}: {error}", file=sys.stderr)
            print(
                f"{result.identities} people from {result.records} records "
                f"in {result.elapsed:.2f}s "
                f"({result.review} held for review, {result.hubs} shared lines)"
            )
            if result.conflicts:
                print(
                    f"  warning: {len(result.conflicts)} 'distinct' decisions are "
                    "defeated by another link", file=sys.stderr
                )
            return 0

        if args.command == "plugin":
            # A package cannot write into $HOME, so installing the overlay is
            # a user-level step rather than something the PKGBUILD does.
            source = Path("/usr/share/omadex/plugin")
            if not source.is_dir():
                source = Path(__file__).resolve().parents[2] / "plugin"
            target = (Path(os.environ.get("XDG_CONFIG_HOME",
                                          Path.home() / ".config"))
                      / "omarchy" / "plugins" / "omadex.contacts")
            if args.action == "remove":
                for name in ("manifest.json", "OmaDex.qml"):
                    (target / name).unlink(missing_ok=True)
                target.rmdir() if target.is_dir() else None
                print(f"removed {target}")
            else:
                if not source.is_dir():
                    print(f"plugin files not found at {source}", file=sys.stderr)
                    return 1
                target.mkdir(parents=True, exist_ok=True)
                for name in ("manifest.json", "OmaDex.qml"):
                    shutil.copyfile(source / name, target / name)
                print(f"installed the overlay to {target}")
            subprocess.run(["omarchy-shell", "shell", "rescanPlugins"],
                           capture_output=True, check=False)
            print("run: omarchy plugin enable omadex.contacts")
            return 0

        if args.command == "doctor":
            checks = check_all()
            marks = {"ready": "ok  ", "empty": "----", "missing": "MISS",
                     "blocked": "BLOCK"}
            for check in checks:
                print(f"  {marks.get(check.state, '?'):<6} "
                      f"{label(check.source):<11} {check.detail}")
                if check.hint:
                    print(f"         {check.hint}")
            if needs_onboarding(checks):
                print("\nNo source can supply a contact yet.", file=sys.stderr)
                return 1
            return 0

        if args.command == "sources":
            settings = config_module.load()
            if args.action == "list":
                counts = store.source_counts()
                print(f"  {'source':<12} {'state':<9} {'records':>8} {'people':>7}"
                      f"  settings")
                for name in sorted(config_module.DEFAULTS["sources"]):
                    records, people = counts.get(name, (0, 0))
                    state = "enabled" if settings.enabled(name) else "disabled"
                    options = "  ".join(
                        f"{key}={settings.option(name, key)}"
                        for key, _ in FIELDS.get(name, [])
                    )
                    print(f"  {label(name):<12} {state:<9} {records:>8} "
                          f"{people:>7}  {options}")
                    print(f"  {'':<12} {DESCRIPTIONS.get(name, '')}")
                return 0

            resolved = source_for_label(args.name or "") or args.name
            if resolved not in config_module.DEFAULTS["sources"]:
                print(f"unknown source: {args.name}", file=sys.stderr)
                return 2
            if args.action in ("enable", "disable"):
                config_module.update_source(
                    resolved, enabled=args.action == "enable"
                )
                print(f"{label(resolved)} {args.action}d")
            else:
                if not args.option or args.value is None:
                    print("usage: omadex sources set <name> <option> <value>",
                          file=sys.stderr)
                    return 2
                value: object = args.value
                if args.option == "min_messages":
                    value = int(args.value)
                config_module.update_source(resolved, **{args.option: value})
                print(f"{label(resolved)} {args.option} = {value}")
            result = sync(store)
            print(f"{result.identities} people from {result.records} records")
            return 0

        if args.command == "stats":
            for key, value in store.counts().items():
                print(f"  {key:<12} {value:>6}")
            per_source = store.source_counts()
            if per_source:
                print(f"\n  {'source':<12} {'records':>8} {'people':>8}")
                for name, (records, people) in per_source.items():
                    print(f"  {label(name):<12} {records:>8} {people:>8}")
            return 0

        if args.command == "search":
            found = store.search(" ".join(args.query))
            for identity in found:
                print(_show(identity))
            if not found:
                print("no matches", file=sys.stderr)
                return 1
            return 0

        if args.command == "show":
            identity = store.get(Override.normalize_handle(args.address) or args.address)
            if identity is None:
                print("not found", file=sys.stderr)
                return 1
            print(f"{identity['name']}")
            for kind, values in (("email", identity["emails"]),
                                 ("phone", identity["phones"])):
                for value in values:
                    print(f"  {kind:<6} {value.partition(':')[2]}")
            for address in identity.get("postal", []):
                print(f"  {'postal':<6} {address}")
            print(f"  from   {', '.join(label(s) for s in identity['sources'])}"
                  f" ({identity['record_count']} records)")
            if len(identity["names"]) > 1:
                print(f"  also   {', '.join(identity['names'][1:])}")
            if args.records:
                for record in store.records_for(identity["key"]):
                    addresses = ", ".join(
                        (*record.phones, *record.emails, *record.postal)
                    )
                    print(f"\n  [{label(record.source)}] "
                          f"{record.name or '(no name)'}")
                    if addresses:
                        print(f"      {addresses}")
            return 0

        if args.command == "list":
            limit = min(args.limit, MAX_PAGE)
            if args.source:
                args.source = source_for_label(args.source) or args.source
            found = (
                store.list_by_source(args.source, args.offset, limit)
                if args.source else store.list(args.offset, limit)
            )
            for identity in found:
                print(_show(identity))
            if args.source and not found:
                print(f"no people from {args.source}", file=sys.stderr)
                return 1
            return 0

        if args.command == "review":
            items = store.review_items(args.limit)
            for item in items:
                print(f"{item['left_name']} | {item['right_name']}")
                print(f"    shared {item['shared']}  ({item['reason']})")
                # These are already kept apart, so the actionable choice is to
                # accept the merge. Doing nothing leaves them separate.
                if item["left"] != item["right"]:
                    print(f"    same person?  omadex link {item['left']} {item['right']}")
                else:
                    print("    no distinguishing address; left separate")
            if not items:
                print("nothing held for review")
            return 0

        if args.command == "open":
            settings = config_module.load()
            identity = store.get(
                Override.normalize_handle(args.address) or args.address
            )
            if identity is None:
                print("not found", file=sys.stderr)
                return 1

            # One entry per source, not per record: a contact with three abook
            # rows still has exactly one abook to open.
            by_source: dict = {}
            for record in store.records_for(identity["key"]):
                by_source.setdefault(record.source, record)
            if args.source:
                args.source = source_for_label(args.source) or args.source
            if args.source and args.source not in by_source:
                print(f"no {label(args.source)} record; this contact came "
                      f"from {', '.join(sorted(label(s) for s in by_source))}",
                      file=sys.stderr)
                return 1
            wanted = [args.source] if args.source else list(by_source)

            resolved, problems = [], []
            for source in wanted:
                try:
                    resolved.append(
                        target_for(source, identity,
                                   by_source[source].to_dict(), settings)
                    )
                except LaunchError as error:
                    problems.append((source, str(error)))
            # An application that can jump to the contact beats one that can
            # only be started.
            resolved.sort(key=lambda found: (not found.preloads_contact, found.source))

            if args.dry_run:
                for found in resolved:
                    suffix = "" if found.preloads_contact else "   (opens app only)"
                    print(f"  {label(found.source):<10} "
                          f"{' '.join(found.argv)}{suffix}")
                for source, problem in problems:
                    print(f"  {label(source):<10} unavailable: {problem}",
                          file=sys.stderr)
                return 0

            if not resolved:
                for source, problem in problems:
                    print(f"  {label(source):<10} unavailable: {problem}",
                          file=sys.stderr)
                return 1
            found = open_source(resolved[0].source, identity,
                                by_source[resolved[0].source].to_dict(), settings)
            print(f"opened {DESCRIPTIONS.get(found.source, found.source)}"
                  f"{'' if found.preloads_contact else ' (application only)'}")
            return 0

        left = Override.normalize_handle(args.left)
        right = Override.normalize_handle(args.right)
        if not left or not right or left == right:
            print("two distinct addresses are required", file=sys.stderr)
            return 2
        left, right = sorted((left, right))
        if args.command == "forget":
            if not store.clear_override(left, right):
                print("no such decision", file=sys.stderr)
                return 1
        else:
            store.set_override(Override(
                left, right,
                VERDICT_SAME if args.command == "link" else VERDICT_DISTINCT,
            ))
        result = sync(store)
        print(f"{result.identities} people ({result.review} held for review)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
