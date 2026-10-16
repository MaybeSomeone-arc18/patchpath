FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-mcp.txt

COPY . .

CMD ["python", "-m", "pytest", "-q"]
