import time

from utils.logger import log_agent


def retry_tool(
    tool_function,
    query: str,
    agent_name: str,
    max_retries: int = 2,
    delay: float = 1.0
):
    """
    Execute a tool with retry support
    and execution logging.
    """

    total_attempts = max_retries + 1

    last_error = None

    log_agent(
        agent_name,
        f"Starting tool execution"
    )

    for attempt in range(
        1,
        total_attempts + 1
    ):

        log_agent(
            agent_name,
            f"Attempt {attempt}/{total_attempts}"
        )

        try:

            result = tool_function(
                query
            )


            # =============================================
            # DICTIONARY RESULT
            # =============================================

            if isinstance(
                result,
                dict
            ):

                content = result.get(
                    "content",
                    ""
                )

                error = result.get(
                    "error",
                    ""
                )


                if (
                    content
                    and not error
                ):

                    log_agent(
                        agent_name,
                        "Tool execution successful"
                    )

                    return result


                last_error = (
                    error
                    or
                    "Tool returned empty result."
                )


            # =============================================
            # STRING RESULT
            # =============================================

            else:

                if result:

                    log_agent(
                        agent_name,
                        "Tool execution successful"
                    )

                    return {
                        "content": str(result),
                        "sources": [],
                        "error": ""
                    }


                last_error = (
                    "Tool returned empty result."
                )


        except Exception as e:

            last_error = str(e)

            log_agent(
                agent_name,
                f"Attempt failed: {last_error}"
            )


        # =============================================
        # RETRY
        # =============================================

        if attempt < total_attempts:

            log_agent(
                agent_name,
                f"Retrying in {delay} second(s)..."
            )

            time.sleep(
                delay
            )


    # =============================================
    # ALL ATTEMPTS FAILED
    # =============================================

    log_agent(
        agent_name,
        "All retry attempts failed"
    )


    return {
        "content": "",
        "sources": [],
        "error": (
            f"{agent_name} failed after "
            f"{total_attempts} attempts. "
            f"Last error: {last_error}"
        )
    }