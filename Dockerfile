FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y curl ffmpeg libgl1 libglib2.0-0 libsm6 libxrender1 libxext6 && apt-get clean

# Install uv
RUN pip install uv

# Install dotenvx
RUN curl -sfS https://dotenvx.sh/install.sh | sh

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Expose port
EXPOSE 8000

# Run the application
CMD ["dotenvx", "run", "-f", ".env.staging", "--", "uv", "run", "daphne", "-b", "0.0.0.0", "-p", "8000", "ares.asgi:application"]
