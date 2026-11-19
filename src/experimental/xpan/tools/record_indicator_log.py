"""Decode the R5T1 record-indicator counter rows from a KUWA RAM export (DbgRA*.bin).

The row formats and argument names are read from rec_tone.h (REC_TONE_REPORT_ROWS),
the single owner of their text. Rows are only emitted by builds made with
--record-indicator and/or --record-stop-indicator; a group is bound to one build by its 16-hex source id.
Counters describe what the worker observed; they do not prove what was audible.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from ram_capture_log import decode_ram_records, record_partition

HEADER = Path(__file__).resolve().parent.parent / "rec_tone.h"
FAULTS = {0: "none", 1: "generation", 2: "descriptor", 3: "observe_timeout", 4: "api"}


def report_rows(header: str | None = None) -> list[tuple[str, str, list[str]]]:
    """(row, format, argument names) in emission order, from rec_tone.h."""
    text = HEADER.read_text() if header is None else header
    formats = dict(re.findall(r'^#define (RT_FMT_[A-Z_]+) (.+)$', text, re.M))
    table = re.search(r"#define REC_TONE_REPORT_ROWS\(X\)((?:.*\\\n)*.*\n)", text)
    if table is None:
        raise ValueError("REC_TONE_REPORT_ROWS missing from rec_tone.h")
    rows = []
    for name, fmt_name, args in re.findall(r'X\((\w+), (RT_FMT_[A-Z_]+), "([^"]*)"\)', table.group(1)):
        fmt = formats[fmt_name].replace('" OBSERVER_SOURCE_ID "', "@SOURCE@")
        if not (fmt.startswith('"') and fmt.endswith('"')):
            raise ValueError("unsupported row format " + fmt_name)
        rows.append((name, fmt[1:-1], args.split()))
    return rows


def _values(record: dict, names: list[str]) -> dict:
    numeric = record["numeric"]
    if numeric["status"] not in ("decoded_scalar32", "no_numeric_arguments") or len(numeric["arguments"]) != len(names):
        raise ValueError("malformed packed arguments")
    return {
        n: a.get("signed_i32", a["raw_u32"]) for n, a in zip(names, numeric["arguments"])
    }


def decode_records(parsed: dict, source_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", source_id):
        raise ValueError("source_id must be 16 lowercase hex characters")
    rows = report_rows()
    formats = [fmt.replace("@SOURCE@", source_id) for _, fmt, _ in rows]
    records = [r for r in parsed["records"] if r["text"].startswith("R5T1 ")]
    groups, rejected, pending, foreign = [], [], [], []
    for record in records:
        if record["text"].startswith("R5T1 S=") and record["text"] != formats[0]:
            foreign.append(record["offset"])
            pending = []
            continue
        if record["text"] == formats[0]:
            if pending:
                rejected.append({"offset": pending[0]["offset"], "reason": "incomplete group before next begin"})
            pending = [record]
            continue
        if not pending:
            continue
        pending.append(record)
        if len(pending) < len(rows):
            continue
        try:
            if [r["text"] for r in pending] != formats:
                raise ValueError("row order or format differs from rec_tone.h")
            record_partition(pending)
            values = {}
            for (name, _, names), row in zip(rows, pending):
                values[name] = _values(row, names)
            if values["begin"]["report"] != values["end"]["report"]:
                raise ValueError("begin/end report sequence mismatch")
            counts = {k: v for name, _, _ in rows if name not in ("begin", "end") for k, v in values[name].items()}
            counts.update(ready=values["begin"]["ready"], fault=values["begin"]["fault"])
            groups.append({
                "report": values["begin"]["report"],
                "partition_index": pending[0]["partition_index"],
                "record_offsets": [r["offset"] for r in pending],
                "counters": counts,
                "fault": FAULTS.get(counts["fault"], "unknown"),
            })
        except (ValueError, KeyError) as error:
            rejected.append({"offset": pending[0]["offset"], "reason": str(error)})
        pending = []
    if pending:
        rejected.append({"offset": pending[0]["offset"], "reason": "incomplete group at end of capture"})
    return {
        "protocol": "R5T1",
        "source_id": source_id,
        "groups": groups,
        "rejected_groups": rejected,
        "other_source_offsets": foreign,
        "limitations": [
            "Counters are worker observations; they do not prove the cue was audible or present in a clip.",
            "Physical record order is not chronology; compare groups by report sequence.",
        ],
    }


def decode(data: bytes, source_id: str) -> dict:
    return decode_records(decode_ram_records(data), source_id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Decode R5T1 record-indicator counters from a KUWA RAM export.")
    ap.add_argument("export", type=Path, help="DbgRA*.bin KUWA RAM export (262,144 bytes)")
    ap.add_argument("--build", type=Path, required=True, help="Build directory whose build.json produced the image")
    args = ap.parse_args(argv)
    build = json.loads((args.build / "build.json").read_text())
    if not build.get("record_indicator"):
        ap.error("this build was made without --record-indicator or --record-stop-indicator; it emits no R5T1 rows")
    print(json.dumps(decode(args.export.read_bytes(), build["observer"]["source_id"]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
