"""Import source-bound research or change priority while retaining revisions."""
import argparse
from pathlib import Path

from agents.assess.reviewed_cases import ReviewedCases, load_cases, save_cases
from tools.assess_refresh import refresh_current_assess_run_if_active


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client", required=True)
    ap.add_argument("--input", type=Path)
    ap.add_argument("--notice-id")
    ap.add_argument("--disposition", choices=["deprioritized", "rejected"])
    ap.add_argument("--reason")
    args = ap.parse_args(argv)
    review = Path(__file__).resolve().parents[1] / "data/review"
    if args.input:
        book = ReviewedCases.model_validate_json(args.input.read_bytes())
        if book.client_name.casefold() != args.client.casefold():
            ap.error("input belongs to a different client")
    else:
        if not all((args.notice_id, args.disposition, args.reason)):
            ap.error("supply --input, or --notice-id --disposition --reason")
        book = load_cases(args.client, review)
        if not any(c.record.notice_id == args.notice_id for c in book.cases):
            ap.error("notice is not in the current research input")
        from datetime import datetime, timezone
        cases = []
        for case in book.cases:
            if case.record.notice_id == args.notice_id:
                research = case.research.model_copy(update={
                    "status": args.disposition, "priority": False, "rationale": args.reason,
                    "reviewed_by": "operator disposition", "reviewed_at": datetime.now(timezone.utc)})
                case = case.model_copy(update={"research": research})
            cases.append(case)
        book = book.model_copy(update={"cases": tuple(cases)})
    print(save_cases(book, review))
    result = refresh_current_assess_run_if_active(args.client, review_dir=review)
    print(result)
    return 2 if result.startswith("FAILED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
