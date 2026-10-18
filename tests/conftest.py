import os
import tempfile

# Must run before app.config is imported anywhere.
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="scriptorium-test-")
os.environ.pop("DATABASE_URL", None)
if os.environ.get("TEST_DATABASE_URL"):  # e.g. postgres://user:pw@localhost/db
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)
