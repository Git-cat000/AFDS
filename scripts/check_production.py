"""Run the same registered production checks against a local APR JSON file."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.schemas import APR
from app.skill_checks import check_production
from app.skill_schema import STAGES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apr", type=Path)
    parser.add_argument("--stage", choices=list(STAGES), required=True)
    arguments = parser.parse_args()
    apr = APR.model_validate_json(arguments.apr.read_bytes()).model_dump(mode="json")
    print(
        json.dumps(check_production(apr, arguments.stage), ensure_ascii=True, indent=2)
    )


if __name__ == "__main__":
    main()
