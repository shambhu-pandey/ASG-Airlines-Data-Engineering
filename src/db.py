"""Small configuration-driven SQLAlchemy helper for local MySQL."""

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import SQLAlchemyError

try:
	from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is listed in requirements.txt
	load_dotenv = None


CONFIG_ENV_PATH = Path(__file__).resolve().parent.parent / "config" / ".env"


def get_engine() -> Engine:
	"""Create a MySQL engine using environment-based connection settings."""
	if load_dotenv is not None:
		load_dotenv(dotenv_path=CONFIG_ENV_PATH)

	extra_params = os.getenv("DB_EXTRA_PARAMS", "")
	query = {
		item.split("=", 1)[0]: item.split("=", 1)[1]
		for item in extra_params.split("&")
		if "=" in item and item.split("=", 1)[0]
	}

	url = URL.create(
		drivername="mysql+pymysql",
		username=os.getenv("DB_USER", "root"),
		password=os.getenv("DB_PASSWORD", ""),
		host=os.getenv("DB_HOST", "127.0.0.1"),
		port=int(os.getenv("DB_PORT", "3306")),
		database=os.getenv("DB_NAME", "asg_airlines"),
		query=query,
	)

	return create_engine(url, pool_pre_ping=True, pool_recycle=1800)


def test_connection(engine: Optional[Engine] = None) -> bool:
	"""Return True when a simple database connectivity check succeeds."""
	connection_engine = engine or get_engine()
	try:
		with connection_engine.connect() as connection:
			connection.execute(text("SELECT 1"))
	except SQLAlchemyError as error:
		raise RuntimeError("Unable to connect to the configured MySQL database.") from error
	return True


if __name__ == "__main__":
	print(test_connection())
