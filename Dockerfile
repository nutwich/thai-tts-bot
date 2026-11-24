# Container for free hosts such as Render (Linux). Windows users keep using start.bat.
FROM python:3.12-slim

# discord.py bundles the Opus voice library only on Windows
RUN apt-get update \
 && apt-get install -y --no-install-recommends libopus0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
