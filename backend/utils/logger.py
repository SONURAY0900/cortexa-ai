from datetime import datetime


def log_agent(
    agent: str,
    message: str
):
    """
    Print a formatted agent execution log.
    """

    timestamp = datetime.now().strftime(
        "%H:%M:%S"
    )

    print(
        f"[{timestamp}] "
        f"[{agent}] "
        f"{message}"
    )