"""Phase 0 exit criteria: a true contact count and a false-merge rate.

Aggregates go to stdout. Anything containing a real destination goes to the
detail file, and phone numbers are masked there too — the point of the report
is to judge merges, not to make a second copy of the address book.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from resolve import resolve
from sources import load_abook, load_blueferry

DETAIL_PATH = Path(__file__).resolve().parent / "merge-report.txt"


def mask(key: str) -> str:
    scheme, _, value = key.partition(":")
    if scheme == "tel":
        return f"tel:***{value[-4:]}" if len(value) > 4 else "tel:***"
    local, _, domain = value.partition("@")
    return f"mailto:{local[:2]}***@{domain}"


def main() -> int:
    records = []
    for label, loader in (("abook", load_abook), ("blueferry", load_blueferry)):
        try:
            loaded = loader()
        except Exception as error:  # a spike reports what it could not read
            print(f"  {label:<12} FAILED: {type(error).__name__}: {error}")
            continue
        records.extend(loaded)
        print(f"  {label:<12} {len(loaded):>5} records")

    if not records:
        print("no sources readable")
        return 1

    clusters, stats = resolve(records)
    multi = [cluster for cluster in clusters if len(cluster.records) > 1]
    cross = [cluster for cluster in multi if len(cluster.sources) > 1]
    suspicious = [cluster for cluster in multi if cluster.names_disagree()]
    sizes = Counter(len(cluster.records) for cluster in clusters)

    print(f"\n  raw records          {stats['raw_records']:>5}")
    print(f"  distinct people      {len(clusters):>5}")
    print(f"  collapsed by merging {stats['raw_records'] - len(clusters):>5}")
    print(f"  merged clusters      {len(multi):>5}")
    print(f"    cross-source       {len(cross):>5}  (in both abook and BlueFerry)")
    print(f"    name disagreement  {len(suspicious):>5}  <-- inspect these")
    print(f"  hub keys excluded    {stats['hubs_excluded']:>5}  (shared lines)")
    rate = len(suspicious) / len(multi) * 100 if multi else 0.0
    print(f"\n  false-merge suspicion rate: {rate:.1f}% of merged clusters")
    print("  cluster sizes: " + ", ".join(
        f"{size}x{count}" for size, count in sorted(sizes.items())
    ))

    with DETAIL_PATH.open("w", encoding="utf-8") as stream:
        stream.write("# Clusters whose names disagree - likely false merges\n\n")
        for cluster in suspicious:
            stream.write(f"names: {' | '.join(cluster.names)}\n")
            stream.write(f"  linked by: {', '.join(sorted(map(mask, cluster.keys)))}\n")
            stream.write(f"  sources:   {', '.join(sorted(cluster.sources))}\n\n")
        stream.write("\n# Hub keys excluded from linking\n\n")
        for key, count, names in stats["hub_detail"]:
            stream.write(f"{mask(key)}  {count} records: {', '.join(names)}\n")
        stream.write("\n# Largest clusters\n\n")
        for cluster in clusters[:20]:
            stream.write(
                f"{len(cluster.records):>3} records  {' | '.join(cluster.names[:4])}\n"
            )

    print(f"\n  detail written to {DETAIL_PATH}")
    json.dump(
        {"stats": stats, "clusters": len(clusters), "suspicious": len(suspicious)},
        (DETAIL_PATH.with_suffix(".json")).open("w"),
        indent=2,
        default=str,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
