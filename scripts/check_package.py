"""Check the public archive for excluded files and common credential patterns."""

import argparse
import json
import io
import re
import zipfile
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    problems = []
    project = dotenv_values(ROOT / ".env").get("GOOGLE_CLOUD_PROJECT")
    patterns = [
        rb"AIza[0-9A-Za-z_-]{30,}",
        rb"ya29\.[0-9A-Za-z_-]{20,}",
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    ]
    with zipfile.ZipFile(args.archive) as z:
        for name in z.namelist():
            parts = Path(name).parts
            if any(
                p in [".env", ".venv", ".adk", "validation", "__pycache__"]
                for p in parts
            ):
                problems.append(f"Excluded path: {name}")
            body = z.read(name)
            if name.endswith(".docx"):
                with zipfile.ZipFile(io.BytesIO(body)) as doc:
                    body = b"\n".join(doc.read(n) for n in doc.namelist() if n.endswith(".xml"))
            if any(re.search(pattern, body) for pattern in patterns):
                problems.append(f"Credential pattern: {name}")
            if project and project.encode() in body:
                problems.append(f"Local project identifier: {name}")
            if str(ROOT).encode() in body:
                problems.append(f"Local workspace prefix: {name}")
            if str(Path.home()).encode() in body:
                problems.append(f"Local home directory prefix: {name}")
    print(json.dumps({"passed": not problems, "problems": problems}, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
