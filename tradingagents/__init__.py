"""TradingAgents: multi-agent LLM financial trading framework."""

from tradingagents.secret_redaction import install_secret_safe_logging

# Redact at LogRecord creation rather than at one provider or one handler. This
# also protects retry/third-party logging and exception tracebacks before they
# reach a console, file, test capture, or Web server handler.
install_secret_safe_logging()

# Load .env files at package import so DEFAULT_CONFIG's env-var overlay
# (and every llm_clients consumer) sees the user's keys regardless of
# which entry point started the process. find_dotenv(usecwd=True) walks
# from the CWD, so the installed `tradingagents` console script picks up
# the project's .env instead of stepping up from site-packages.
# load_dotenv defaults to override=False, so it never clobbers values
# the caller has already exported.
__version__ = "0.5.2"

from dotenv import find_dotenv, load_dotenv  # noqa: E402 - logging must be redacted first

# Load .env at package import so DEFAULT_CONFIG's env-var overlay and every LLM
# client see the user's keys whichever entry point started the process.
# usecwd=True walks from the working directory, so the installed console script
# finds the project's .env rather than looking beside site-packages. Values the
# caller has already exported are never overridden.
load_dotenv(find_dotenv(usecwd=True))
load_dotenv(find_dotenv(".env.enterprise", usecwd=True), override=False)
