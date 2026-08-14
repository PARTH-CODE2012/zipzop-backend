"""Write the OpenAPI schema to a file.

`openapi.json` is committed at the repository root and the frontend generates
its TypeScript types from it. CI fails when it is stale — that check is the one
thing keeping both sides of the contract honest.

    python -m app.scripts.dump_openapi ../openapi.json
"""

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m app.scripts.dump_openapi <output.json>", file=sys.stderr)
        return 2

    # Imported lazily: this script must work without a database or Redis
    # reachable, so CI can check the contract without standing up services.
    from app.main import create_app

    schema = create_app().openapi()

    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys so the file is stable across runs — otherwise the freshness
    # check fails on key ordering rather than on a real change.
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(schema.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
