import os

# Keeps the test run from creating a logs/ directory in the repo; tests that need files set their own dir.
os.environ["LOG_DIR"] = ""
os.environ["FACE_ENROLL_MIN_IMAGES"] = "3"
os.environ["FACE_ENROLL_MAX_IMAGES"] = "10"
