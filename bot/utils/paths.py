"""Centralized path resolution for NOXbot.

This module provides a single source of truth for project paths, ensuring
consistent path resolution across the entire application regardless of the
current working directory.

Usage:
    from bot.utils.paths import get_project_root, get_database_path
    
    project_root = get_project_root()
    db_path = get_database_path()
"""

from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def get_project_root() -> Path:
    """Get the absolute path to the project root directory.
    
    The project root is determined by traversing up from this file until
    we find the directory containing main.py.
    
    Returns:
        Path: Absolute path to the project root directory.
        
    Example:
        >>> root = get_project_root()
        >>> assert (root / "main.py").exists()
    """
    # This file is at: <project_root>/bot/utils/paths.py
    # So project root is 2 levels up
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent
    
    # Verify we found the correct directory
    if not (project_root / "main.py").exists():
        raise RuntimeError(
            f"Failed to locate project root. Expected main.py at {project_root}"
        )
    
    return project_root


@lru_cache(maxsize=1)
def get_database_path(db_name: str = "noxbot.db") -> Path:
    """Get the absolute path to the database file.
    
    By default, the database is stored in the project root directory.
    
    Args:
        db_name: Name of the database file (default: "noxbot.db")
        
    Returns:
        Path: Absolute path to the database file.
        
    Example:
        >>> db_path = get_database_path()
        >>> print(db_path)
        I:/python/NOXbott/noxbot.db
    """
    return get_project_root() / db_name


def ensure_database_directory() -> Path:
    """Ensure the database directory exists and return the database path.
    
    This function should be called during application initialization to ensure
    the parent directory of the database file exists before any database
    operations are attempted.
    
    Returns:
        Path: Absolute path to the database file.
        
    Example:
        >>> db_path = ensure_database_directory()
        >>> assert db_path.parent.exists()
    """
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_logs_directory() -> Path:
    """Get the absolute path to the logs directory.
    
    Returns:
        Path: Absolute path to the logs directory.
    """
    logs_dir = get_project_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


if __name__ == "__main__":
    # Self-test
    print("Project Root:", get_project_root())
    print("Database Path:", get_database_path())
    print("Database Directory Exists:", get_database_path().parent.exists())
    
    db_path = ensure_database_directory()
    print("After ensure_database_directory():", db_path)
    print("Parent Directory Exists:", db_path.parent.exists())
