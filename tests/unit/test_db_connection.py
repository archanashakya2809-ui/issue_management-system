
from sqlalchemy import text
from app.db.session import get_db


def test_connection():
    db_generator = get_db()
    db = next(db_generator)

    try:
        result = db.execute(text("SELECT 1"))
        print("Database connection successful:", result.scalar_one())

        result = db.execute(
            text("SELECT current_user, current_database()")
        )
        role, database = result.one()

        print("Database role:", role)
        print("Database name:", database)

    finally:
        db.close()
        db_generator.close()


if __name__ == "__main__":
    test_connection()
