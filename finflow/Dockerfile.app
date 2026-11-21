FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .

# CPU 전용 PyTorch
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch

RUN pip install --no-cache-dir \
    -r requirements.txt

COPY app_ui.py .
COPY src ./src
COPY scripts ./scripts
COPY utils ./utils
COPY styles ./styles
COPY data/chroma ./data/chroma

CMD ["streamlit", "run", "app_ui.py", \
     "--server.port=8080", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]