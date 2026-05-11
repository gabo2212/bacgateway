import argparse
import csv
import sys
from pathlib import Path
import yaml

def main():
    parser = argparse.ArgumentParser(description="Promote OTA mappings to pointmap.yaml")
    parser.add_argument("--csv", type=Path, help="Path to mapping_report.csv")
    parser.add_argument("--point", type=str, help="Candidate point name to promote")
    parser.add_argument("--status", choices=["confirmed", "candidate"], required=True, help="Status to apply")
    parser.add_argument("--replace", action="store_true", help="Replace existing entry")
    parser.add_argument("--device-label", type=str, default="")
    parser.add_argument("--prefix", type=str)
    parser.add_argument("--write-code", type=str)
    parser.add_argument("--report-code", type=str)
    parser.add_argument("--ack-code", type=str)
    parser.add_argument("--kind", type=str)
    parser.add_argument("--direction", type=str, default="bidirectional")
    parser.add_argument("--evidence", type=str, default="")
    parser.add_argument("--pointmap", type=Path, default=Path(__file__).parent.parent / "gateway/ota/pointmap.yaml")
    
    args = parser.parse_args()

    if args.pointmap.exists():
        with open(args.pointmap, "r") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {"mappings": []}
    if "mappings" not in data:
        data["mappings"] = []
        
    mappings = data["mappings"]

    entries_to_add = []
    
    if args.csv:
        if not args.point:
            print("Error: --point is required when using --csv to select which row to promote", file=sys.stderr)
            sys.exit(1)
        with open(args.csv, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("candidate_point") == args.point:
                    if row.get("decoded_kind") == "unknown":
                        print(f"Error: Cannot promote unknown kind for point {args.point}", file=sys.stderr)
                        sys.exit(1)
                    
                    cmd = int(row.get("cmd", "0"))
                    direction = row.get("direction", "bidirectional")
                    entry = {
                        "device_label": row.get("device_label", ""),
                        "prefix": row.get("prefix"),
                        "canonical_point": args.point,
                        "kind": row.get("decoded_kind"),
                        "direction": direction,
                        "status": args.status,
                        "evidence": row.get("capture_id", "")
                    }
                    if cmd == 2 and direction == "gw->dev":
                        entry["write_code"] = row.get("code")
                    elif cmd == 2 and direction == "dev->gw":
                        entry["report_code"] = row.get("code")
                    elif cmd == 3:
                        entry["ack_code"] = row.get("code")
                    
                    entries_to_add.append(entry)
                    break
        if not entries_to_add:
            print(f"Error: Point {args.point} not found in {args.csv}", file=sys.stderr)
            sys.exit(1)
    else:
        if not args.point or not args.prefix or not args.kind:
            print("Error: --point, --prefix, --kind required when not using --csv", file=sys.stderr)
            sys.exit(1)
        if args.kind == "unknown":
            print("Error: Cannot promote unknown kind", file=sys.stderr)
            sys.exit(1)
        entry = {
            "device_label": args.device_label,
            "prefix": args.prefix,
            "canonical_point": args.point,
            "kind": args.kind,
            "direction": args.direction,
            "status": args.status,
            "evidence": args.evidence
        }
        if args.write_code: entry["write_code"] = args.write_code
        if args.report_code: entry["report_code"] = args.report_code
        if args.ack_code: entry["ack_code"] = args.ack_code
        entries_to_add.append(entry)

    for new_e in entries_to_add:
        duplicate_idx = -1
        for i, existing in enumerate(mappings):
            if existing.get("canonical_point") == new_e["canonical_point"] and existing.get("prefix") == new_e["prefix"] and existing.get("device_label") == new_e["device_label"]:
                duplicate_idx = i
                break
        
        if duplicate_idx >= 0:
            if args.replace:
                mappings[duplicate_idx].update(new_e)
                print(f"Replaced existing entry for {new_e['canonical_point']}")
            else:
                print(f"Error: Duplicate entry for {new_e['canonical_point']}. Use --replace to overwrite.", file=sys.stderr)
                sys.exit(1)
        else:
            mappings.append(new_e)
            print(f"Appended entry for {new_e['canonical_point']}")

    with open(args.pointmap, "w") as f:
        yaml.dump(data, f, sort_keys=False, default_flow_style=False)
        
if __name__ == "__main__":
    main()
