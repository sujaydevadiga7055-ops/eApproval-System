import os

class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    
    # Folder for static assets like CSS, JS, and signature images
    STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
    
    # Folder for generated PDFs
    GENERATED_FOLDER = os.path.join(BASE_DIR, 'generated')
    
    # Secret key for sessions
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-very-hard-to-guess-secret-key'
    
    # Database configuration
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Ensure generated folder exists
    os.makedirs(GENERATED_FOLDER, exist_ok=True)
    os.makedirs(os.path.join(STATIC_FOLDER, 'signatures'), exist_ok=True)
