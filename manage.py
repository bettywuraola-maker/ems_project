"""Command line helper:  python manage.py init   |   python manage.py demo   |   python manage.py run"""
import sys

from ems import create_app
from ems.seed import demo_data, init_system

app = create_app()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    with app.app_context():
        if cmd == "init":
            init_system()
            print("Database created. Admin login: admin@ui.edu.ng / ChangeMe123 (change it after first login).")
        elif cmd == "demo":
            demo_data()
            print("Demo data loaded.")
        else:
            app.run(debug=True)
