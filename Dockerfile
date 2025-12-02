# Use Python 3.11 slim so Pillow wheels/build behave predictably
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system deps needed for Pillow / reportlab
RUN apt-get update && apt-get install -y \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libopenjp2-7-dev \
    libtiff5-dev \
    libwebp-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .

# Upgrade pip and install Python deps
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt

# Copy rest of the app
COPY . .

# Make generated folder and signatures dir (just in case)
RUN mkdir -p generated static/signatures

# Expose port and run with gunicorn
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app", "--workers", "2"]
