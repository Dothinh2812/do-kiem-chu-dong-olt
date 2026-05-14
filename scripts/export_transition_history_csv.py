import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transition_history_export import (
    export_transition_history_csv,
    export_transition_summary_csv,
)


def main():
    parser = argparse.ArgumentParser(description="Export filtered transition history to CSV.")
    parser.add_argument("--measurement-db", default="onu_measurements.db")
    parser.add_argument("--source-db", default="database.db")
    parser.add_argument("--output", default="runtime/transition_history_export.csv")
    parser.add_argument("--summary-output", default="runtime/transition_history_summary.csv")
    args = parser.parse_args()

    row_count = export_transition_history_csv(args.measurement_db, args.source_db, args.output)
    summary_count = export_transition_summary_csv(
        args.measurement_db,
        args.source_db,
        args.summary_output,
    )
    print(f"Wrote {row_count} detail rows to {Path(args.output).resolve()}")
    print(f"Wrote {summary_count} summary rows to {Path(args.summary_output).resolve()}")


if __name__ == "__main__":
    main()
