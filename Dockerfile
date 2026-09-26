FROM python:3.11-slim

WORKDIR /app

# System deps needed by kaleido (headless chromium-based renderer) for
# offline Plotly -> PNG export.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libnss3 \
    libx11-6 \
    libxcomposite1 \
    libxrandr2 \
    libxi6 \
    libxtst6 \
    libatk-bridge2.0-0 \
    libgbm1 \
    libasound2 \
    libcups2 \
    libdrm2 \
    libxdamage1 \
    libxfixes3 \
    libxshmfence1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
