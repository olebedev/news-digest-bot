import logging
import os
import shutil
from pathlib import Path

from sources import SOURCES


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )

    public_dir = Path("public")
    if public_dir.exists():
        shutil.rmtree(public_dir)
    public_dir.mkdir(parents=True, exist_ok=True)

    base_url = os.environ.get("FEED_BASE_URL", "").rstrip("/")

    for source in SOURCES:
        feed_base_url = f"{base_url}/{source.SLUG}" if base_url else ""
        result = source.run(feed_base_url=feed_base_url)
        dest_dir = public_dir / source.SLUG
        dest_dir.mkdir(parents=True, exist_ok=True)
        for path in result["files"]:
            path = Path(path)
            if path.suffix.lower() != ".xml":
                continue
            shutil.copy2(path, dest_dir / path.name)
        logging.info("Published %s feeds to %s", source.SLUG, dest_dir)


if __name__ == "__main__":
    main()
