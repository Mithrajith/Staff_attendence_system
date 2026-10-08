import os

# Keeps the test run from creating a logs/ directory in the repo; tests that need files set their own dir.
os.environ.setdefault("LOG_DIR", "")
