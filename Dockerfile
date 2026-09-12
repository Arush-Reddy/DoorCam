FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    ENABLE_REMOTE_TUNNEL=false \
    CMAKE_BUILD_PARALLEL_LEVEL=1 \
    DLIB_NO_GUI_SUPPORT=1 \
    DLIB_USE_CUDA=0 \
    USE_AVX_INSTRUCTIONS=0

# Install OS libraries for OpenCV, dlib and face_recognition
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements with single-threaded compilation to stay under 400MB RAM
COPY requirements_hf.txt requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir numpy && \
    CMAKE_BUILD_PARALLEL_LEVEL=1 pip install --no-cache-dir -r requirements.txt

# Copy all application code, known faces, database and static assets
COPY . .

EXPOSE 10000 5000 7860

CMD ["python", "doorbell_server.py"]
