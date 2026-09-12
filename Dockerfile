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
    git \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Install base python tools & numpy
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir numpy

# 2. Build dlib from source explicitly WITHOUT AVX instructions to ensure 100% compatibility on cloud microVMs
RUN git clone --depth 1 https://github.com/davisking/dlib.git /tmp/dlib && \
    sed -i 's/set(AVX_IS_AVAILABLE_ON_HOST 1)/set(AVX_IS_AVAILABLE_ON_HOST 0)/g' /tmp/dlib/dlib/cmake_utils/check_if_avx_instructions_executable_on_host.cmake && \
    sed -i 's/set(USE_AVX_INSTRUCTIONS ON/set(USE_AVX_INSTRUCTIONS OFF/g' /tmp/dlib/dlib/cmake_utils/set_compiler_specific_options.cmake && \
    CMAKE_BUILD_PARALLEL_LEVEL=1 pip install --no-cache-dir /tmp/dlib && \
    rm -rf /tmp/dlib

# 3. Install remaining Python requirements and official face models from git
COPY requirements_hf.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir git+https://github.com/ageitgey/face_recognition_models.git

# Copy all application code, known faces, database and static assets
COPY . .

EXPOSE 10000 5000 7860

CMD ["python", "doorbell_server.py"]
