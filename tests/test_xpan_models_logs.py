"""Portable host model export and KUWA diagnostic decoder tests (no private assets)."""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
XPAN_TOOLS = ROOT / "src" / "experimental" / "xpan" / "tools"
sys.path.insert(0, str(XPAN_TOOLS))

import disable_model
import framing_model
import playback_log
import playback_model
import q_label_model
from ram_capture_log import HEADER_BYTES, RAM_BYTES, RECORD_BYTES, decode_ram_records


def _pack_record(counter: int, timer: int, fmt: str, args: list[int], *, cls: int = 0x84, lvl: int = 3) -> bytes:
    buf = bytearray(RECORD_BYTES)
    struct.pack_into("<II", buf, 0, counter, timer)
    buf[8] = 0xFF
    buf[9] = cls
    buf[10] = lvl
    encoded = fmt.encode("ascii") + b"\0"
    buf[11 : 11 + len(encoded)] = encoded
    for idx, val in enumerate(args):
        struct.pack_into("<I", buf, RECORD_BYTES - 4 - idx * 4, val)
    return bytes(buf)


def _pack_kuwa(records: list[bytes]) -> bytes:
    buf = bytearray(b"\xff" * RAM_BYTES)
    p0_off = HEADER_BYTES
    p0_cap = 1559
    p0_ext = p0_cap * RECORD_BYTES
    p1_off = p0_off + p0_ext
    p1_ext = RAM_BYTES - p1_off
    p1_cap = p1_ext // RECORD_BYTES
    struct.pack_into("<4sI", buf, 0, b"KUWA", len(records) + 1)
    struct.pack_into("<5I", buf, 8, p0_off, p0_ext, p0_cap, 1, len(records) + 1)
    struct.pack_into("<5I", buf, 8 + 60, p1_off, p1_ext, p1_cap, 1, 1)
    for idx, rec in enumerate(records):
        at = p0_off + idx * RECORD_BYTES
        buf[at : at + RECORD_BYTES] = rec
    return bytes(buf)


class XPanModelsAndLogsTests(unittest.TestCase):
    def test_host_c_models_export_and_classify_without_private_assets(self):
        source_id = "0123456789abcdef"
        fm = framing_model.export_model(source_id)
        self.assertEqual(fm["schema"], "canon-r5-lab.framing-c-model.v1")
        self.assertEqual(len(fm["lcd_rows"]), 2)

        pm = playback_model.export_model(source_id)
        self.assertEqual(pm["layouts"]["view_owner_bytes"], 40)
        self.assertEqual(pm["first_view"]["profile"]["reason_count"], 67)
        self.assertIn("PFV_R_GUIDE_DEVICE_MISMATCH", pm["first_view"]["reasons"])
        self.assertIn("PFV_R_GUIDE_ORIENTATION_MISMATCH", pm["first_view"]["reasons"])

        res_raw = playback_model.classify([7, 8192, 3024, 0, 1220, 8192, 5464], playback_model.model_digests())
        self.assertEqual(res_raw["ratio"], [512, 189])
        self.assertEqual(res_raw["kind_code"], pm["box_kinds"]["PB_BOX_RAW_L"])

        qm = q_label_model.export_model()
        self.assertEqual(qm["profile"]["values"]["item_key"], 52)
        self.assertEqual(qm["text"]["bytes"], 5)

        dm = disable_model.export_model(source_id)
        self.assertEqual(dm["profile"]["reason_count"], 40)

    def test_playback_first_view_67_reason_kuwa_roundtrip(self):
        source_id = "0123456789abcdef"
        pm = playback_model.export_model(source_id)
        fv = pm["first_view"]
        fmt = fv["formats"]
        contract = {
            "source_id": source_id,
            "playback_model": pm,
            "playback_sources": playback_model.model_digests(),
        }

        # Synthesize R5V1 group reporting PFV_R_GUIDE_DEVICE_MISMATCH (47) and PFV_R_GUIDE_ORIENTATION_MISMATCH (48)
        rows = [
            fmt["begin"],
            fmt["source"],
        ]
        raw_recs = [
            _pack_record(1, 0x100, fmt["begin"], [1, fv["profile"]["schema"], fv["profile"]["words"]]),
            _pack_record(2, 0x101, fmt["source"], []),
        ]
        ctr = 3
        reasons_by_role = {
            fv["roles"]["PFV_GUIDE_FIRST_HIDDEN"]: fv["reasons"]["PFV_R_GUIDE_DEVICE_MISMATCH"],
            fv["roles"]["PFV_GUIDE_RELEVANT_REJECT"]: fv["reasons"]["PFV_R_GUIDE_ORIENTATION_MISMATCH"],
        }
        for role in range(fv["profile"]["roles"]):
            if role in reasons_by_role:
                raw_recs.append(_pack_record(ctr, 0x100 + ctr, fmt["role"], [role, fv["states"]["PFV_STATE_READY"], 1]))
                ctr += 1
                words = [0] * fv["profile"]["words"]
                words[fv["fields"]["reason"]["word"]] = reasons_by_role[role]
                words[fv["fields"]["site"]["word"]] = fv["sites"]["PFV_SITE_GUIDE"]
                words[fv["fields"]["known_mask"]["word"]] = (
                    fv["fields"]["reason"]["known_bit"] | fv["fields"]["site"]["known_bit"]
                )
                for idx in range(0, fv["profile"]["words"], 4):
                    raw_recs.append(_pack_record(ctr, 0x100 + ctr, fmt["words"], [role, idx, *words[idx : idx + 4]]))
                    ctr += 1
            else:
                raw_recs.append(_pack_record(ctr, 0x100 + ctr, fmt["role"], [role, fv["states"]["PFV_STATE_EMPTY"], 0]))
                ctr += 1
        raw_recs.append(_pack_record(ctr, 0x100 + ctr, fmt["end"], [1]))

        parsed = decode_ram_records(_pack_kuwa(raw_recs))
        decoded = playback_log._decode_first_view_records(parsed, contract, pm)
        self.assertEqual(decoded["complete_groups"], 1)
        self.assertEqual(decoded["rejected_groups"], [])
        cohorts = {c["role"]: c for c in decoded["groups"][0]["cohorts"]}
        self.assertEqual(cohorts["PFV_GUIDE_FIRST_HIDDEN"]["reason"], "PFV_R_GUIDE_DEVICE_MISMATCH")
        self.assertEqual(cohorts["PFV_GUIDE_RELEVANT_REJECT"]["reason"], "PFV_R_GUIDE_ORIENTATION_MISMATCH")


if __name__ == "__main__":
    unittest.main()
