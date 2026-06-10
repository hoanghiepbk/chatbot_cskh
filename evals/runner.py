"""Eval runner stub — implemented in TIP-009."""

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description="XeCare eval runner")
    parser.add_argument(
        "--suite",
        choices=["golden", "ragas", "adversarial_critical", "adversarial_quality", "smoke", "full"],
        default="smoke",
    )
    args = parser.parse_args()
    print(f"eval runner: suite '{args.suite}' not implemented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
