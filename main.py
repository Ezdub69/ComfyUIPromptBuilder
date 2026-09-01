import sys

from PySide6.QtWidgets import QApplication

from app import db
from app.main_window import MainWindow


def run():
    conn = db.get_connection()
    db.initialize_db(conn)

    app = QApplication(sys.argv)
    window = MainWindow(conn)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
