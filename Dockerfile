FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Install gunicorn
RUN pip install gunicorn

EXPOSE 8080
# Run app with 3 workers, binding 0.0.0.0:8080
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8080", "app:app"]

