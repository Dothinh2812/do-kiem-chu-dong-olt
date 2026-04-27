import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cts_port_status_scan import login_and_scan_all_olt_port_statuses


def main():
    parser = argparse.ArgumentParser(
        description="Login CTS, save session snapshot, scan all OLT port statuses, and export JSON/CSV."
    )
    parser.add_argument("--output-dir", default="runtime")
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Open visible browser window during CTS login.",
    )
    args = parser.parse_args()

    rows, session_path, json_path, csv_path = login_and_scan_all_olt_port_statuses(
        output_dir=args.output_dir,
        headless=not args.headful,
    )
    print(f"Scanned {len(rows)} port status rows")
    print(f"Session: {Path(session_path).resolve()}")
    print(f"JSON: {Path(json_path).resolve()}")
    print(f"CSV: {Path(csv_path).resolve()}")


if __name__ == "__main__":
    main()
