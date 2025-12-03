'''
Database utilities for the project.
'''

import sqlite3
from typing import List, Dict, Any, Optional
from .exceptions import ProjectError


class Database:
    """
    A simple database wrapper for SQLite.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """
        Initialize the database and create tables if they don't exist.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL
                )
            ''')

    def insert_user(self, name: str, email: str) -> int:
        """
        Insert a new user into the database.

        Args:
            name (str): The user's name.
            email (str): The user's email.

        Returns:
            int: The ID of the inserted user.

        Raises:
            ProjectError: If the insertion fails.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "INSERT INTO users (name, email) VALUES (?, ?)",
                    (name, email)
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ProjectError(f"User with email {email} already exists.")
        except sqlite3.Error as e:
            raise ProjectError(f"Database error: {e}")

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve a user by ID.

        Args:
            user_id (int): The user's ID.

        Returns:
            Optional[Dict[str, Any]]: The user data or None if not found.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        """
        Retrieve all users from the database.

        Returns:
            List[Dict[str, Any]]: A list of user data.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM users")
            return [dict(row) for row in cursor.fetchall()]

    def update_user(self, user_id: int, name: Optional[str] = None, email: Optional[str] = None) -> bool:
        """
        Update a user's information.

        Args:
            user_id (int): The user's ID.
            name (Optional[str]): The new name.
            email (Optional[str]): The new email.

        Returns:
            bool: True if the update was successful.
        """
        if not name and not email:
            return False

        updates = []
        params = []

        if name:
            updates.append("name = ?")
            params.append(name)
        if email:
            updates.append("email = ?")
            params.append(email)

        params.append(user_id)

        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(query, params)
                return conn.total_changes > 0
        except sqlite3.IntegrityError:
            raise ProjectError(f"User with email {email} already exists.")
        except sqlite3.Error as e:
            raise ProjectError(f"Database error: {e}")

    def delete_user(self, user_id: int) -> bool:
        """
        Delete a user from the database.

        Args:
            user_id (int): The user's ID.

        Returns:
            bool: True if the deletion was successful.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return conn.total_changes > 0