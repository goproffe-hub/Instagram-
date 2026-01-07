FROM python:3.9-slim

# Install ffmpeg (Essential for merging video+audio)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Create downloads folder with write permissions
RUN mkdir -p downloads && chmod 777 downloads

CMD ["python", "main.py"]

