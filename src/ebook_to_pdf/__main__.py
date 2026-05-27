from __future__ import annotations

import sys


def main() -> int:
    try:
        from ebook_to_pdf.app import run
    except ModuleNotFoundError as exc:
        missing = exc.name or "required package"
        print(
            f"Missing dependency: {missing}\n"
            "Install the app dependencies with: pip install -e .",
            file=sys.stderr,
        )
        return 1

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
