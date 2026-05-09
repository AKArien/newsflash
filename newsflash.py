import logging
from src.daemon import newsflash

logger = logging.getlogger(__name__)

def main() -> None:
    logging.basicconfig(level=logging.info, format="%(levelname)s: %(message)s")
    newsflash().run()

if __name__ == "__main__":
    main()
