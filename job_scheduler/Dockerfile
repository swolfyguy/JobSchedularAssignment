FROM python:3.10-slim

# Prevent Python from writing .pyc files to disc and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install daphne

# Copy project code
COPY . /app/

# Collect static files (uncomment if needed for production)
RUN python manage.py collectstatic --noinput

# Expose the application port
EXPOSE 8000

# Default command: run the ASGI server using Daphne
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "job_scheduler.asgi:application"]