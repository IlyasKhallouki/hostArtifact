import json
import logging


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {"severity": record.levelname, "message": record.getMessage()}
        )


def configure():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    logging.getLogger("publisher").handlers = [handler]
    logging.getLogger("publisher").setLevel(logging.INFO)
    # HTTP/OAuth libraries can log URLs, headers or bodies; do not expose these in production.
    for name in ("httpx", "httpcore", "fastmcp", "mcp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
