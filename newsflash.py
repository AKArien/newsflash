import logging
from src.daemon import newsflash

logger = logging.getLogger(__name__)

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    newsflash().run()

if __name__ == "__main__":
    main()
