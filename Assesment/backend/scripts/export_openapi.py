"""Write the OpenAPI schema of the API to a file, for frontend type generation.

    uv run python scripts/export_openapi.py [output-file]

The default output is ``openapi.json`` in the backend directory, which is where
``npm run gen:api`` in the frontend reads it. No database is needed.
"""

import json
import sys
from pathlib import Path

from ndm.api.app import create_app
from ndm.core.settings import Settings

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    schema = create_app(Settings()).openapi()
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
