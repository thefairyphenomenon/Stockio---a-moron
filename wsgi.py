import database as db
import app as application

# Initialize database on startup
db.init_db()
print("Database initialized.")
print("Default admin: admin@stockhub.com / admin123")
print("Change the admin password immediately after first login.")

app = application.app
