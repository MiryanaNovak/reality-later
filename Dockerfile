FROM python:3.13-slim

WORKDIR /app

# Install the application and its dependencies.
COPY pyproject.toml ./
COPY rpapp/ ./rpapp/
COPY main.py ./

RUN pip install --no-cache-dir .

# Run as an unprivileged user.
RUN useradd --system --create-home --uid 10001 appuser \
    && mkdir -p /app/data /app/certificates \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8090

CMD ["python", "main.py", "--no-browser", "--no-tray"]
